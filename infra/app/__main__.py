# Copyright (c) Microsoft. All rights reserved.

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pulumi
from pulumi_azure_native import app
from pulumi_azure_native import containerregistry
from pulumi_azure_native import operationalinsights
from pulumi_azure_native import resources
from pulumi_azure_native.containerregistry import outputs as acr_outputs
from pulumi_command import local

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CONTAINER_PORT: Final = 8000
ACR_PASSWORD_SECRET_NAME: Final = "acr-password"
MODEL_API_KEY_SECRET_NAME: Final = "fde-model-api-key"
IMAGE_SOURCE_PATHS: Final = (
    REPO_ROOT / "Dockerfile",
    REPO_ROOT / "py" / "pyproject.toml",
    REPO_ROOT / "py" / "uv.lock",
    REPO_ROOT / "py" / "common",
    REPO_ROOT / "py" / "apps" / "sample",
)


def _slug(value: str, *, max_length: int) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    trimmed = cleaned[:max_length].strip("-")
    return trimmed or "fdebench"


def _acr_name(configured_name: str | None, resource_prefix: str) -> str:
    if configured_name is not None:
        normalized = configured_name.lower()
        if not re.fullmatch(r"[a-z0-9]{5,50}", normalized):
            msg = "containerRegistryName must be 5-50 lowercase alphanumeric characters"
            raise ValueError(msg)
        return normalized

    base = re.sub(r"[^a-z0-9]", "", resource_prefix.lower())
    suffix = hashlib.sha1(f"{pulumi.get_project()}:{pulumi.get_stack()}:{resource_prefix}".encode()).hexdigest()[:8]
    name = f"{base[:42]}{suffix}"
    if len(name) < 5:
        name = f"fde{suffix}"
    return name[:50]


def _config_int(config: pulumi.Config, key: str, default: int, *, minimum: int) -> int:
    value = config.get_int(key)
    result = default if value is None else value
    if result < minimum:
        msg = f"{key} must be at least {minimum}"
        raise ValueError(msg)
    return result


def _config_float(config: pulumi.Config, key: str, default: float, *, minimum: float) -> float:
    raw_value = config.get(key)
    if raw_value is None:
        return default
    try:
        result = float(raw_value)
    except ValueError as exc:
        msg = f"{key} must be a number"
        raise ValueError(msg) from exc
    if result < minimum:
        msg = f"{key} must be at least {minimum}"
        raise ValueError(msg)
    return result


def _source_digest(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for source_path in paths:
        files = (
            [source_path]
            if source_path.is_file()
            else sorted(path for path in source_path.rglob("*") if path.is_file())
        )
        for file_path in files:
            if "__pycache__" in file_path.parts or file_path.suffix == ".pyc":
                continue
            digest.update(str(file_path.relative_to(REPO_ROOT)).replace("\\", "/").encode())
            digest.update(file_path.read_bytes())
    return digest.hexdigest()[:12]


def _primary_acr_password(passwords: Sequence[acr_outputs.RegistryPasswordResponse] | None) -> str:
    if not passwords or passwords[0].value is None:
        msg = "Azure Container Registry did not return an admin password"
        raise ValueError(msg)
    return passwords[0].value


config = pulumi.Config()
fde_config = pulumi.Config("fde")
azure_config = pulumi.Config("azure-native")

location = azure_config.get("location") or config.get("location") or "westus2"
resource_prefix = _slug(config.get("resourcePrefix") or f"fdebench-{pulumi.get_stack()}", max_length=24)
container_app_name = _slug(config.get("containerAppName") or f"{resource_prefix}-api", max_length=32)
environment_name = _slug(config.get("environmentName") or f"{resource_prefix}-env", max_length=32)
workspace_name = _slug(config.get("logWorkspaceName") or f"{resource_prefix}-logs", max_length=63)
registry_name = _acr_name(config.get("containerRegistryName"), resource_prefix)
image_repository = config.get("imageRepository") or "fdebench-api"
image_tag = config.get("imageTag") or f"src-{_source_digest(IMAGE_SOURCE_PATHS)}"
docker_platform = config.get("dockerPlatform") or "linux/amd64"

min_replicas = _config_int(config, "minReplicas", 1, minimum=0)
max_replicas = _config_int(config, "maxReplicas", 1, minimum=1)
if max_replicas < min_replicas:
    raise ValueError("maxReplicas must be greater than or equal to minReplicas")

cpu = _config_float(config, "cpu", 1.0, minimum=0.25)
memory = config.get("memory") or "2Gi"
model_concurrency = _config_int(fde_config, "modelConcurrency", 2, minimum=1)
max_retry_attempts = _config_int(fde_config, "maxRetryAttempts", 3, minimum=1)
http_timeout_seconds = _config_float(fde_config, "httpTimeoutSeconds", 45.0, minimum=1.0)
retry_base_delay_seconds = _config_float(fde_config, "retryBaseDelaySeconds", 1.0, minimum=0.0)

tags: dict[str, str] = {
    "app": "fdebench-api",
    "pulumi-project": pulumi.get_project(),
    "pulumi-stack": pulumi.get_stack(),
}

resource_group = resources.ResourceGroup(
    "resource-group",
    resource_group_name=f"{resource_prefix}-rg",
    location=location,
    tags=tags,
)

registry = containerregistry.Registry(
    "registry",
    registry_name=registry_name,
    resource_group_name=resource_group.name,
    location=resource_group.location,
    sku=containerregistry.SkuArgs(name=containerregistry.SkuName.BASIC),
    admin_user_enabled=True,
    tags=tags,
)

registry_credentials = containerregistry.list_registry_credentials_output(
    resource_group_name=resource_group.name,
    registry_name=registry.name,
)
registry_username = registry_credentials.username
registry_password = registry_credentials.passwords.apply(_primary_acr_password)

full_image_name = pulumi.Output.concat(registry.login_server, "/", image_repository, ":", image_tag)
image_build_command = pulumi.Output.concat(
    "az acr build --registry ",
    registry.name,
    " --image ",
    image_repository,
    ":",
    image_tag,
    " --file Dockerfile --platform ",
    docker_platform,
    " .",
)
image_build = local.Command(
    "api-image-build",
    create=image_build_command,
    update=image_build_command,
    dir=str(REPO_ROOT),
    triggers=[image_tag],
    opts=pulumi.ResourceOptions(depends_on=[registry]),
)

workspace = operationalinsights.Workspace(
    "logs",
    workspace_name=workspace_name,
    resource_group_name=resource_group.name,
    location=resource_group.location,
    retention_in_days=30,
    sku=operationalinsights.WorkspaceSkuArgs(name=operationalinsights.WorkspaceSkuNameEnum.PER_GB2018),
    tags=tags,
)
workspace_keys = operationalinsights.get_shared_keys_output(
    resource_group_name=resource_group.name,
    workspace_name=workspace.name,
)

environment = app.ManagedEnvironment(
    "container-app-environment",
    environment_name=environment_name,
    resource_group_name=resource_group.name,
    location=resource_group.location,
    app_logs_configuration=app.AppLogsConfigurationArgs(
        destination="log-analytics",
        log_analytics_configuration=app.LogAnalyticsConfigurationArgs(
            customer_id=workspace.customer_id,
            shared_key=workspace_keys.primary_shared_key,
        ),
    ),
    tags=tags,
)

secrets: list[app.SecretArgs] = [
    app.SecretArgs(name=ACR_PASSWORD_SECRET_NAME, value=registry_password),
]

model_api_key = fde_config.get_secret("modelApiKey")
if model_api_key is not None:
    secrets.append(app.SecretArgs(name=MODEL_API_KEY_SECRET_NAME, value=model_api_key))

env_vars: list[app.EnvironmentVarArgs] = [
    app.EnvironmentVarArgs(name="FDE_SERVICE_NAME", value=fde_config.get("serviceName") or "FDEBench API"),
    app.EnvironmentVarArgs(name="FDE_MODEL_BASE_URL", value=fde_config.get("modelBaseUrl") or ""),
    app.EnvironmentVarArgs(name="FDE_DEFAULT_MODEL_NAME", value=fde_config.get("defaultModelName") or "gpt-4.1-mini"),
    app.EnvironmentVarArgs(name="FDE_MODEL_API_STYLE", value=fde_config.get("modelApiStyle") or "auto"),
    app.EnvironmentVarArgs(
        name="FDE_MODEL_MAX_TOKENS", value=str(_config_int(fde_config, "modelMaxTokens", 1024, minimum=1))
    ),
    app.EnvironmentVarArgs(name="FDE_MODEL_CONCURRENCY", value=str(model_concurrency)),
    app.EnvironmentVarArgs(name="FDE_MAX_RETRY_ATTEMPTS", value=str(max_retry_attempts)),
    app.EnvironmentVarArgs(name="FDE_HTTP_TIMEOUT_SECONDS", value=str(http_timeout_seconds)),
    app.EnvironmentVarArgs(
        name="FDE_RETRY_BASE_DELAY_SECONDS",
        value=str(retry_base_delay_seconds),
    ),
    app.EnvironmentVarArgs(name="FDE_EXTRACT_IMAGE_DETAIL", value=fde_config.get("extractImageDetail") or "high"),
    app.EnvironmentVarArgs(name="FDE_EXTRACT_IMAGE_FORMAT", value=fde_config.get("extractImageFormat") or "png"),
    app.EnvironmentVarArgs(
        name="FDE_EXTRACT_JPEG_QUALITY",
        value=str(_config_int(fde_config, "extractJpegQuality", 90, minimum=1)),
    ),
    app.EnvironmentVarArgs(
        name="FDE_EXTRACT_IMAGE_MAX_DIMENSION",
        value=str(_config_int(fde_config, "extractImageMaxDimension", 3072, minimum=512)),
    ),
]

for config_key, env_name in (
    ("triageModelName", "FDE_TRIAGE_MODEL_NAME"),
    ("extractModelName", "FDE_EXTRACT_MODEL_NAME"),
    ("orchestrateModelName", "FDE_ORCHESTRATE_MODEL_NAME"),
):
    value = fde_config.get(config_key)
    if value:
        env_vars.append(app.EnvironmentVarArgs(name=env_name, value=value))

if model_api_key is not None:
    env_vars.append(app.EnvironmentVarArgs(name="FDE_MODEL_API_KEY", secret_ref=MODEL_API_KEY_SECRET_NAME))

container_app = app.ContainerApp(
    "container-app",
    container_app_name=container_app_name,
    resource_group_name=resource_group.name,
    location=resource_group.location,
    environment_id=environment.id,
    configuration=app.ConfigurationArgs(
        active_revisions_mode=app.ActiveRevisionsMode.SINGLE,
        ingress=app.IngressArgs(
            external=True,
            target_port=CONTAINER_PORT,
            allow_insecure=False,
            transport=app.IngressTransportMethod.AUTO,
            traffic=[app.TrafficWeightArgs(latest_revision=True, weight=100)],
        ),
        registries=[
            app.RegistryCredentialsArgs(
                server=registry.login_server,
                username=registry_username,
                password_secret_ref=ACR_PASSWORD_SECRET_NAME,
            ),
        ],
        secrets=secrets,
    ),
    template=app.TemplateArgs(
        containers=[
            app.ContainerArgs(
                name="api",
                image=full_image_name,
                env=env_vars,
                resources=app.ContainerResourcesArgs(cpu=cpu, memory=memory),
                probes=[
                    app.ContainerAppProbeArgs(
                        type=app.Type.STARTUP,
                        http_get=app.ContainerAppProbeHttpGetArgs(
                            path="/health",
                            port=CONTAINER_PORT,
                            scheme=app.Scheme.HTTP,
                        ),
                        initial_delay_seconds=5,
                        period_seconds=10,
                        timeout_seconds=3,
                        failure_threshold=12,
                    ),
                    app.ContainerAppProbeArgs(
                        type=app.Type.LIVENESS,
                        http_get=app.ContainerAppProbeHttpGetArgs(
                            path="/health",
                            port=CONTAINER_PORT,
                            scheme=app.Scheme.HTTP,
                        ),
                        initial_delay_seconds=30,
                        period_seconds=30,
                        timeout_seconds=3,
                        failure_threshold=3,
                    ),
                ],
            ),
        ],
        scale=app.ScaleArgs(min_replicas=min_replicas, max_replicas=max_replicas),
    ),
    tags=tags,
    opts=pulumi.ResourceOptions(depends_on=[image_build]),
)

endpoint = pulumi.Output.concat("https://", container_app.latest_revision_fqdn)

pulumi.export("resourceGroupName", resource_group.name)
pulumi.export("containerRegistryLoginServer", registry.login_server)
pulumi.export("containerAppName", container_app.name)
pulumi.export("endpoint", endpoint)
pulumi.export("healthUrl", pulumi.Output.concat(endpoint, "/health"))
pulumi.export("image", full_image_name)
pulumi.export("modelConcurrency", model_concurrency)
pulumi.export("maxRetryAttempts", max_retry_attempts)
pulumi.export("httpTimeoutSeconds", http_timeout_seconds)
pulumi.export("retryBaseDelaySeconds", retry_base_delay_seconds)
