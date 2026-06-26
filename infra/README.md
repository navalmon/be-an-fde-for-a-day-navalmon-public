# 🛰️ Infrastructure as Code — Deploying to Orbit

> *"Your triage system is only as reliable as the infrastructure it runs on. A perfect prompt means nothing if your container can't survive a cold start, and a flawless model is useless if the endpoint isn't reachable. Deploy it like you're launching a hull repair drone — test it, trust it, and make sure it comes back."*
> — Chief Signal Officer Mehta, margin note on the station's IaC runbook

The `infra/` folder contains infrastructure as code (IaC) configurations for provisioning and managing cloud resources using [Pulumi](https://www.pulumi.com/) with [Python and uv](https://www.pulumi.com/docs/iac/languages-sdks/python/#uv). Think of it as your station blueprint — except this station runs in Azure instead of orbiting at 0.3 AU.

## Project layout

```
infra/
└── app/
    ├── __main__.py      # Pulumi program — your orbital deployment manifest
    ├── Pulumi.yml       # Project settings — station configuration
    └── pyproject.toml   # Python dependencies (Pulumi SDK, Azure SDKs, etc.)
```

## What Pulumi deploys

The Pulumi program in `infra/app` deploys the root `Dockerfile` to Azure Container Apps:

- Azure Resource Group
- Azure Container Registry with an ACR-built `fdebench-api` image
- Log Analytics Workspace
- Azure Container Apps Environment
- Azure Container App with external HTTPS ingress on port `8000`
- Container App secrets/env vars for model provider settings
- Health probe against `GET /health`

The deployment exports `endpoint` and `healthUrl` after `pulumi up`.

## Prerequisites

- Azure CLI logged into the subscription you want to use.
- Pulumi CLI. The included devcontainer installs it automatically; on Windows you can install it with `winget install Pulumi.Pulumi`.
- Azure CLI on `PATH`; Pulumi uses `az acr build` so the image builds remotely in Azure Container Registry.
- `uv` for the Python Pulumi runtime.

## Getting started on Windows

```bash
cd infra/app
uv sync
az login
pulumi login
pulumi stack select dev --create
pulumi config set azure-native:location westus2
pulumi config set fde:modelBaseUrl "<openai-compatible-model-base-url>"
pulumi config set --secret fde:modelApiKey "<model-api-key>"
pulumi up
```

If you prefer local Pulumi state instead of Pulumi Cloud:

```powershell
pulumi login --local
$env:PULUMI_CONFIG_PASSPHRASE = "<pick-a-long-random-passphrase>"
pulumi stack select dev --create
```

## Useful configuration

Set these from `infra/app` with `pulumi config set <key> <value>`.

| Key | Default | Purpose |
|---|---:|---|
| `azure-native:location` | `westus2` | Azure region |
| `resourcePrefix` | `fdebench-<stack>` | Prefix for Azure resource names |
| `containerRegistryName` | generated | Optional globally unique ACR name |
| `containerAppName` | `<prefix>-api` | Container App name |
| `minReplicas` | `1` | Keeps the endpoint warm for cold-start probes |
| `maxReplicas` | `1` | Caps scale-out and model-token spend |
| `cpu` | `1.0` | Container CPU |
| `memory` | `2Gi` | Container memory |
| `fde:modelBaseUrl` | empty | OpenAI-compatible/Azure OpenAI base URL |
| `fde:modelApiKey` | unset | Model API key; set with `--secret` |
| `fde:defaultModelName` | `gpt-4.1-mini` | Default `X-Model-Name` header value |
| `fde:triageModelName` | unset | Optional `/triage` model header override |
| `fde:extractModelName` | unset | Optional `/extract` model header override |
| `fde:orchestrateModelName` | unset | Optional `/orchestrate` model header override |
| `fde:modelConcurrency` | `5` | Outbound model-call concurrency throttle |
| `fde:maxRetryAttempts` | `3` | Retry attempts for transient model/tool failures |
| `fde:httpTimeoutSeconds` | `20` | Shared outbound HTTP timeout |
| `fde:modelMaxTokens` | `1024` | Maximum tokens requested per model call |
| `fde:extractImageDetail` | `high` | Vision image detail hint |
| `fde:extractImageFormat` | `png` | Image format sent to the vision model; `jpeg` reduces payload size |
| `fde:extractJpegQuality` | `90` | JPEG quality when `fde:extractImageFormat=jpeg` |
| `fde:extractImageMaxDimension` | `3072` | Maximum image dimension before extraction model calls |

For quota-limited subscriptions, keep `maxReplicas=1` and start with `fde:modelConcurrency=3` to `5`.

## Test the deployment

```powershell
$endpoint = pulumi stack output endpoint
curl "$endpoint/health"

cd ..\..\py\apps\eval
python run_eval.py --endpoint $endpoint
```

## Tear down

```powershell
cd infra\app
pulumi destroy
```

For more details, see [Pulumi's documentation](https://www.pulumi.com/docs/).

> **Tip from Station Ops:** Deploy early. The number of operators who deploy at hour 23 and then discover their container won't start is... nonzero. Much like hull breach drills, the best time to test your deployment is before the emergency. The second-best time is not 30 minutes before submission. The scoring computer cannot reach localhost, and neither can Commander Kapoor's patience.
