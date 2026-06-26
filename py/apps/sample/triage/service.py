"""Service layer for Task 1 signal triage."""

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Literal
from typing import Protocol

import httpx
from config import Settings
from model_client import ChatMessage
from model_client import ModelProviderError
from models import Category
from models import MissingInfo
from models import Team
from models import TriageRequest
from models import TriageResponse

from triage.prompts import TRIAGE_SYSTEM_PROMPT
from triage.prompts import build_triage_user_prompt

Priority = Literal["P1", "P2", "P3", "P4"]
logger = logging.getLogger(__name__)

_HARDWARE_TERMS = (
    "terminal",
    "console",
    "workstation",
    "laptop",
    "display",
    "projector",
    "scanner",
    "printer",
    "fabricator",
    "fan",
    "cradle",
    "data-port",
    "data port",
    "device",
    "wrist-comm",
    "comm device",
)
_MODEL_SPEC_TERMS = (
    "dell",
    "latitude",
    "shipos",
    "stationos",
    "windows",
    "macos",
    "ios",
    "android",
    "model",
    "serial",
    "version",
    "build",
    "firmware",
    "patch",
)
_ERROR_TERMS = (
    "error",
    "exception",
    "traceback",
    "alert",
    "alarm",
    "code",
    "access denied",
    "crashloopbackoff",
    "disk full",
    "timeout",
    "expired",
    "failed",
    "failure",
    "warning",
    "red indicator",
)
_TIME_TERMS = (
    "today",
    "yesterday",
    "morning",
    "afternoon",
    "evening",
    "overnight",
    "since",
    "started",
    "last ",
    " at ",
    " am",
    " pm",
    ":",
    "stardate",
)
_RECURRENCE_TERMS = (
    "again",
    "still",
    "keeps",
    "repeated",
    "recurring",
    "every ",
    "intermittent",
    "third time",
    "3rd time",
    "same issue",
)


class TriageService:
    """Classify and route incoming mission signals."""

    def __init__(self, *, settings: Settings | None = None, model_client: "TriageModelClient | None" = None) -> None:
        self._settings = settings or Settings()
        self._model_client = model_client

    async def triage(self, request: TriageRequest) -> TriageResponse:
        """Return a deterministic triage decision for a mission signal."""
        baseline = self._deterministic_triage(request)
        if not self._should_request_model(request, baseline):
            _log_triage_decision(request, baseline, model_result="skipped")
            return baseline

        proposal = await self._model_proposal(request, baseline)
        if proposal is None:
            _log_triage_decision(request, baseline, model_result="unavailable")
            return baseline
        response = _response_from_proposal(request, proposal, baseline)
        _log_triage_decision(
            request,
            response,
            model_result="accepted" if response != baseline else "rejected",
            proposal=proposal,
        )
        return response

    def _deterministic_triage(self, request: TriageRequest) -> TriageResponse:
        text = _combined_text(request)
        category = _category_for(text)
        assigned_team = _team_for(category, text)
        priority = _priority_for(category, text, request.channel)
        needs_escalation = _needs_escalation(category, priority, text, request.channel)
        missing_information = _missing_information_for(category, text, request.attachments)

        return TriageResponse(
            ticket_id=request.ticket_id,
            category=category,
            priority=priority,
            assigned_team=assigned_team,
            needs_escalation=needs_escalation,
            missing_information=missing_information,
            next_best_action=_next_best_action(category, assigned_team, needs_escalation),
            remediation_steps=_remediation_steps(category, assigned_team, missing_information, needs_escalation),
        )

    def _should_request_model(self, request: TriageRequest, baseline: TriageResponse) -> bool:
        if self._model_client is None or not self._model_client.is_configured():
            return False
        text = _combined_text(request)
        if _has_deterministic_guardrail(text, baseline):
            return False
        return _model_request_reason(text, baseline) is not None

    async def _model_proposal(self, request: TriageRequest, baseline: TriageResponse) -> "TriageProposal | None":
        if self._model_client is None:
            return None
        try:
            payload = await self._model_client.complete_json(
                messages=[
                    ChatMessage(role="system", content=TRIAGE_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=build_triage_user_prompt(request, baseline)),
                ],
                model_name=self._settings.model_name_for_path("/triage"),
                temperature=0.0,
                max_tokens=512,
            )
        except (ModelProviderError, httpx.HTTPError, TimeoutError):
            return None
        return _proposal_from_payload(payload)


class TriageModelClient(Protocol):
    """Subset of ModelClient used by the triage service."""

    def is_configured(self) -> bool:
        """Return whether model calls can be made."""
        ...

    async def complete_json(
        self,
        *,
        messages: Sequence[ChatMessage],
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return a strict JSON model response."""
        ...


@dataclass(frozen=True, slots=True)
class TriageProposal:
    """Validated model proposal for a triage response."""

    category: Category
    priority: Priority
    assigned_team: Team
    needs_escalation: bool
    missing_information: list[MissingInfo]
    confidence: float


_CATEGORY_BY_LABEL = {item.value.lower(): item for item in Category}
_TEAM_BY_LABEL = {item.value.lower(): item for item in Team}
_MISSING_INFO_BY_LABEL = {item.value.lower(): item for item in MissingInfo}
_PRIORITIES: set[str] = {"P1", "P2", "P3", "P4"}
_INCIDENT_CATEGORIES = {
    Category.ACCESS,
    Category.HULL,
    Category.COMMS,
    Category.SOFTWARE,
    Category.THREAT,
    Category.DATA,
}
_NON_INCIDENT_MODEL_CATEGORIES = {Category.BRIEFING, Category.NOT_SIGNAL}


def _has_deterministic_guardrail(text: str, baseline: TriageResponse) -> bool:
    if baseline.category == Category.NOT_SIGNAL:
        return True
    if baseline.category == Category.BRIEFING and baseline.priority in {"P3", "P4"}:
        return True
    if baseline.priority == "P1" and baseline.needs_escalation and _must_preserve_p1(text, baseline):
        return True
    if _is_resolved_acknowledgement(text):
        return True
    return _contains_any(text, ("hull breach", "atmosphere leak", "loss of atmosphere", "life support"))


def _model_request_reason(text: str, baseline: TriageResponse) -> str | None:
    if _is_ambiguous_for_model(text):
        return "ambiguous_signal"
    if len(baseline.missing_information) >= 4:
        return "noisy_missing_information"
    if baseline.priority in {"P1", "P2"} and _contains_any(text, ("fyi only", "minor issue", "low priority")):
        return "priority_conflict"
    if _contains_any(text, ("also unrelated", "another issue", "but also", "not sure who handles")):
        return "multi_issue"
    return None


def _is_ambiguous_for_model(text: str) -> bool:
    category_hits = sum(
        (
            _is_threat_signal(text),
            _is_access_signal(text),
            _is_telemetry_signal(text),
            _is_comms_signal(text),
            _is_software_signal(text),
            _is_hardware_signal(text),
        )
    )
    return category_hits >= 2 or _contains_any(
        text,
        (
            "not sure",
            "maybe",
            "unclear",
            "fwd:",
            "forwarding this",
            "re:",
            "same issue",
            "following up",
            "but also",
            "another issue",
            "misclassified",
            "miscategorized",
        ),
    )


def _proposal_from_payload(payload: dict[str, Any]) -> TriageProposal | None:
    category = _enum_from_payload(payload.get("category"), _CATEGORY_BY_LABEL)
    priority = _priority_from_payload(payload.get("priority"))
    assigned_team = _enum_from_payload(payload.get("assigned_team"), _TEAM_BY_LABEL)
    needs_escalation = payload.get("needs_escalation")
    missing_information = _missing_info_from_payload(payload.get("missing_information"))
    confidence = _confidence_from_payload(payload.get("confidence"))

    if (
        category is None
        or priority is None
        or assigned_team is None
        or not isinstance(needs_escalation, bool)
        or missing_information is None
        or confidence < 0.55
    ):
        return None

    return TriageProposal(
        category=category,
        priority=priority,
        assigned_team=assigned_team,
        needs_escalation=needs_escalation,
        missing_information=missing_information,
        confidence=confidence,
    )


def _enum_from_payload[T](value: object, labels: dict[str, T]) -> T | None:
    if not isinstance(value, str):
        return None
    return labels.get(value.strip().lower())


def _priority_from_payload(value: object) -> Priority | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if normalized == "P1":
        return "P1"
    if normalized == "P2":
        return "P2"
    if normalized == "P3":
        return "P3"
    if normalized == "P4":
        return "P4"
    return None


def _missing_info_from_payload(value: object) -> list[MissingInfo] | None:
    if not isinstance(value, list):
        return None
    missing: list[MissingInfo] = []
    for item in value:
        label = _enum_from_payload(item, _MISSING_INFO_BY_LABEL)
        if label is None:
            return None
        if label not in missing:
            missing.append(label)
    return missing[:8]


def _confidence_from_payload(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(min(float(value), 1.0), 0.0)
    return 0.0


def _response_from_proposal(
    request: TriageRequest, proposal: TriageProposal, baseline: TriageResponse
) -> TriageResponse:
    text = _combined_text(request)
    if _has_deterministic_guardrail(text, baseline):
        return baseline
    if (
        baseline.category in _INCIDENT_CATEGORIES
        and proposal.category in _NON_INCIDENT_MODEL_CATEGORIES
        and not _is_safe_model_non_incident_downgrade(text, baseline, proposal)
    ):
        return baseline
    if proposal.category == Category.NOT_SIGNAL and baseline.category != Category.NOT_SIGNAL:
        return baseline
    if baseline.priority == "P1" and proposal.priority != "P1" and _must_preserve_p1(text, baseline):
        return baseline

    category = proposal.category
    team = proposal.assigned_team
    priority = proposal.priority
    needs_escalation = proposal.needs_escalation
    missing_information = proposal.missing_information

    if category not in {Category.NOT_SIGNAL, Category.BRIEFING} and team == Team.NONE:
        team = _team_for(category, text)

    if _contains_any(text, ("hull breach", "atmosphere leak", "loss of atmosphere", "life support")):
        category = Category.HULL
        team = Team.SYSTEMS
        priority = "P1"
        needs_escalation = True
    if category == Category.THREAT and _contains_any(
        text, ("credential capture", "phishing", "malware", "exfiltration")
    ):
        team = Team.THREAT
        needs_escalation = needs_escalation or priority == "P1"

    return TriageResponse(
        ticket_id=request.ticket_id,
        category=category,
        priority=priority,
        assigned_team=team,
        needs_escalation=needs_escalation,
        missing_information=missing_information,
        next_best_action=_next_best_action(category, team, needs_escalation),
        remediation_steps=_remediation_steps(category, team, missing_information, needs_escalation),
    )


def _is_safe_model_non_incident_downgrade(text: str, baseline: TriageResponse, proposal: TriageProposal) -> bool:
    return (
        proposal.category == Category.BRIEFING
        and baseline.priority in {"P3", "P4"}
        and not baseline.needs_escalation
        and proposal.confidence >= 0.95
        and _is_mission_briefing_request(text)
    )


def _must_preserve_p1(text: str, baseline: TriageResponse) -> bool:
    if baseline.category == Category.THREAT and _contains_any(
        text,
        (
            "credential capture",
            "credential theft",
            "exfiltration",
            "compromised",
            "malware",
            "phishing",
            "hidden admin",
            "wiped logs",
            "external ip",
        ),
    ):
        return True
    return _contains_any(
        text,
        (
            "hull breach",
            "atmosphere leak",
            "loss of atmosphere",
            "life support",
            "entire vessel",
            "widespread",
            "critical deep-space",
        ),
    )


def _combined_text(request: TriageRequest) -> str:
    return " ".join(
        (
            request.subject,
            request.description,
            request.reporter.department,
        )
    ).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _contains_word_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


def _category_for(text: str) -> Category:
    lead = text[:300]
    if _is_resolved_acknowledgement(text):
        return Category.NOT_SIGNAL
    if _is_concrete_hull_safety_incident(text):
        return Category.HULL
    if _is_vendor_outreach(text):
        return Category.NOT_SIGNAL
    if _contains_any(lead, ("hull breach", "atmosphere leak", "loss of atmosphere", "life support")):
        return Category.HULL
    if _contains_any(lead, ("crew member transfer", "new crew member", "full setup needed")):
        return Category.BRIEFING
    if _contains_any(text, ("need a list", "current inventory", "devices assigned to my division")):
        return Category.BRIEFING
    if _contains_any(text, ("can i use", "am i allowed")) and _contains_any(
        text, ("personal data", "data stick", "thumb drive", "duty terminal")
    ):
        return Category.BRIEFING
    if _is_not_mission_signal(text):
        return Category.NOT_SIGNAL
    if _is_mission_briefing_request(text):
        return Category.BRIEFING
    if _contains_any(
        lead,
        (
            "containment breach",
            "security breach",
            "data breach",
            "suspicious",
            "voice synthesis",
            "monitor crew",
            "credential capture",
            "phishing alert",
            "phishing detected",
            "malware",
            "tls certificate",
            "certificate expired",
            "cert expired",
            "hidden admin",
            "wiped logs",
            "external ip",
        ),
    ):
        return Category.THREAT
    if _contains_any(text, ("hidden admin", "wiped logs", "wipe event logs", "external ip")) and _contains_any(
        text, ("alert", "detected", "sentinel", "security", "suspicious")
    ):
        return Category.THREAT
    if _contains_any(
        lead, ("data core", "data bank", "data feed", "storage", "nav report", "shared data", "analytics node")
    ):
        return Category.DATA
    if _contains_any(
        lead,
        (
            "biometric",
            "mfbv",
            "multi-factor",
            "keycard",
            "badge",
            "hera system",
            "can't authenticate",
            "cannot authenticate",
            "authentication token",
            "auth token",
        ),
    ):
        return Category.ACCESS
    if _contains_any(
        lead,
        ("video playback", "training portal", "license", "hermes", "janus", "iris", "flightos", "citrix", "subcomm"),
    ):
        return Category.SOFTWARE
    if _contains_any(lead, ("subspace", "relay", "network", "transmission", "ndr", "holo-call", "holo call")):
        return Category.COMMS
    if _contains_any(
        lead,
        (
            "data-port",
            "data port",
            "display panel",
            "holographic display",
            "projector",
            "scanner",
            "terminal fan",
            "terminal running",
            "workstation console",
            "interface cradle",
        ),
    ):
        return Category.HULL
    if _contains_any(lead, ("iris", "flightos", "mercury", "hermes", "janus", "citrix", "license", "portal")):
        return Category.SOFTWARE
    if _is_threat_signal(text):
        return Category.THREAT
    if _is_telemetry_signal(text):
        return Category.DATA
    if _is_access_signal(text):
        return Category.ACCESS
    if _is_software_signal(text):
        return Category.SOFTWARE
    if _is_comms_signal(text):
        return Category.COMMS
    if _is_hardware_signal(text):
        return Category.HULL
    if _is_benign_acknowledgement(text):
        return Category.NOT_SIGNAL
    return Category.SOFTWARE


def _is_not_mission_signal(text: str) -> bool:
    if _is_vendor_outreach(text) or _contains_any(
        text,
        (
            "automated response",
            "out of office",
            "cryo-sleep",
            "personal comms relay",
            "personal home",
            "family's entertainment",
            "not station-issued",
            "docking bay slot",
            "reserved over my docking",
        ),
    ):
        return True
    dangerous_operation = _contains_any(
        text,
        (
            "disable real-time av",
            "disable av",
            "export saved credentials",
            "hidden admin",
            "external ip",
            "wipe station event logs",
            "wipe event logs",
            "wiping logs",
        ),
    )
    if dangerous_operation and _contains_any(text, ("alert", "detected", "sentinel", "security", "suspicious")):
        return False
    if dangerous_operation and _contains_any(
        text, ("need", "write", "build", "create", "request", "script", "template")
    ):
        return True
    if (
        "clone" in text
        and "captures credentials" in text
        and _contains_any(text, ("need", "build", "create", "request", "template", "training", "drill"))
    ):
        return True
    if "bypass execution policy" in text:
        return True
    if "credential capture" in text:
        if _contains_any(text, ("alert", "detected", "dashboard", "phishing", "suspicious")):
            return False
        return _contains_any(text, ("need", "build", "create", "request", "template", "training", "drill"))
    return False


def _is_vendor_outreach(text: str) -> bool:
    if _contains_any(
        text,
        (
            "credential capture",
            "malware",
            "phishing",
            "suspicious",
            "hull breach",
            "atmosphere leak",
            "loss of atmosphere",
            "life support",
            "containment breach",
            "security breach",
            "data breach",
            "compromised",
            "exfiltration",
            "credential theft",
            "hidden admin",
            "wiped logs",
            "wipe event logs",
            "external ip",
        ),
    ) and _contains_any(text, ("alert", "detected", "active", "alarm", "outage", "failed", "failure")):
        return False
    return _contains_any(
        text,
        (
            "partnership opportunity",
            "quick holo-demo",
            "complimentary defense assessment",
            "vendor offering",
            "sales outreach",
            "would you have 15 minutes",
            "offering a demo",
        ),
    )


def _is_concrete_hull_safety_incident(text: str) -> bool:
    lead = text[:300]
    if _contains_any(lead, ("atmosphere leak", "loss of atmosphere", "life support")):
        return True
    if "hull breach" not in lead:
        return False
    return _contains_any(
        lead,
        (
            "current hull breach",
            "ongoing hull breach",
            "active hull breach",
            "hull breach near",
            "hull breach after",
            "hull breach alarm",
            "hull breach detected",
            "hull breach reported",
            "hull breach and",
        ),
    )


def _is_resolved_acknowledgement(text: str) -> bool:
    return _contains_any(
        text,
        (
            "got it working",
            "figured it out",
            "working perfectly now",
            "resolved now",
            "issue is resolved",
        ),
    ) and not _contains_any(
        text,
        (
            "not really",
            "not resolved",
            "still broken",
            "still failing",
            "still down",
            "still blocked",
            "access denied",
        ),
    )


def _is_benign_acknowledgement(text: str) -> bool:
    return _contains_any(
        text, ("thanks", "thank you", "got it working", "figured it out", "resolved")
    ) and not _contains_any(
        text,
        (
            "still",
            "not really",
            "not resolved",
            "error",
            "failed",
            "failing",
            "can't",
            "cannot",
            "broken",
            "down",
            "expired",
            "access denied",
        ),
    )


def _is_mission_briefing_request(text: str) -> bool:
    if _contains_word_any(text, ("down",)) or _contains_any(
        text,
        (
            "failing",
            "failure",
            "broken",
            "error",
            "disconnecting",
            "cannot maintain",
            "can't maintain",
            "access denied",
            "suspicious",
            "alert",
        ),
    ):
        return False
    return _contains_any(
        text,
        (
            "can i ",
            "how do i",
            "am i allowed",
            "is there a catalog",
            "approved software",
            "quick question",
            "need a list",
            "current inventory",
            "status update",
            "checking in on",
            "following up on signal",
            "full setup needed",
            "new crew member",
            "crew member transfer",
            "prepare a routine mission briefing",
            "briefing request",
        ),
    )


def _is_threat_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            "suspicious",
            "lateral movement",
            "purview",
            "pii",
            "highly confidential",
            "data classification",
            "security logs",
            "access records",
            "inspection trail",
            "monitor crew",
            "voice synthesis",
            "cloning any speaker",
            "face-swap",
            "malware",
            "phishing",
            "hostile",
            "certificate",
            "credential capture",
        ),
    ) or _contains_any(text, ("security breach", "data breach", "sentinel for identity", "sentinel identity"))


def _is_access_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            "authenticate",
            "can't authenticate",
            "cannot authenticate",
            "authentication",
            "auth failures",
            "keycard",
            "badge",
            "biometric",
            "bioscan",
            "mfbv",
            "mfa",
            "multi-factor",
            "credentials expired",
            "hera system",
            "airlock access",
            "access code",
            "sign-in",
        ),
    ) or ("access denied" in text and _contains_any(text, ("authenticate", "authentication", "credentials", "sign-in")))


def _is_telemetry_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            "data vault",
            "data core",
            "data bank",
            "shared data",
            "archive",
            "storage",
            "disk utilization",
            "disk space",
            "filesrv",
            "sql",
            "data feed",
            "json response",
            "reporting dashboard",
            "source system",
            "nav report",
            "market data api",
            "telemetry",
            "database",
        ),
    )


def _is_comms_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            "subspace",
            "relay",
            "network",
            "communications",
            "dns",
            "beacon",
            "signal ping",
            "ip address",
            "node address",
            "gateway",
            "tunnel",
            "holo-call",
            "holo call",
            "connection drops",
            "disconnecting",
            "latency",
            "packets",
            "transmission",
            "messaging performance",
        ),
    )


def _is_software_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            " app ",
            "application",
            "portal",
            "citrix",
            "iris",
            "flightos",
            "subcomm",
            "mercury",
            "hermes",
            "janus",
            "payment service",
            "kubectl",
            "crashloopbackoff",
            "regex",
            "search/filter",
            "video playback",
            "auto-sort",
            "calendar",
            "license",
            "integration",
            "trajectory plotter",
            "share screen",
            "screen share",
        ),
    )


def _is_hardware_signal(text: str) -> bool:
    return _contains_any(text, _HARDWARE_TERMS) or _contains_any(
        text,
        (
            "won't power on",
            "not working",
            "running slow",
            "fan",
            "display panel",
            "blank pages",
            "flickering",
            "materializes",
            "data-port interface",
        ),
    )


def _team_for(category: Category, text: str) -> Team:
    if category == Category.NOT_SIGNAL:
        return Team.NONE
    if category == Category.BRIEFING:
        if _contains_any(text, ("new crew", "crew member transfer", "onboarding", "departure")):
            return Team.IDENTITY
        if _contains_any(text, ("book", "booking")):
            return Team.SOFTWARE
        return Team.NONE
    if category == Category.ACCESS:
        return Team.IDENTITY
    if category == Category.THREAT:
        return Team.THREAT
    if category == Category.DATA:
        return Team.TELEMETRY
    if category == Category.COMMS:
        return Team.COMMS
    if category == Category.SOFTWARE:
        if _contains_any(text, ("new workstation",)) and not _contains_any(
            text,
            ("flightos", "mercury", "hermes", "janus", "citrix", "portal", "screen share", "share screen", "policy"),
        ):
            return Team.SYSTEMS
        return Team.SOFTWARE
    if category == Category.HULL:
        if _contains_any(
            text[:300],
            ("data-port", "data port", "display panel", "projector", "scanner", "terminal fan", "terminal running"),
        ):
            return Team.SYSTEMS
        if _contains_any(
            text, ("node address", "subspace relay", "network outage", "network unreachable")
        ) or re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            return Team.COMMS
        return Team.SYSTEMS
    return Team.SYSTEMS


def _priority_for(category: Category, text: str, channel: str) -> Priority:
    if category == Category.NOT_SIGNAL:
        return "P4"
    if category == Category.ACCESS and _contains_any(text, ("off-ship", "allied outpost")):
        return "P4"
    if category == Category.ACCESS and _contains_any(text, ("eva", "away mission")):
        return "P3"
    if _contains_any(text, ("hull breach", "atmosphere leak", "loss of atmosphere", "life support")):
        return "P1"
    if _contains_any(
        text,
        (
            "hull breach",
            "atmosphere leak",
            "loss of atmosphere",
            "life support",
            "entire vessel",
            "widespread",
            "data protection regulation",
            "deletion of all security logs",
            "containment breach",
            "certificate expiring",
            "cert expiring",
            "certificate expired",
            "cert expired",
            "externally exposed",
            "sensitive records",
            "compromised",
            "exfiltration",
            "classified archive",
            "classified data",
            "credential theft",
            "credential capture",
            "hidden admin",
            "wiped logs",
            "wipe event logs",
            "external ip",
            "critical deep-space",
            "accessibility",
        ),
    ):
        return "P1"
    if "fleet admiral" in text and _contains_any(text, ("happening now", "presenting", "critical")):
        return "P1"
    if channel == "emergency_beacon" and category in {Category.HULL, Category.THREAT, Category.ACCESS}:
        return "P1"
    if "fyi only" in text:
        return "P4"
    if category not in {Category.BRIEFING, Category.NOT_SIGNAL} and _contains_any(
        text,
        (
            "can't maintain",
            "cannot maintain",
            "no workaround",
            "3rd time",
            "third time",
            "still not resolved",
            "critically low",
            "98 percent",
            "production",
            "policy violation",
            "pii",
            "payment service",
            "fleet admiral",
            "still broken",
            "external display",
            "data discrepancies",
        ),
    ):
        return "P2"
    if (
        category not in {Category.BRIEFING, Category.NOT_SIGNAL}
        and _contains_word_any(text, ("down",))
        and not _contains_any(text, ("mbps down", "download"))
    ):
        return "P2"
    if category in {Category.BRIEFING, Category.NOT_SIGNAL} and _contains_any(
        text, ("low priority", "minor issue", "quick question", "can i ", "am i allowed")
    ):
        return "P4"
    return "P3"


def _needs_escalation(category: Category, priority: str, text: str, channel: str) -> bool:
    if channel == "emergency_beacon" and category in {Category.HULL, Category.THREAT, Category.ACCESS}:
        return True
    if priority == "P1" and (
        category == Category.THREAT
        or _contains_any(
            text,
            (
                "hull breach",
                "life support",
                "atmosphere leak",
                "loss of atmosphere",
                "entire vessel",
                "widespread",
                "critical deep-space",
                "fleet admiral",
                "data protection regulation",
            ),
        )
    ):
        return True
    if category == Category.THREAT and _has_high_risk_threat_context(text):
        return True
    if category == Category.THREAT and _contains_any(
        text, ("certificate expiring", "cert expiring", "certificate expired", "cert expired", "tls cert")
    ):
        return True
    if category == Category.NOT_SIGNAL and _contains_any(
        text,
        (
            "bypass execution policy",
            "captures credentials",
            "credential capture",
            "signal-spoofing",
            "disable real-time av",
            "disable av",
            "export saved credentials",
            "hidden admin",
            "external ip",
            "wipe station event logs",
            "wipe event logs",
            "wiping logs",
        ),
    ):
        return True
    if "fleet admiral" in text and _contains_any(text, ("happening now", "presenting", "critical")):
        return True
    if "regulatory" in text:
        return True
    if "c-suite" in text and _contains_any(text, ("auto-alert", "critical", "production", "data core")):
        return True
    if category == Category.ACCESS and _contains_any(text, ("eva", "away mission")):
        return True
    return _contains_any(
        text,
        (
            "prior security breach",
            "same pattern as before",
            "unresolved incident",
            "still blocked",
            "still down",
            "still failing in production",
        ),
    ) and category in {Category.ACCESS, Category.THREAT, Category.DATA}


def _has_high_risk_threat_context(text: str) -> bool:
    if _contains_any(
        text,
        (
            "clone",
            "monitor crew",
            "voice synthesis",
            "face-swap",
            "lateral movement",
            "compromised",
            "exfiltration",
            "credential theft",
            "suspicious access",
            "hidden admin",
            "wiped logs",
            "wipe event logs",
            "external ip",
        ),
    ):
        return True
    return _contains_any(
        text,
        (
            "security logs",
            "confidential:",
            "classified archive",
            "classified data",
            "breach",
        ),
    ) and _contains_any(
        text,
        (
            "active",
            "alert",
            "compromised",
            "containment",
            "detected",
            "exfiltration",
            "external",
            "suspicious",
            "unauthorized",
            "wiped",
        ),
    )


def _missing_information_for(category: Category, text: str, attachments: list[str]) -> list[MissingInfo]:
    if category == Category.NOT_SIGNAL:
        return []
    if (
        category == Category.SOFTWARE
        and _contains_any(text, ("share screen", "screen share"))
        and _contains_any(text, ("blocked by", "policy"))
    ):
        return []

    missing: list[MissingInfo] = []

    def add_if(condition: bool, label: MissingInfo) -> None:
        if condition and label not in missing:
            missing.append(label)

    has_attachment = bool(attachments) or (
        _contains_any(text, ("attached", "screenshot", "logs below", "diagnostic logs", "csv"))
        and not _contains_any(text, ("didn't capture", "did not capture", "no screenshot", "no logs"))
    )
    has_error = _has_anomaly_readout(text)
    has_specs = _contains_any(text, _MODEL_SPEC_TERMS) or re.search(r"\bv\d+(?:\.\d+)+\b", text) is not None
    has_steps = _contains_any(
        text,
        (
            "when i",
            "i tried",
            "tried ",
            "after ",
            "restarting",
            "re-enrolled",
            "ran it",
            "ran a ",
            "diagnostic sweep",
            "from my",
        ),
    )

    add_if(_contains_any(text, ("callback", "call back", "forwarding this from")), MissingInfo.CREW_CONTACT)
    add_if(
        category == Category.COMMS
        and _contains_any(text, ("may be nothing", "haven't complained", "impact is unclear")),
        MissingInfo.MISSION_IMPACT,
    )
    if category in {Category.HULL, Category.SOFTWARE, Category.ACCESS, Category.COMMS}:
        add_if(
            (
                _contains_any(text, _HARDWARE_TERMS)
                or (
                    category == Category.ACCESS
                    and _contains_any(text, ("badge", "keycard", "access card", "comm device", "communicator"))
                )
            )
            and not has_specs,
            MissingInfo.MODULE_SPECS,
        )
    if category == Category.SOFTWARE and _contains_any(
        text, ("app", "portal", "citrix", "mercury", "hermes", "janus", "iris")
    ):
        add_if("patch" not in text and "version" not in text and "build" not in text, MissingInfo.SOFTWARE_VERSION)
    if category == Category.ACCESS and (
        _contains_word_any(text, ("app",)) or _contains_any(text, ("authenticator", "comm device", "communicator"))
    ):
        add_if("patch" not in text and "version" not in text and "build" not in text, MissingInfo.SOFTWARE_VERSION)
    if category in {Category.COMMS, Category.DATA}:
        add_if(
            not _contains_any(text, ("deck", "sector", "gateway", "node", "vlan", "ip address", "hostname", "server"))
            or _contains_any(
                text, ("not sure exactly which deck", "not sure which deck", "not sure which network sector")
            ),
            MissingInfo.SECTOR_COORDINATES,
        )
    if category == Category.ACCESS:
        add_if(
            not _contains_any(
                text,
                (
                    "biometric",
                    "badge",
                    "keycard",
                    "mfa",
                    "mfbv",
                    "password",
                    "backup code",
                    "sso",
                    "sign-in",
                    "hera",
                    "iam",
                    "access policy",
                    "auth token",
                    "authentication token",
                ),
            ),
            MissingInfo.BIOMETRIC_METHOD,
        )
    if category == Category.BRIEFING:
        add_if(
            _contains_any(text, ("data stick", "thumb drive", "duty terminal")) and not has_specs,
            MissingInfo.MODULE_SPECS,
        )
        add_if(
            _contains_any(text, ("inventory", "devices assigned", "assigned to my division")), MissingInfo.AFFECTED_CREW
        )
    if category == Category.THREAT:
        add_if(
            not has_attachment
            and _contains_any(text, ("alert", "dashboard", "flagged", "suspicious"))
            and not _contains_any(text, ("forwarded message", "from:", "http://", "https://", "haven't clicked")),
            MissingInfo.SENSOR_LOG_OR_CAPTURE,
        )
        add_if(
            ("policy" in text or _contains_any(text, ("certificate", "cert ", "auto-renewal", "auto renewal")))
            and not _contains_any(text, ("classification", "rule", "role", "permission")),
            MissingInfo.SYSTEM_CONFIGURATION,
        )
        add_if(
            _contains_any(text, ("production api", "api gateway", "fleet integrations", "external fleet")),
            MissingInfo.HABITAT_CONDITIONS,
        )

    add_if(
        not has_error and category in {Category.HULL, Category.SOFTWARE, Category.COMMS, Category.DATA},
        MissingInfo.ANOMALY_READOUT,
    )
    add_if(
        not has_steps and category in {Category.HULL, Category.SOFTWARE, Category.COMMS},
        MissingInfo.SEQUENCE_TO_REPRODUCE,
    )
    add_if(
        category in {Category.COMMS, Category.DATA, Category.THREAT}
        and not _contains_any(
            text,
            (
                "subspace",
                "relay",
                "network",
                "gateway",
                "domain",
                "data core",
                "data bank",
                "storage",
                "archive",
                "server",
                "api",
                "site",
                "service",
            ),
        ),
        MissingInfo.AFFECTED_SUBSYSTEM,
    )
    add_if(
        category in {Category.ACCESS, Category.DATA, Category.HULL} and not _has_affected_crew_context(text),
        MissingInfo.AFFECTED_CREW,
    )
    add_if(
        category in {Category.COMMS, Category.DATA, Category.SOFTWARE}
        and not _contains_any(
            text, ("prod", "production", "deck", "sector", "gateway", "node", "station core", "flight")
        ),
        MissingInfo.HABITAT_CONDITIONS,
    )
    add_if(
        category in {Category.COMMS, Category.DATA, Category.SOFTWARE, Category.HULL}
        and not _contains_any(text, _TIME_TERMS),
        MissingInfo.STARDATE,
    )
    add_if(category == Category.HULL and not _contains_any(text, _RECURRENCE_TERMS), MissingInfo.RECURRENCE_PATTERN)
    add_if(
        (
            _contains_any(
                text,
                (
                    "following up",
                    "checking in on signal report",
                    "same issue",
                    "same pattern",
                    "prior ticket",
                    "prior signal",
                    "prior security breach signal report",
                    "previous signal report",
                    "can't find the number",
                    "opened last",
                    "submitted last",
                ),
            )
        )
        and "sig-" not in text,
        MissingInfo.PREVIOUS_SIGNAL_ID,
    )

    return missing[:6]


def _has_anomaly_readout(text: str) -> bool:
    if _contains_any(
        text,
        (
            "exception",
            "traceback",
            "alert",
            "alarm",
            "access denied",
            "crashloopbackoff",
            "disk full",
            "timeout",
            "expired",
            "failed",
            "failure",
            "warning",
            "red indicator",
            "white screen",
            "won't power on",
            "wont power on",
            "blocked by",
            "flickering",
            "error code",
            "error message",
            "malformed json",
            "sample response",
            "comparison table",
            "csv",
        ),
    ):
        return True
    return re.search(r"\b\d{3,5}\s+errors?\b", text) is not None


def _has_affected_crew_context(text: str) -> bool:
    return _contains_any(
        text,
        (
            "all crew",
            "everyone",
            "users",
            "other people",
            "team",
            "division",
            "crew member",
            "fleet admiral",
            "delegation",
            "commander",
            " my ",
            " i'm ",
            " i am ",
            " i ",
        ),
    )


def _next_best_action(category: Category, team: Team, needs_escalation: bool) -> str:
    if category == Category.NOT_SIGNAL:
        if needs_escalation:
            return (
                "Reject the unsafe non-mission request and escalate it to Threat Response Command for security review."
            )
        return (
            "Do not route as a mission incident; close or redirect the request to the appropriate non-mission channel."
        )
    if category == Category.BRIEFING:
        return "Answer the request or route it to the policy and briefing owner without opening an incident response."
    escalation = " and notify command escalation" if needs_escalation else ""
    return f"Route the signal to {team.value}{escalation} with the captured symptoms and missing-information checklist."


def _remediation_steps(
    category: Category, team: Team, missing_information: list[MissingInfo], needs_escalation: bool
) -> list[str]:
    if category == Category.NOT_SIGNAL:
        if needs_escalation:
            return [
                "Confirm the request is outside mission-signal scope.",
                "Do not execute scripts, credential-capture steps, or unsafe operational instructions.",
                "Escalate unsafe or policy-violating requests to Threat Response Command for review.",
            ]
        return [
            "Confirm the request is outside mission-signal scope.",
            "Close or redirect without executing unsafe instructions.",
        ]
    if category == Category.BRIEFING:
        return [
            "Provide the requested policy or inventory guidance.",
            "Ask for any missing context before taking operational action.",
        ]

    steps = [
        f"Assign ownership to {team.value}.",
        "Validate the reported impact, affected scope, and latest symptoms.",
    ]
    if missing_information:
        missing = ", ".join(item.value for item in missing_information)
        steps.append(f"Collect missing information: {missing}.")
    steps.append("Apply the team runbook and confirm recovery with the reporter.")
    return steps


def _log_triage_decision(
    request: TriageRequest,
    response: TriageResponse,
    *,
    model_result: str,
    proposal: TriageProposal | None = None,
) -> None:
    proposal_category = proposal.category.value if proposal is not None else ""
    proposal_priority = proposal.priority if proposal is not None else ""
    proposal_team = proposal.assigned_team.value if proposal is not None else ""
    logger.warning(
        "telemetry=true event=triage_decision ticket_hash=%s category=%s priority=%s assigned_team=%s "
        "needs_escalation=%s missing_count=%d model_result=%s proposal_category=%s proposal_priority=%s proposal_team=%s",
        _short_hash(request.ticket_id),
        _log_value(response.category.value),
        response.priority,
        _log_value(response.assigned_team.value),
        response.needs_escalation,
        len(response.missing_information),
        model_result,
        _log_value(proposal_category),
        proposal_priority,
        _log_value(proposal_team),
    )


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _log_value(value: str) -> str:
    return value.replace(" ", "_").replace("&", "and")
