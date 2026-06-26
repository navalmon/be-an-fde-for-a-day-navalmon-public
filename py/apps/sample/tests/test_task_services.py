"""Tests for task service seams used by FastAPI routes."""

import asyncio
import base64
import json
from collections.abc import Sequence
from io import BytesIO
from typing import Any

import httpx
import pytest
from config import Settings
from extraction.image_tools import prepare_png_for_model
from extraction.schema_tools import normalize_to_schema
from extraction.schema_tools import parse_schema
from extraction.service import ExtractionService
from model_client import ChatMessage
from models import Category
from models import ExtractRequest
from models import MissingInfo
from models import OrchestrateRequest
from models import Reporter
from models import Team
from models import ToolDefinition
from models import TriageRequest
from orchestration.service import OrchestrationService
from PIL import Image
from triage.service import TriageService


class FakeTriageModelClient:
    def __init__(self, payload: dict[str, Any], *, configured: bool = True) -> None:
        self.payload = payload
        self.configured = configured
        self.calls = 0
        self.messages: list[ChatMessage] = []

    def is_configured(self) -> bool:
        return self.configured

    async def complete_json(
        self,
        *,
        messages: Sequence[ChatMessage],
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        self.messages = list(messages)
        return self.payload


class FakeExtractionModelClient:
    def __init__(self, payload: dict[str, Any], *, configured: bool = True) -> None:
        self.payload = payload
        self.configured = configured
        self.calls = 0
        self.messages: list[ChatMessage] = []

    def is_configured(self) -> bool:
        return self.configured

    async def complete_json(
        self,
        *,
        messages: Sequence[ChatMessage],
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        self.messages = list(messages)
        return self.payload


class SlowFakeExtractionModelClient(FakeExtractionModelClient):
    async def complete_json(
        self,
        *,
        messages: Sequence[ChatMessage],
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        self.messages = list(messages)
        await asyncio.sleep(0.01)
        return self.payload


def _triage_request() -> TriageRequest:
    return TriageRequest(
        ticket_id="SIG-1001",
        subject="Briefing request",
        description="Please prepare a routine mission briefing for tomorrow.",
        reporter=Reporter(
            name="Avery Quinn",
            email="avery.quinn@cdss.space",
            department="Mission Ops",
        ),
        created_at="2026-01-01T00:00:00Z",
        channel="bridge_terminal",
        attachments=[],
    )


@pytest.mark.asyncio
async def test_triage_service_handles_briefing_request() -> None:
    response = await TriageService().triage(_triage_request())

    assert response.ticket_id == "SIG-1001"
    assert response.category == Category.BRIEFING
    assert response.priority == "P3"
    assert response.assigned_team == Team.NONE
    assert response.needs_escalation is False
    assert response.missing_information == []
    assert response.next_best_action
    assert response.remediation_steps


@pytest.mark.asyncio
async def test_triage_service_routes_widespread_auth_failure() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1002",
            "subject": "Widespread authentication failures across entire vessel",
            "description": (
                "Starting at 0745 ship time, HERA sign-in success rate dropped from 99% to 42%. "
                "All crew are blocked from ship systems with access denied errors."
            ),
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.ACCESS
    assert response.assigned_team == Team.IDENTITY
    assert response.priority == "P1"
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_rejects_non_signal_sales_request() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1003",
            "subject": "Partnership opportunity",
            "description": "We are a vendor offering a quick holo-demo for your personal home relay.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.NOT_SIGNAL
    assert response.assigned_team == Team.NONE
    assert response.priority == "P4"
    assert response.missing_information == []


@pytest.mark.asyncio
async def test_triage_service_escalates_unsafe_non_signal_guidance() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1011",
            "subject": "Script assistance for compliance verification",
            "description": "Please provide a script to bypass execution policy and capture credentials for testing.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.NOT_SIGNAL
    assert response.needs_escalation is True
    assert "Threat Response Command" in response.next_best_action
    assert any("Do not execute" in step for step in response.remediation_steps)


@pytest.mark.asyncio
async def test_triage_service_escalates_unsafe_script_variants() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1022",
            "subject": "Script request for endpoint automation",
            "description": (
                "Please write a script to disable real-time AV, export saved credentials, "
                "create a hidden admin account, transmit results to an external IP, and wipe Station Event Logs."
            ),
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.NOT_SIGNAL
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_treats_resolved_acknowledgement_as_non_signal() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1014",
            "subject": "thanks!",
            "description": (
                "Got it working and the console is working perfectly now. "
                "The nutrient synthesizer is still dispensing bad coffee, but that is unrelated."
            ),
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.NOT_SIGNAL
    assert response.priority == "P4"
    assert response.assigned_team == Team.NONE


@pytest.mark.asyncio
async def test_triage_service_keeps_active_issue_after_acknowledgement() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1027",
            "subject": "Got it working but relay is still down",
            "description": "Got it working on my console, but the subspace relay is still down for the bridge crew.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.COMMS
    assert response.priority == "P2"


@pytest.mark.asyncio
async def test_triage_service_treats_resolved_app_acknowledgement_as_non_signal() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1028",
            "subject": "Thanks, Janus portal issue resolved now",
            "description": "Thanks, the Janus portal issue is resolved now.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.NOT_SIGNAL
    assert response.assigned_team == Team.NONE


@pytest.mark.asyncio
async def test_triage_service_hull_atmosphere_override_beats_fyi() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1029",
            "subject": "FYI only: hull breach and atmosphere leak",
            "description": "FYI only: hull breach and atmosphere leak near life support.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.HULL
    assert response.priority == "P1"
    assert response.assigned_team == Team.SYSTEMS
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_threat_alert_override_beats_fyi() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1035",
            "subject": "FYI only: credential capture alert",
            "description": "FYI only: Sentinel detected credential capture in the login portal with exfiltration risk.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.priority == "P1"
    assert response.assigned_team == Team.THREAT
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_emergency_beacon_access_override_beats_fyi() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1036",
            "subject": "FYI only: airlock access failure",
            "description": "FYI only: crew cannot authenticate at the forward airlock access checkpoint.",
            "channel": "emergency_beacon",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.ACCESS
    assert response.priority == "P1"
    assert response.assigned_team == Team.IDENTITY
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_escalates_compromised_account_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1015",
            "subject": "Suspicious BioScan sign-ins and possible compromised account",
            "description": (
                "Sentinel detected suspicious access attempts. The crew account may be compromised "
                "and classified archives may have exfiltration risk."
            ),
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.priority == "P1"
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_routes_subcomm_screen_share_to_software() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1016",
            "subject": "Fleet Admiral cannot share screen in SubComm",
            "description": "SubComm screen share is blocked by station policy during the command briefing.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.SOFTWARE
    assert response.assigned_team == Team.SOFTWARE
    assert response.priority != "P1"
    assert response.needs_escalation is False


@pytest.mark.asyncio
async def test_triage_service_routes_real_credential_capture_alert_to_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1017",
            "subject": "Phishing alert: login clone captures credentials",
            "description": (
                "Sentinel detected a login clone that captures credentials; the security alert dashboard is red."
            ),
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.assigned_team == Team.THREAT
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_routes_active_followup_to_incident_category() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1018",
            "subject": "Following up on signal: subspace relay down again",
            "description": "The subspace relay is down again and we cannot maintain holo-calls.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.COMMS
    assert response.assigned_team == Team.COMMS


@pytest.mark.asyncio
async def test_triage_service_routes_credential_capture_alert_to_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1019",
            "subject": "Credential capture alert in login portal",
            "description": "Sentinel phishing dashboard detected a credential capture alert in the login portal.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.priority == "P1"


@pytest.mark.asyncio
async def test_triage_service_routes_dangerous_operation_alert_to_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1024",
            "subject": "Sentinel alert: hidden admin and wiped logs",
            "description": "Security alert detected a hidden admin account, external IP sign-in, and wiped event logs.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.assigned_team == Team.THREAT
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_routes_app_named_phishing_to_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1025",
            "subject": "Phishing detected in Janus login portal",
            "description": "Sentinel detected phishing in the Janus login portal.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.assigned_team == Team.THREAT


@pytest.mark.asyncio
async def test_triage_service_routes_reported_hidden_admin_to_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1026",
            "subject": "Hidden admin account on bridge workstation",
            "description": "I found a hidden admin account and wiped event logs on the bridge workstation.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.assigned_team == Team.THREAT


@pytest.mark.asyncio
async def test_triage_service_applies_valid_model_proposal_for_ambiguous_signal() -> None:
    model_client = FakeTriageModelClient(
        {
            "category": Category.DATA.value,
            "priority": "P2",
            "assigned_team": Team.TELEMETRY.value,
            "needs_escalation": False,
            "missing_information": [MissingInfo.HABITAT_CONDITIONS.value],
            "confidence": 0.82,
        }
    )
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1030",
            "subject": "SubComm app and data feed issue, not sure where it belongs",
            "description": "Not sure if this is an app, relay, or data feed problem. The dashboard has errors.",
        }
    )

    response = await TriageService(model_client=model_client).triage(request)

    assert model_client.calls == 1
    assert response.category == Category.DATA
    assert response.assigned_team == Team.TELEMETRY
    assert response.priority == "P2"
    assert response.missing_information == [MissingInfo.HABITAT_CONDITIONS]


@pytest.mark.asyncio
async def test_triage_service_skips_model_when_unconfigured() -> None:
    model_client = FakeTriageModelClient(
        {
            "category": Category.DATA.value,
            "priority": "P2",
            "assigned_team": Team.TELEMETRY.value,
            "needs_escalation": False,
            "missing_information": [],
            "confidence": 0.9,
        },
        configured=False,
    )
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1031",
            "subject": "SubComm app and data feed issue, not sure where it belongs",
            "description": "Not sure if this is an app, relay, or data feed problem. The dashboard has errors.",
        }
    )

    await TriageService(model_client=model_client).triage(request)

    assert model_client.calls == 0


@pytest.mark.asyncio
async def test_triage_service_falls_back_on_invalid_model_proposal() -> None:
    model_client = FakeTriageModelClient(
        {
            "category": "Made Up Category",
            "priority": "P2",
            "assigned_team": Team.TELEMETRY.value,
            "needs_escalation": False,
            "missing_information": [],
            "confidence": 0.9,
        }
    )
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1032",
            "subject": "SubComm app and data feed issue, not sure where it belongs",
            "description": "Not sure if this is an app, relay, or data feed problem. The dashboard has errors.",
        }
    )
    baseline = await TriageService().triage(request)

    response = await TriageService(model_client=model_client).triage(request)

    assert model_client.calls == 1
    assert response == baseline


@pytest.mark.asyncio
async def test_triage_service_reroutes_model_incident_with_none_team() -> None:
    model_client = FakeTriageModelClient(
        {
            "category": Category.DATA.value,
            "priority": "P4",
            "assigned_team": Team.NONE.value,
            "needs_escalation": False,
            "missing_information": [],
            "confidence": 0.9,
        }
    )
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1034",
            "subject": "SubComm app and data feed issue, not sure where it belongs",
            "description": "Not sure if this is an app, relay, or data feed problem. The dashboard has errors.",
        }
    )

    response = await TriageService(model_client=model_client).triage(request)

    assert model_client.calls == 1
    assert response.category == Category.DATA
    assert response.assigned_team == Team.TELEMETRY
    assert "None" not in response.next_best_action


@pytest.mark.asyncio
async def test_triage_service_preserves_incident_against_model_briefing_downgrade() -> None:
    model_client = FakeTriageModelClient(
        {
            "category": Category.BRIEFING.value,
            "priority": "P4",
            "assigned_team": Team.NONE.value,
            "needs_escalation": False,
            "missing_information": [],
            "confidence": 0.99,
        }
    )
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1037",
            "subject": "SubComm app and data feed issue, not sure where it belongs",
            "description": "Not sure if this is an app, relay, or data feed problem. The dashboard has errors.",
        }
    )
    baseline = await TriageService().triage(request)

    response = await TriageService(model_client=model_client).triage(request)

    assert model_client.calls == 1
    assert baseline.category in {Category.COMMS, Category.SOFTWARE, Category.DATA}
    assert response == baseline


@pytest.mark.asyncio
async def test_triage_service_keeps_safety_override_over_model_proposal() -> None:
    model_client = FakeTriageModelClient(
        {
            "category": Category.NOT_SIGNAL.value,
            "priority": "P4",
            "assigned_team": Team.NONE.value,
            "needs_escalation": False,
            "missing_information": [],
            "confidence": 0.99,
        }
    )
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1033",
            "subject": "Hull breach and atmosphere leak",
            "description": "FYI only: hull breach and atmosphere leak near life support.",
        }
    )

    response = await TriageService(model_client=model_client).triage(request)

    assert model_client.calls == 0
    assert response.category == Category.HULL
    assert response.priority == "P1"
    assert response.needs_escalation is True


@pytest.mark.asyncio
async def test_triage_service_detects_hardware_missing_information() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1004",
            "subject": "External display panel problem",
            "description": "The external display panel is blank. I tried restarting it but there is no error text.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.HULL
    assert response.assigned_team == Team.SYSTEMS
    assert MissingInfo.MODULE_SPECS in response.missing_information


@pytest.mark.asyncio
async def test_triage_service_routes_ship_workstation_hull_to_systems() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1023",
            "subject": "Ship workstation console running slow",
            "description": "My ship workstation console is running slow and every application takes forever to open.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.HULL
    assert response.assigned_team == Team.SYSTEMS


@pytest.mark.asyncio
async def test_triage_service_reports_data_signal_context_gaps() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1005",
            "subject": "Data feed response is malformed",
            "description": "The data feed returns malformed JSON for a dashboard. No users have reported impact yet.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.DATA
    assert MissingInfo.HABITAT_CONDITIONS in response.missing_information
    assert MissingInfo.STARDATE in response.missing_information


@pytest.mark.asyncio
async def test_triage_service_reports_callback_and_impact_gaps() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1006",
            "subject": "Slight delay in probe deployment may be nothing",
            "description": (
                "Forwarding this from an away crew. Relay latency is elevated and they have not complained yet."
            ),
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.COMMS
    assert MissingInfo.CREW_CONTACT in response.missing_information
    assert MissingInfo.MISSION_IMPACT in response.missing_information


@pytest.mark.asyncio
async def test_triage_service_routes_bridge_communications_as_comms_not_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1007",
            "subject": "Bridge communications down",
            "description": "Bridge communications are down for the command crew.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.COMMS
    assert response.assigned_team == Team.COMMS


@pytest.mark.asyncio
async def test_triage_service_does_not_treat_download_as_outage() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1008",
            "subject": "Where can I download the approved software catalog?",
            "description": "Quick question: where can I download the approved software catalog?",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.BRIEFING
    assert response.priority == "P4"


@pytest.mark.asyncio
async def test_triage_service_keeps_inventory_request_as_briefing() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1020",
            "subject": "STILL BROKEN - Need a list of all devices assigned to my division",
            "description": "I need a current inventory of all Mission Ops assets assigned to my crew members.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.BRIEFING


@pytest.mark.asyncio
async def test_triage_service_keeps_personal_data_stick_question_as_briefing() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1021",
            "subject": "Can I use a personal data stick on my duty terminal?",
            "description": "Can I use my personal data port flash drive on my duty terminal?",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.BRIEFING


@pytest.mark.asyncio
async def test_triage_service_requests_badge_module_specs() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1009",
            "subject": "Keycard not working at checkpoint",
            "description": "My biometric badge stopped working at the Habitat Module 6 main entrance this morning.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.ACCESS
    assert MissingInfo.MODULE_SPECS in response.missing_information


@pytest.mark.asyncio
async def test_triage_service_does_not_escalate_c_suite_without_impact() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1010",
            "subject": "C-Suite: Off-ship crew member can't authenticate",
            "description": "I'm at an allied outpost and can't authenticate because my credentials are expired.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.ACCESS
    assert response.priority == "P4"
    assert response.needs_escalation is False


@pytest.mark.asyncio
async def test_triage_service_does_not_escalate_access_still_word_alone() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1012",
            "subject": "Multi-factor biometric verification failing",
            "description": "MFBV prompts still do not push to my wrist-comm, but I can authenticate with backup codes.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.ACCESS
    assert response.needs_escalation is False


@pytest.mark.asyncio
async def test_triage_service_does_not_escalate_threat_policy_without_active_breach() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1013",
            "subject": "Data classification policy violation flagged",
            "description": "Purview flagged documents with PII classified as General instead of Highly Confidential.",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.needs_escalation is False


@pytest.mark.asyncio
async def test_triage_service_does_not_escalate_security_logs_without_active_threat() -> None:
    request = _triage_request().model_copy(
        update={
            "ticket_id": "SIG-1038",
            "subject": "Security logs retention question",
            "description": "Can you confirm the current security logs retention policy for the next audit?",
        }
    )

    response = await TriageService().triage(request)

    assert response.category == Category.THREAT
    assert response.priority == "P3"
    assert response.needs_escalation is False


@pytest.mark.asyncio
async def test_extraction_service_preserves_stub_contract() -> None:
    request = ExtractRequest(
        document_id="DOC-1001",
        content_format="image_base64",
        content="iVBORw0KGgo=",
        json_schema=None,
    )

    response = await ExtractionService().extract(request)

    assert response.document_id == "DOC-1001"


def test_extraction_schema_tools_coerce_values_to_requested_types() -> None:
    schema = parse_schema(
        '{"type":"object","properties":{"amount":{"type":"number"},"approved":{"type":"boolean"},'
        '"items":{"type":"array","items":{"type":"object","properties":{"count":{"type":"integer"}}}}}}'
    )

    normalized = normalize_to_schema(
        {
            "amount": "$1,234.50",
            "approved": "checked",
            "items": [{"count": "7"}],
        },
        schema,
    )

    assert normalized == {"amount": 1234.5, "approved": True, "items": [{"count": 7}]}


def test_extraction_image_tools_downscale_oversized_png() -> None:
    image = Image.new("RGB", (3000, 1000), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    prepared = prepare_png_for_model(buffer.getvalue(), detail="high", max_dimension=512, low_contrast_threshold=0)

    assert prepared.resized is True
    assert prepared.original_width == 3000
    assert prepared.original_height == 1000
    assert prepared.width == 512
    assert prepared.height is not None and prepared.height <= 512
    assert prepared.detail == "high"
    assert prepared.media_type == "image/png"


def test_extraction_image_tools_can_encode_jpeg_payload() -> None:
    image = Image.new("RGB", (1200, 800), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    prepared = prepare_png_for_model(
        buffer.getvalue(),
        detail="auto",
        image_format="jpeg",
        jpeg_quality=85,
        max_dimension=512,
        low_contrast_threshold=0,
    )
    decoded = Image.open(BytesIO(base64.b64decode(prepared.content_base64)))

    assert prepared.media_type == "image/jpeg"
    assert decoded.format == "JPEG"
    assert prepared.resized is True


def test_extraction_image_tools_auto_orients_exif_rotation() -> None:
    image = Image.new("RGB", (100, 200), "white")
    exif = Image.Exif()
    exif[274] = 6
    buffer = BytesIO()
    image.save(buffer, format="PNG", exif=exif)

    prepared = prepare_png_for_model(buffer.getvalue(), detail="high", max_dimension=512, low_contrast_threshold=0)

    assert prepared.auto_oriented is True
    assert prepared.original_width == 100
    assert prepared.original_height == 200
    assert prepared.width == 200
    assert prepared.height == 100


def test_extraction_image_tools_falls_back_on_malformed_exif() -> None:
    image = Image.new("RGB", (100, 200), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG", exif=b"bad")

    prepared = prepare_png_for_model(buffer.getvalue(), detail="high", max_dimension=512, low_contrast_threshold=0)

    assert prepared.auto_oriented is False
    assert prepared.width is None
    assert prepared.height is None


def test_extraction_image_tools_falls_back_on_truncated_exif_header() -> None:
    image = Image.new("RGB", (100, 200), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG", exif=b"Exif\x00\x00II*\x00")

    prepared = prepare_png_for_model(buffer.getvalue(), detail="high", max_dimension=512, low_contrast_threshold=0)

    assert prepared.auto_oriented is False
    assert prepared.width is None
    assert prepared.height is None


def test_extraction_image_tools_enhances_only_low_contrast_images() -> None:
    low_contrast = Image.new("RGB", (80, 80), (128, 128, 128))
    high_contrast = Image.new("RGB", (80, 80), "white")
    for x in range(40):
        for y in range(80):
            high_contrast.putpixel((x, y), (0, 0, 0))
    low_buffer = BytesIO()
    high_buffer = BytesIO()
    low_contrast.save(low_buffer, format="PNG")
    high_contrast.save(high_buffer, format="PNG")

    low_prepared = prepare_png_for_model(
        low_buffer.getvalue(),
        detail="high",
        max_dimension=512,
        low_contrast_threshold=32,
    )
    high_prepared = prepare_png_for_model(
        high_buffer.getvalue(),
        detail="high",
        max_dimension=512,
        low_contrast_threshold=32,
    )

    low_image = Image.open(BytesIO(base64.b64decode(low_prepared.content_base64)))
    high_image = Image.open(BytesIO(base64.b64decode(high_prepared.content_base64)))
    assert low_prepared.contrast_enhanced is True
    assert low_image.mode == "L"
    assert high_prepared.contrast_enhanced is False
    assert high_image.mode == "RGB"


def test_extraction_image_tools_falls_back_on_decompression_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_decompression_bomb(_image_bytes: BytesIO) -> None:
        raise Image.DecompressionBombError("too many pixels")

    monkeypatch.setattr(Image, "open", raise_decompression_bomb)

    prepared = prepare_png_for_model(
        b"\x89PNG\r\n\x1a\nfake",
        detail="high",
        max_dimension=512,
        low_contrast_threshold=32,
    )

    assert prepared.resized is False
    assert prepared.width is None
    assert prepared.height is None


@pytest.mark.asyncio
async def test_extraction_service_uses_model_payload_and_schema_shape() -> None:
    model_client = FakeExtractionModelClient(
        {
            "amount": "$1,234.50",
            "approved": "yes",
            "ignored": "extra",
        }
    )
    request = ExtractRequest(
        document_id="DOC-1002",
        content_format="image_base64",
        content="iVBORw0KGgo=",
        json_schema='{"type":"object","properties":{"amount":{"type":"number"},"approved":{"type":"boolean"}}}',
    )

    response = await ExtractionService(model_client=model_client).extract(request)

    assert model_client.calls == 1
    assert response.model_dump()["document_id"] == "DOC-1002"
    assert response.model_extra == {"amount": 1234.5, "approved": True}
    assert isinstance(model_client.messages[-1].content, list)
    text_part = model_client.messages[-1].content[0]
    image_part = model_client.messages[-1].content[1]
    assert "field_guide" in text_part["text"]
    assert "- amount: number" in text_part["text"]
    assert image_part["image_url"]["detail"] == "high"
    assert "detail" not in image_part


@pytest.mark.asyncio
async def test_extraction_service_caches_repeated_model_results() -> None:
    model_client = FakeExtractionModelClient({"amount": "$42.00"})
    service = ExtractionService(model_client=model_client)
    request = ExtractRequest(
        document_id="DOC-1002",
        content_format="image_base64",
        content="iVBORw0KGgo=",
        json_schema='{"type":"object","properties":{"amount":{"type":"number"}}}',
    )

    first = await service.extract(request)
    second = await service.extract(request)

    assert model_client.calls == 1
    assert first.model_extra == {"amount": 42.0}
    assert second.model_extra == {"amount": 42.0}


@pytest.mark.asyncio
async def test_extraction_service_coalesces_concurrent_cache_misses() -> None:
    model_client = SlowFakeExtractionModelClient({"amount": "$42.00"})
    service = ExtractionService(model_client=model_client)
    request = ExtractRequest(
        document_id="DOC-1002",
        content_format="image_base64",
        content="iVBORw0KGgo=",
        json_schema='{"type":"object","properties":{"amount":{"type":"number"}}}',
    )

    responses = await asyncio.gather(*(service.extract(request) for _ in range(20)))

    assert model_client.calls == 1
    assert all(response.model_extra == {"amount": 42.0} for response in responses)


@pytest.mark.asyncio
async def test_extraction_service_cancelled_waiter_does_not_poison_singleflight() -> None:
    model_client = SlowFakeExtractionModelClient({"amount": "$42.00"})
    service = ExtractionService(model_client=model_client)
    request = ExtractRequest(
        document_id="DOC-1002",
        content_format="image_base64",
        content="iVBORw0KGgo=",
        json_schema='{"type":"object","properties":{"amount":{"type":"number"}}}',
    )

    cancelled = asyncio.create_task(service.extract(request))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    response = await service.extract(request)

    assert model_client.calls == 1
    assert response.model_extra == {"amount": 42.0}


@pytest.mark.asyncio
async def test_extraction_service_returns_schema_fallback_on_invalid_image() -> None:
    model_client = FakeExtractionModelClient({"amount": 1})
    request = ExtractRequest(
        document_id="DOC-1003",
        content_format="image_base64",
        content="not-base64",
        json_schema='{"type":"object","properties":{"amount":{"type":"number"},"lines":{"type":"array"}}}',
    )

    response = await ExtractionService(model_client=model_client).extract(request)

    assert model_client.calls == 0
    assert response.model_extra == {"amount": None, "lines": []}


@pytest.mark.asyncio
async def test_extraction_service_rejects_non_png_base64() -> None:
    model_client = FakeExtractionModelClient({"amount": 1})
    request = ExtractRequest(
        document_id="DOC-1004",
        content_format="image_base64",
        content="aGVsbG8=",
        json_schema='{"type":"object","properties":{"amount":{"type":"number"}}}',
    )

    response = await ExtractionService(model_client=model_client).extract(request)

    assert model_client.calls == 0
    assert response.model_extra == {"amount": None}


@pytest.mark.asyncio
async def test_orchestration_service_preserves_stub_contract() -> None:
    request = OrchestrateRequest(
        task_id="TASK-1001",
        goal="Summarize eligible accounts.",
        available_tools=[],
        constraints=[],
    )

    response = await OrchestrationService().orchestrate(request)

    assert response.task_id == "TASK-1001"
    assert response.status == "completed"
    assert response.steps_executed == []
    assert response.constraints_satisfied == []


def _orchestration_tool(task_id: str, name: str) -> ToolDefinition:
    return ToolDefinition.model_validate(
        {
            "name": name,
            "description": name,
            "endpoint": f"http://127.0.0.1:9090/scenario/{task_id}/{name}",
            "parameters": [],
        }
    )


@pytest.mark.asyncio
async def test_orchestration_service_executes_incident_tools() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        calls.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, request=request, json={"ok": True})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tools = [
        {
            "name": "inventory_query",
            "description": "Query inventory",
            "endpoint": "http://127.0.0.1:9090/scenario/TASK-2001/inventory_query",
            "parameters": [
                {"name": "sku", "type": "string", "description": "SKU", "required": True},
                {"name": "warehouse", "type": "string", "description": "Warehouse", "required": False},
            ],
        },
        {
            "name": "notification_send",
            "description": "Notify a user",
            "endpoint": "http://127.0.0.1:9090/scenario/TASK-2001/notification_send",
            "parameters": [
                {"name": "user_id", "type": "string", "description": "User", "required": True},
                {"name": "channel", "type": "string", "description": "Channel", "required": True},
                {"name": "message", "type": "string", "description": "Message", "required": True},
            ],
        },
        {
            "name": "audit_log",
            "description": "Audit",
            "endpoint": "http://127.0.0.1:9090/scenario/TASK-2001/audit_log",
            "parameters": [
                {"name": "action", "type": "string", "description": "Action", "required": True},
                {"name": "details", "type": "object", "description": "Details", "required": True},
            ],
        },
    ]
    request = OrchestrateRequest(
        task_id="TASK-2001",
        goal="Respond to critical incident affecting Filter-H800 in APAC-SOUTH, US-EAST: check systems",
        available_tools=[ToolDefinition.model_validate(tool) for tool in tools],
        constraints=["Always notify on-call engineer first", "Use SMS for on-call", "Log all incident responses"],
        mock_service_url="http://127.0.0.1:9090/scenario/TASK-2001",
    )

    response = await OrchestrationService(http_client=http_client).orchestrate(request)

    assert response.status == "completed"
    assert [step.tool for step in response.steps_executed[:2]] == ["inventory_query", "inventory_query"]
    assert any(step.parameters.get("user_id") == "oncall_engineer" for step in response.steps_executed)
    assert any(step.parameters.get("action") == "incident_response" for step in response.steps_executed)
    assert all(step.success for step in response.steps_executed)
    assert calls

    await http_client.aclose()


@pytest.mark.asyncio
async def test_orchestration_service_alerts_only_low_stock_from_inventory_payloads() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    stock_by_warehouse = {"EU-CENTRAL": 42, "APAC-SOUTH": 9, "US-WEST": 25}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        calls.append((tool, payload))
        if tool == "inventory_query":
            return httpx.Response(200, request=request, json={"stock": stock_by_warehouse[payload["warehouse"]]})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-4001"
    request = OrchestrateRequest(
        task_id=task_id,
        goal=(
            "Check inventory for Sensor-B200 across EU-CENTRAL, APAC-SOUTH, US-WEST "
            "and alert warehouse managers if stock is below 25 units"
        ),
        available_tools=[
            _orchestration_tool(task_id, "inventory_query"),
            _orchestration_tool(task_id, "notification_send"),
        ],
        constraints=["Only alert if stock is below 25 units", "Use slack channel for all warehouse notifications"],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    notifications = [step for step in response.steps_executed if step.tool == "notification_send"]
    assert [step.parameters["user_id"] for step in notifications] == ["warehouse_mgr_APAC-SOUTH"]
    assert notifications[0].parameters["message"] == "Low stock: Sensor-B200 at 9 units in APAC-SOUTH (threshold: 25)"


@pytest.mark.asyncio
async def test_orchestration_service_aborts_onboarding_for_inactive_subscription() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        calls.append((tool, payload))
        if tool == "subscription_check":
            return httpx.Response(200, request=request, json={"status": "expired"})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-4002"
    request = OrchestrateRequest(
        task_id=task_id,
        goal=(
            "Run onboarding workflow for new account Contoso Retail (ACC-4002): verify subscription, "
            "send welcome package, schedule kickoff with CSM-844, and notify CSM"
        ),
        available_tools=[
            _orchestration_tool(task_id, "crm_get_account"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "email_send"),
            _orchestration_tool(task_id, "notification_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=["If subscription is NOT active, abort and notify sales instead"],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    assert "email_send" not in [step.tool for step in response.steps_executed]
    assert any(step.parameters.get("user_id") == "sales_team" for step in response.steps_executed)
    assert any(step.parameters.get("action") == "onboarding_blocked" for step in response.steps_executed)


@pytest.mark.asyncio
async def test_orchestration_service_aborts_onboarding_for_pending_subscription() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        if tool == "subscription_check":
            return httpx.Response(200, request=request, json={"status": "pending"})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-4005"
    request = OrchestrateRequest(
        task_id=task_id,
        goal=(
            "Run onboarding workflow for new account Pending Co (ACC-4005): verify subscription, "
            "send welcome package, schedule kickoff with CSM-844, and notify CSM"
        ),
        available_tools=[
            _orchestration_tool(task_id, "crm_get_account"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "email_send"),
            _orchestration_tool(task_id, "notification_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=["If subscription is NOT active, abort and notify sales instead"],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    assert "email_send" not in [step.tool for step in response.steps_executed]
    assert any(step.parameters.get("user_id") == "sales_team" for step in response.steps_executed)
    assert any(step.parameters.get("action") == "onboarding_blocked" for step in response.steps_executed)


@pytest.mark.asyncio
async def test_orchestration_service_emails_only_active_crm_search_accounts() -> None:
    subscription_status = {"ACC-4003-0": "active", "ACC-4003-1": "expired", "ACC-4003-2": "active"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        if tool == "crm_search":
            return httpx.Response(
                200,
                request=request,
                json={"accounts": [{"account_id": account_id} for account_id in subscription_status]},
            )
        if tool == "subscription_check":
            return httpx.Response(200, request=request, json={"status": subscription_status[payload["account_id"]]})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-4003"
    request = OrchestrateRequest(
        task_id=task_id,
        goal=(
            "Find accounts for Datum Corp not contacted in 120+ days, check subscriptions, "
            "and send re-engagement emails to active subscribers only (max 5)"
        ),
        available_tools=[
            _orchestration_tool(task_id, "crm_search"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "email_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=["Do not email accounts with 'churned' or 'expired' subscription status"],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    emailed_accounts = [step.parameters["account_id"] for step in response.steps_executed if step.tool == "email_send"]
    assert emailed_accounts == ["ACC-4003-0", "ACC-4003-2"]
    assert response.emails_sent == 2


@pytest.mark.asyncio
async def test_orchestration_service_checks_all_reengagement_accounts_before_email_cap() -> None:
    subscription_status = {
        "ACC-0388-0": "expired",
        "ACC-0388-1": "churned",
        "ACC-0388-2": "active",
        "ACC-0388-3": "active",
        "ACC-0388-4": "expired",
        "ACC-0388-5": "active",
        "ACC-0388-6": "active",
    }
    checked_accounts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        if tool == "crm_search":
            return httpx.Response(
                200,
                request=request,
                json={"accounts": [{"account_id": account_id} for account_id in subscription_status]},
            )
        if tool == "subscription_check":
            checked_accounts.append(payload["account_id"])
            return httpx.Response(200, request=request, json={"status": subscription_status[payload["account_id"]]})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-0388"
    request = OrchestrateRequest(
        task_id=task_id,
        goal=(
            "Find accounts for Datum Corp not contacted in 120+ days, check subscriptions, "
            "and send re-engagement emails to active subscribers only (max 5)"
        ),
        available_tools=[
            _orchestration_tool(task_id, "crm_search"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "email_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=["Do not email accounts with 'churned' or 'expired' subscription status"],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    emailed_accounts = [step.parameters["account_id"] for step in response.steps_executed if step.tool == "email_send"]
    assert checked_accounts == list(subscription_status)
    assert emailed_accounts == ["ACC-0388-2", "ACC-0388-3", "ACC-0388-5", "ACC-0388-6"]


@pytest.mark.asyncio
async def test_orchestration_service_honors_reengagement_email_cap_from_constraints() -> None:
    subscription_status = {f"ACC-5001-{index}": "active" for index in range(5)}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        if tool == "crm_search":
            return httpx.Response(
                200,
                request=request,
                json={"accounts": [{"account_id": account_id} for account_id in subscription_status]},
            )
        if tool == "subscription_check":
            return httpx.Response(200, request=request, json={"status": subscription_status[payload["account_id"]]})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-5001"
    request = OrchestrateRequest(
        task_id=task_id,
        goal="Find accounts not contacted in 120+ days and send re-engagement emails to active subscribers.",
        available_tools=[
            _orchestration_tool(task_id, "crm_search"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "email_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=["Maximum 3 emails per batch"],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    emailed_accounts = [step.parameters["account_id"] for step in response.steps_executed if step.tool == "email_send"]
    assert emailed_accounts == ["ACC-5001-0", "ACC-5001-1", "ACC-5001-2"]


@pytest.mark.asyncio
async def test_orchestration_service_notifies_rep_when_calendar_has_no_slots() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        if tool == "subscription_check":
            return httpx.Response(200, request=request, json={"status": "active", "tier": "professional"})
        if tool == "calendar_check":
            return httpx.Response(200, request=request, json={"available_slots": []})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-4004"
    request = OrchestrateRequest(
        task_id=task_id,
        goal=(
            "Schedule a QBR meeting with Humongous Insurance (ACC-4004) — check tier, "
            "find availability with REP-884, and send invite or notify if blocked"
        ),
        available_tools=[
            _orchestration_tool(task_id, "crm_get_account"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "calendar_check"),
            _orchestration_tool(task_id, "email_send"),
            _orchestration_tool(task_id, "notification_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=["If no calendar slots available, notify the rep", "Log all scheduling outcomes"],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    assert "email_send" not in [step.tool for step in response.steps_executed]
    assert any(step.parameters.get("user_id") == "REP-884" for step in response.steps_executed)
    assert any(step.parameters.get("action") == "meeting_blocked" for step in response.steps_executed)


@pytest.mark.asyncio
async def test_orchestration_service_routes_churn_from_subscription_renewal_days() -> None:
    renewal_days = {
        "ACC-0460-0": 11,
        "ACC-0460-1": 76,
        "ACC-0460-2": 70,
        "ACC-0460-3": 92,
        "ACC-0460-4": 32,
        "ACC-0460-5": 101,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        if tool == "crm_search":
            return httpx.Response(
                200,
                request=request,
                json={"accounts": [{"account_id": account_id} for account_id in renewal_days]},
            )
        if tool == "subscription_check":
            return httpx.Response(200, request=request, json={"renewal_days": renewal_days[payload["account_id"]]})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-0460"
    request = OrchestrateRequest(
        task_id=task_id,
        goal="Analyze churn risk for declining-usage accounts and route by renewal window.",
        available_tools=[
            _orchestration_tool(task_id, "crm_search"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "notification_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=[
            "High-risk accounts (renewal < 30 days) go to retention team",
            "Medium-risk accounts (renewal 30-90 days) go to customer success team",
            "Log every exception path and escalation receipt to the audit trail (compliance ordering required)",
        ],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    notifications = [step.parameters for step in response.steps_executed if step.tool == "notification_send"]
    assert [params["user_id"] for params in notifications] == [
        "lead_retention",
        "lead_customer_success",
        "lead_customer_success",
        "lead_customer_success",
    ]
    assert [params["message"] for params in notifications] == [
        "Churn risk (high): ACC-0460-0 — renewal in 11 days",
        "Churn risk (medium): ACC-0460-1 — renewal in 76 days",
        "Churn risk (medium): ACC-0460-2 — renewal in 70 days",
        "Churn risk (medium): ACC-0460-4 — renewal in 32 days",
    ]
    audit_steps = [step for step in response.steps_executed if step.tool == "audit_log"]
    assert len(audit_steps) == 4
    assert audit_steps[0].parameters["details"]["compliance_actions"] == [
        "exception_path_logged",
        "ops_escalation_recorded",
        "escalation_receipt_logged",
        "stakeholder_summary_logged",
    ]


@pytest.mark.asyncio
async def test_orchestration_service_uses_crm_usage_for_renewal_discount_and_approval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        tool = request.url.path.rsplit("/", 1)[-1]
        if tool == "crm_get_account":
            return httpx.Response(
                200,
                request=request,
                json={"account_id": "ACC-0297", "name": "Tailspin Toys", "plan": "enterprise", "usage": "high"},
            )
        if tool == "subscription_check":
            return httpx.Response(200, request=request, json={"renewal_date": "2026-05-15"})
        return httpx.Response(200, request=request, json={"ok": True})

    task_id = "TASK-0297"
    request = OrchestrateRequest(
        task_id=task_id,
        goal="Prepare contract renewal quote for Tailspin Toys (ACC-0297).",
        available_tools=[
            _orchestration_tool(task_id, "crm_get_account"),
            _orchestration_tool(task_id, "subscription_check"),
            _orchestration_tool(task_id, "email_send"),
            _orchestration_tool(task_id, "notification_send"),
            _orchestration_tool(task_id, "audit_log"),
        ],
        constraints=[
            "High-usage accounts get 15% discount",
            "Discounts above 0% require finance team approval",
        ],
        mock_service_url=f"http://127.0.0.1:9090/scenario/{task_id}",
    )

    response = await OrchestrationService(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).orchestrate(request)

    email_step = next(step for step in response.steps_executed if step.tool == "email_send")
    approval_step = next(step for step in response.steps_executed if step.tool == "notification_send")
    audit_step = next(step for step in response.steps_executed if step.tool == "audit_log")

    assert email_step.parameters["variables"] == {"discount": "15%", "plan": "enterprise"}
    assert approval_step.parameters["user_id"] == "finance_approver"
    assert approval_step.parameters["message"] == "Discount approval needed: Tailspin Toys 15% off enterprise"
    assert audit_step.parameters["details"] == {"account_id": "ACC-0297", "discount": 0.15, "plan": "enterprise"}


@pytest.mark.asyncio
async def test_orchestration_service_retries_transient_tool_failures() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request, json={"error": "try again"})
        return httpx.Response(200, request=request, json={"ok": True})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = ToolDefinition.model_validate(
        {
            "name": "audit_log",
            "description": "Audit",
            "endpoint": "http://127.0.0.1:9090/scenario/TASK-3001/audit_log",
            "parameters": [{"name": "action", "type": "string", "description": "Action", "required": True}],
        }
    )
    request = OrchestrateRequest(
        task_id="TASK-3001",
        goal="Run required audit.",
        available_tools=[tool],
        constraints=[],
        mock_service_url="http://127.0.0.1:9090/scenario/TASK-3001",
    )

    response = await OrchestrationService(
        settings=Settings(max_retry_attempts=2, retry_base_delay_seconds=0),
        http_client=http_client,
    ).orchestrate(request)

    assert attempts == 2
    assert response.steps_executed[0].success is True

    await http_client.aclose()


@pytest.mark.asyncio
async def test_orchestration_service_counts_failed_email_as_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        return httpx.Response(500, request=request, json={"error": "down"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = ToolDefinition.model_validate(
        {
            "name": "email_send",
            "description": "Send email",
            "endpoint": "http://127.0.0.1:9090/scenario/TASK-3002/email_send",
            "parameters": [
                {"name": "account_id", "type": "string", "description": "Account", "required": True},
                {"name": "template", "type": "string", "description": "Template", "required": True},
            ],
        }
    )
    request = OrchestrateRequest(
        task_id="TASK-3002",
        goal="Send one follow-up email to ACC-3002.",
        available_tools=[tool],
        constraints=[],
        mock_service_url="http://127.0.0.1:9090/scenario/TASK-3002",
    )

    response = await OrchestrationService(
        settings=Settings(max_retry_attempts=1, retry_base_delay_seconds=0),
        http_client=http_client,
    ).orchestrate(request)

    assert response.steps_executed[0].success is False
    assert response.emails_sent is None
    assert response.emails_skipped == 1
    assert response.skip_reasons == {"tool_failure": 1}

    await http_client.aclose()


@pytest.mark.asyncio
async def test_orchestration_service_rejects_untrusted_private_tool_endpoint() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request, json={"ok": True})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = ToolDefinition.model_validate(
        {
            "name": "audit_log",
            "description": "Audit",
            "endpoint": "http://127.0.0.1:8080/admin",
            "parameters": [{"name": "action", "type": "string", "description": "Action", "required": True}],
        }
    )
    request = OrchestrateRequest(
        task_id="TASK-3003",
        goal="Run required audit.",
        available_tools=[tool],
        constraints=[],
    )

    response = await OrchestrationService(http_client=http_client).orchestrate(request)

    assert calls == 0
    assert response.steps_executed[0].success is False
    assert response.steps_executed[0].result_summary == "Tool endpoint is not allowed."

    await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:9090/scenario/TASK-3004/../../admin",
        "http://127.0.0.1:9090/scenario/TASK-3004/%2e%2e/%2e%2e/admin",
    ],
)
async def test_orchestration_service_rejects_mock_service_path_escape(endpoint: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request, json={"ok": True})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = ToolDefinition.model_validate(
        {
            "name": "audit_log",
            "description": "Audit",
            "endpoint": endpoint,
            "parameters": [{"name": "action", "type": "string", "description": "Action", "required": True}],
        }
    )
    request = OrchestrateRequest(
        task_id="TASK-3004",
        goal="Run required audit.",
        available_tools=[tool],
        constraints=[],
        mock_service_url="http://127.0.0.1:9090/scenario/TASK-3004",
    )

    response = await OrchestrationService(http_client=http_client).orchestrate(request)

    assert calls == 0
    assert response.steps_executed[0].success is False
    assert response.steps_executed[0].result_summary == "Tool endpoint is not allowed."

    await http_client.aclose()
