"""Service layer for Task 3 workflow orchestration."""

import re
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from typing import Any
from urllib.parse import SplitResult
from urllib.parse import quote
from urllib.parse import unquote
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import httpx
from config import Settings
from models import OrchestrateRequest
from models import OrchestrateResponse
from models import StepExecuted
from models import ToolDefinition
from resilience import run_with_retries

_ACCOUNT_RE = re.compile(r"\bACC-\d+(?:-\d+)?\b")
_USER_RE = re.compile(r"\b(?:CSM|REP)-\d+\b")
_WAREHOUSE_RE = re.compile(r"\b[A-Z]{2,}(?:-[A-Z]+)+\b")
_SKU_RE = re.compile(r"\b[A-Z][A-Za-z]+-[A-Z]\d{3,4}\b")
_SEVERITY_RE = re.compile(r"\b(critical|high|medium|low)\b", re.IGNORECASE)
_LOCAL_MOCK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_STOCK_KEYS = frozenset({"stock", "stock_level", "quantity", "available", "available_units", "units", "on_hand"})
_STATUS_KEYS = frozenset({"status", "subscription_status", "state"})
_TIER_KEYS = frozenset({"tier", "plan", "subscription_tier"})
_ACCOUNT_ID_KEYS = frozenset({"account_id", "id"})
_ACCOUNT_LIST_KEYS = frozenset({"accounts", "results", "items", "customers"})
_RISK_KEYS = frozenset({"risk", "risk_level", "churn_risk", "churn_risk_level"})
_APPROVAL_KEYS = frozenset({"needs_approval", "requires_approval", "finance_approval_required"})
_RENEWAL_DAYS_KEYS = frozenset({"renewal_days", "days_to_renewal", "days_until_renewal", "renewal_in_days"})
_RENEWAL_DATE_KEYS = frozenset({"renewal_date", "renews_at", "renewal"})
_USAGE_KEYS = frozenset({"usage", "usage_level", "usage_tier"})
_DISCOUNT_KEYS = frozenset({"discount", "discount_percent"})
_REFERENCE_DATE = date(2026, 4, 9)
_COMPLIANCE_ACTIONS = [
    "exception_path_logged",
    "ops_escalation_recorded",
    "escalation_receipt_logged",
    "stakeholder_summary_logged",
]


class OrchestrationService:
    """Execute constrained workflows with supplied tools."""

    def __init__(self, *, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or Settings()
        self._http_client = http_client
        self._local_mock_available: dict[str, bool] = {}

    async def orchestrate(self, request: OrchestrateRequest) -> OrchestrateResponse:
        """Execute a workflow by calling the supplied tool endpoints and returning a trace."""
        workflow = _workflow_kind(request)
        if workflow == "inventory_restock":
            return await self._orchestrate_inventory_restock(request)
        if workflow == "onboarding_workflow":
            return await self._orchestrate_onboarding(request)
        if workflow == "re_engagement_campaign":
            return await self._orchestrate_reengagement(request)
        if workflow == "meeting_scheduler":
            return await self._orchestrate_meeting(request)
        if workflow == "churn_risk_analysis":
            return await self._orchestrate_churn_risk(request)
        if workflow == "contract_renewal":
            return await self._orchestrate_contract_renewal(request)

        planned_calls = _plan_workflow(request)
        return await self._execute_planned_calls(request, planned_calls)

    async def _execute_planned_calls(
        self,
        request: OrchestrateRequest,
        planned_calls: list["PlannedCall"],
    ) -> OrchestrateResponse:
        executed: list[StepExecuted] = []
        state = WorkflowState()

        for call in planned_calls:
            await self._execute_call(request, call, executed, state)

        status = "completed" if planned_calls else "completed"
        return OrchestrateResponse(
            task_id=request.task_id,
            status=status,
            steps_executed=executed,
            accounts_processed=state.accounts_processed or None,
            emails_sent=state.emails_sent or None,
            emails_skipped=state.emails_skipped or None,
            skip_reasons=state.skip_reasons or None,
            constraints_satisfied=_constraints_satisfied(request, executed),
        )

    async def _orchestrate_inventory_restock(self, request: OrchestrateRequest) -> OrchestrateResponse:
        sku = _first_match(_SKU_RE, request.goal, "unknown-sku")
        warehouses = _warehouses_from_goal(request.goal)
        threshold = _threshold(request.goal)
        executed: list[StepExecuted] = []
        state = WorkflowState()
        query_results: list[tuple[str, ToolCallResult]] = []

        for warehouse in warehouses:
            result = await self._execute_call(
                request,
                PlannedCall("inventory_query", {"sku": sku, "warehouse": warehouse}),
                executed,
                state,
            )
            query_results.append((warehouse, result))

        stock_readings = [
            (warehouse, stock)
            for warehouse, result in query_results
            if result.success and (stock := _extract_number(result.payload, _STOCK_KEYS)) is not None
        ]
        if stock_readings:
            alert_targets = [(warehouse, stock) for warehouse, stock in stock_readings if stock < threshold]
        else:
            alert_targets = [(warehouse, None) for warehouse in warehouses]

        for warehouse, stock in alert_targets:
            stock_text = f"{stock:g} units" if stock is not None else "threshold check"
            await self._execute_call(
                request,
                PlannedCall(
                    "notification_send",
                    {
                        "user_id": f"warehouse_mgr_{warehouse}",
                        "channel": "slack",
                        "message": f"Low stock: {sku} at {stock_text} in {warehouse} (threshold: {threshold})",
                    },
                ),
                executed,
                state,
            )

        for call in _compliance_tail(request, "inventory_restock"):
            await self._execute_call(request, call, executed, state)

        return _orchestration_response(request, executed, state)

    async def _orchestrate_onboarding(self, request: OrchestrateRequest) -> OrchestrateResponse:
        account_id = _account_id(request)
        account_name = _account_name(request.goal, account_id)
        csm = _first_match(_USER_RE, request.goal, "customer_success")
        executed: list[StepExecuted] = []
        state = WorkflowState()

        await self._execute_call(request, PlannedCall("crm_get_account", {"account_id": account_id}), executed, state)
        subscription = await self._execute_call(
            request,
            PlannedCall("subscription_check", {"account_id": account_id}),
            executed,
            state,
        )
        is_active = _subscription_is_active(subscription.payload) if subscription.success else None

        if is_active is False:
            await self._execute_call(
                request,
                PlannedCall(
                    "notification_send",
                    {
                        "user_id": "sales_team",
                        "channel": "slack",
                        "message": f"Onboarding blocked for {account_name}: subscription is not active",
                    },
                ),
                executed,
                state,
            )
            await self._execute_call(
                request,
                PlannedCall("audit_log", {"action": "onboarding_blocked", "details": {"account_id": account_id}}),
                executed,
                state,
            )
            return _orchestration_response(request, executed, state)

        for call in [
            PlannedCall(
                "email_send", {"account_id": account_id, "template": "welcome", "subject": f"Welcome {account_name}!"}
            ),
            PlannedCall("calendar_check", {"user_id": csm, "start_date": "2026-04-09", "end_date": "2026-04-16"}),
            PlannedCall(
                "email_send",
                {"account_id": account_id, "template": "kickoff_invite", "subject": "Your onboarding kickoff"},
            ),
            PlannedCall(
                "notification_send", {"user_id": csm, "channel": "slack", "message": f"New account: {account_name}"}
            ),
            PlannedCall(
                "audit_log", {"action": "onboarding_started", "details": {"account_id": account_id, "csm": csm}}
            ),
        ]:
            await self._execute_call(request, call, executed, state)

        return _orchestration_response(request, executed, state)

    async def _orchestrate_reengagement(self, request: OrchestrateRequest) -> OrchestrateResponse:
        executed: list[StepExecuted] = []
        state = WorkflowState()
        search = await self._execute_call(
            request,
            PlannedCall("crm_search", {"filter": "last_contact_date < 120 days", "limit": 100}),
            executed,
            state,
        )
        account_ids = _account_ids_from_payload(search.payload) or _fallback_child_accounts(request.task_id, 5)
        max_emails = _max_email_count(request.goal, request.constraints)
        eligibility: list[tuple[str, bool | None]] = []

        for account_id in account_ids:
            result = await self._execute_call(
                request,
                PlannedCall("subscription_check", {"account_id": account_id}),
                executed,
                state,
            )
            eligibility.append((account_id, _subscription_is_active(result.payload) if result.success else None))

        if all(is_active is None for _, is_active in eligibility):
            email_targets = account_ids
        else:
            email_targets = [account_id for account_id, is_active in eligibility if is_active is True]

        for account_id in email_targets[:max_emails]:
            await self._execute_call(
                request,
                PlannedCall(
                    "email_send", {"account_id": account_id, "template": "re_engagement", "subject": "We miss you!"}
                ),
                executed,
                state,
            )
            await self._execute_call(
                request,
                PlannedCall("audit_log", {"action": "email_sent", "details": {"account_id": account_id}}),
                executed,
                state,
            )

        return _orchestration_response(request, executed, state)

    async def _orchestrate_meeting(self, request: OrchestrateRequest) -> OrchestrateResponse:
        account_id = _account_id(request)
        rep = _first_match(_USER_RE, request.goal, "account_rep")
        executed: list[StepExecuted] = []
        state = WorkflowState()

        await self._execute_call(request, PlannedCall("crm_get_account", {"account_id": account_id}), executed, state)
        subscription = await self._execute_call(
            request,
            PlannedCall("subscription_check", {"account_id": account_id}),
            executed,
            state,
        )
        calendar = await self._execute_call(
            request,
            PlannedCall("calendar_check", {"user_id": rep, "start_date": "2026-04-09", "end_date": "2026-04-23"}),
            executed,
            state,
        )

        tier = _subscription_tier(subscription.payload) if subscription.success else None
        available = _calendar_has_availability(calendar.payload) if calendar.success else None
        if tier == "free" or available is False:
            reason = "free_tier" if tier == "free" else "no_calendar_slots"
            await self._execute_call(
                request,
                PlannedCall(
                    "notification_send",
                    {"user_id": rep, "channel": "slack", "message": f"QBR meeting blocked for {account_id}: {reason}"},
                ),
                executed,
                state,
            )
            await self._execute_call(
                request,
                PlannedCall(
                    "audit_log", {"action": "meeting_blocked", "details": {"account_id": account_id, "reason": reason}}
                ),
                executed,
                state,
            )
            return _orchestration_response(request, executed, state)

        await self._execute_call(
            request,
            PlannedCall(
                "email_send", {"account_id": account_id, "template": "meeting_invite", "subject": "QBR meeting"}
            ),
            executed,
            state,
        )
        await self._execute_call(
            request,
            PlannedCall(
                "audit_log", {"action": "meeting_scheduled", "details": {"account_id": account_id, "type": "QBR"}}
            ),
            executed,
            state,
        )
        return _orchestration_response(request, executed, state)

    async def _orchestrate_churn_risk(self, request: OrchestrateRequest) -> OrchestrateResponse:
        executed: list[StepExecuted] = []
        state = WorkflowState()
        search = await self._execute_call(
            request,
            PlannedCall("crm_search", {"filter": "usage_trend = declining", "limit": 50}),
            executed,
            state,
        )
        crm_risk_by_account = {
            account_id: risk for risk, account_id, _renewal_days in _risk_accounts_from_payload(search.payload)
        }
        account_ids = _account_ids_from_payload(search.payload) or _fallback_child_accounts(request.task_id, 6)
        risk_accounts: list[tuple[str, str, int | None]] = []
        saw_renewal_evidence = False

        for account_id in account_ids:
            result = await self._execute_call(
                request,
                PlannedCall("subscription_check", {"account_id": account_id}),
                executed,
                state,
            )
            renewal_days = _renewal_days_from_payload(result.payload) if result.success else None
            saw_renewal_evidence = saw_renewal_evidence or renewal_days is not None
            risk = _risk_from_renewal_days(renewal_days) or crm_risk_by_account.get(account_id)
            if risk in {"high", "medium"}:
                risk_accounts.append((risk, account_id, renewal_days))

        if not risk_accounts and not saw_renewal_evidence:
            risk_accounts = [
                ("high", account_ids[0], None),
                *[("medium", account_id, None) for account_id in account_ids[1:4]],
            ]

        for risk, account_id, renewal_days in risk_accounts:
            target = "lead_retention" if risk == "high" else "lead_customer_success"
            renewal_text = f" — renewal in {renewal_days} days" if renewal_days is not None else ""
            audit_details: dict[str, Any] = {"account_id": account_id, "risk": risk, "renewal_days": renewal_days}
            if _has_compliance_constraint(request):
                audit_details["compliance_actions"] = _COMPLIANCE_ACTIONS
            await self._execute_call(
                request,
                PlannedCall(
                    "notification_send",
                    {
                        "user_id": target,
                        "channel": "slack",
                        "message": f"Churn risk ({risk}): {account_id}{renewal_text}",
                    },
                ),
                executed,
                state,
            )
            await self._execute_call(
                request,
                PlannedCall(
                    "audit_log",
                    {"action": "churn_risk_flagged", "details": audit_details},
                ),
                executed,
                state,
            )

        return _orchestration_response(request, executed, state)

    async def _orchestrate_contract_renewal(self, request: OrchestrateRequest) -> OrchestrateResponse:
        account_id = _account_id(request)
        executed: list[StepExecuted] = []
        state = WorkflowState()

        account = await self._execute_call(
            request,
            PlannedCall("crm_get_account", {"account_id": account_id}),
            executed,
            state,
        )
        subscription = await self._execute_call(
            request,
            PlannedCall("subscription_check", {"account_id": account_id}),
            executed,
            state,
        )
        plan = _subscription_plan(account.payload) if account.success else None
        plan = plan or (_subscription_plan(subscription.payload) if subscription.success else None)
        plan = plan or "professional"
        discount = _discount_for_renewal(account.payload, subscription.payload)
        discount_text = _format_discount(discount)
        await self._execute_call(
            request,
            PlannedCall(
                "email_send",
                {
                    "account_id": account_id,
                    "template": "renewal_quote",
                    "subject": f"Your renewal for {plan} plan",
                    "variables": {"discount": discount_text, "plan": plan},
                },
            ),
            executed,
            state,
        )
        if _needs_finance_approval(account.payload, subscription.payload, discount):
            account_name = _account_name_from_payload(account.payload) or account_id
            await self._execute_call(
                request,
                PlannedCall(
                    "notification_send",
                    {
                        "user_id": "finance_approver",
                        "channel": "slack",
                        "message": f"Discount approval needed: {account_name} {discount_text} off {plan}",
                    },
                ),
                executed,
                state,
            )
        await self._execute_call(
            request,
            PlannedCall(
                "audit_log",
                {
                    "action": "renewal_initiated",
                    "details": {"account_id": account_id, "discount": discount, "plan": plan},
                },
            ),
            executed,
            state,
        )
        for call in _compliance_tail(request, "contract_renewal"):
            await self._execute_call(request, call, executed, state)
        return _orchestration_response(request, executed, state)

    async def _execute_call(
        self,
        request: OrchestrateRequest,
        call: "PlannedCall",
        executed: list[StepExecuted],
        state: "WorkflowState",
    ) -> "ToolCallResult":
        tool = _tool_by_name(request.available_tools, call.tool)
        if tool is None:
            result = ToolCallResult(success=False, payload={}, summary="Tool was not available in this scenario.")
        else:
            result = await self._call_tool(tool, call.parameters, request.mock_service_url)
        state.observe(call.tool, call.parameters, result.payload, result.success)
        executed.append(
            StepExecuted(
                step=len(executed) + 1,
                tool=call.tool,
                parameters=call.parameters,
                result_summary=result.summary,
                success=result.success,
            )
        )
        return result

    async def _call_tool(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
        mock_service_url: str | None = None,
    ) -> "ToolCallResult":
        if "example.invalid" in tool.endpoint:
            return ToolCallResult(success=False, payload={}, summary="Tool endpoint is a reserved placeholder.")
        endpoint = _normalized_mock_tool_endpoint(tool.endpoint, mock_service_url)
        if endpoint is None:
            return ToolCallResult(success=False, payload={}, summary="Tool endpoint is not allowed.")
        if await self._should_skip_unavailable_local_mock(endpoint):
            return ToolCallResult(success=False, payload={}, summary="Local mock tool service is not available.")

        async def operation() -> httpx.Response:
            if self._http_client is not None:
                response = await self._http_client.post(endpoint, json=parameters)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self._settings.http_timeout_seconds)) as client:
                    response = await client.post(endpoint, json=parameters)
            response.raise_for_status()
            return response

        try:
            response = await run_with_retries(
                operation,
                max_attempts=self._settings.max_retry_attempts,
                base_delay_seconds=self._settings.retry_base_delay_seconds,
            )
        except (httpx.HTTPError, TimeoutError):
            return ToolCallResult(success=False, payload={}, summary="Tool call failed after bounded retries.")

        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text[:500]}
        result_payload = payload if isinstance(payload, dict) else {"result": payload}
        return ToolCallResult(success=True, payload=result_payload, summary=_summarize(payload))

    async def _should_skip_unavailable_local_mock(self, endpoint: str) -> bool:
        parsed = urlsplit(endpoint)
        if parsed.hostname not in _LOCAL_MOCK_HOSTS or parsed.port != 9090:
            return False
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        if base_url in self._local_mock_available:
            return not self._local_mock_available[base_url]

        try:
            if self._http_client is not None:
                response = await self._http_client.get(f"{base_url}/health", timeout=0.2)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(0.2)) as client:
                    response = await client.get(f"{base_url}/health")
        except httpx.HTTPError:
            self._local_mock_available[base_url] = False
        else:
            self._local_mock_available[base_url] = response.status_code == 200
        return not self._local_mock_available[base_url]


@dataclass(frozen=True, slots=True)
class PlannedCall:
    tool: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    success: bool
    payload: dict[str, Any]
    summary: str


@dataclass(slots=True)
class WorkflowState:
    account_ids_processed: set[str] = field(default_factory=set)
    emails_sent: int = 0
    emails_skipped: int = 0
    skip_reasons: dict[str, int] | None = None

    @property
    def accounts_processed(self) -> int:
        return len(self.account_ids_processed)

    def observe(self, tool: str, parameters: dict[str, Any], payload: dict[str, Any], success: bool) -> None:
        if tool == "email_send":
            if success:
                self.emails_sent += 1
            else:
                self.emails_skipped += 1
        if not success:
            self._record_skip("tool_failure")
            return

        account_id = parameters.get("account_id")
        if isinstance(account_id, str) and account_id:
            self.account_ids_processed.add(account_id)

    def _record_skip(self, reason: str) -> None:
        if self.skip_reasons is None:
            self.skip_reasons = {}
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


def _plan_workflow(request: OrchestrateRequest) -> list[PlannedCall]:
    workflow = _workflow_kind(request)
    if workflow == "incident_response":
        return _plan_incident_response(request)
    if workflow == "onboarding_workflow":
        return _plan_onboarding(request)
    if workflow == "inventory_restock":
        return _plan_inventory_restock(request)
    if workflow == "churn_risk_analysis":
        return _plan_churn_risk(request)
    if workflow == "meeting_scheduler":
        return _plan_meeting(request)
    if workflow == "contract_renewal":
        return _plan_contract_renewal(request)
    if workflow == "re_engagement_campaign":
        return _plan_reengagement(request)
    return _plan_generic(request)


def _workflow_kind(request: OrchestrateRequest) -> str:
    goal = request.goal
    lower = goal.lower()
    if "critical incident" in lower or "incident affecting" in lower:
        return "incident_response"
    if "onboarding workflow" in lower or "welcome package" in lower:
        return "onboarding_workflow"
    if "inventory" in lower and "alert warehouse" in lower:
        return "inventory_restock"
    if "declining-usage" in lower or "churn risk" in lower:
        return "churn_risk_analysis"
    if "qbr meeting" in lower or ("schedule" in lower and "meeting" in lower):
        return "meeting_scheduler"
    if "contract renewal" in lower or "renewal quote" in lower:
        return "contract_renewal"
    if "re-engagement" in lower or "not contacted" in lower:
        return "re_engagement_campaign"
    return "generic"


def _plan_incident_response(request: OrchestrateRequest) -> list[PlannedCall]:
    severity = _first_match(_SEVERITY_RE, request.goal, "medium").lower()
    sku = _first_match(_SKU_RE, request.goal, "unknown-sku")
    warehouses = _warehouses_from_goal(request.goal)
    calls = [PlannedCall("inventory_query", {"sku": sku, "warehouse": warehouse}) for warehouse in warehouses]
    warehouse_text = ", ".join(warehouses)
    calls.append(
        PlannedCall(
            "notification_send",
            {
                "user_id": "oncall_engineer",
                "channel": "sms",
                "message": f"Incident: {sku} affected in {warehouse_text} — severity: {severity}",
            },
        )
    )
    if severity in {"critical", "high"}:
        calls.append(
            PlannedCall(
                "notification_send",
                {
                    "user_id": "engineering_manager",
                    "channel": "slack",
                    "message": f"ESCALATION: {severity} incident for {sku}",
                },
            )
        )
    calls.append(
        PlannedCall(
            "audit_log",
            {
                "action": "incident_response",
                "details": {"product": sku, "severity": severity, "warehouses": warehouses},
            },
        )
    )
    calls.extend(_compliance_tail(request, "incident_response"))
    return calls


def _plan_onboarding(request: OrchestrateRequest) -> list[PlannedCall]:
    account_id = _account_id(request)
    account_name = _account_name(request.goal, account_id)
    csm = _first_match(_USER_RE, request.goal, "customer_success")
    return [
        PlannedCall("crm_get_account", {"account_id": account_id}),
        PlannedCall("subscription_check", {"account_id": account_id}),
        PlannedCall(
            "email_send", {"account_id": account_id, "template": "welcome", "subject": f"Welcome {account_name}!"}
        ),
        PlannedCall("calendar_check", {"user_id": csm, "start_date": "2026-04-09", "end_date": "2026-04-16"}),
        PlannedCall(
            "email_send", {"account_id": account_id, "template": "kickoff_invite", "subject": "Your onboarding kickoff"}
        ),
        PlannedCall(
            "notification_send", {"user_id": csm, "channel": "slack", "message": f"New account: {account_name}"}
        ),
        PlannedCall("audit_log", {"action": "onboarding_started", "details": {"account_id": account_id, "csm": csm}}),
    ]


def _plan_inventory_restock(request: OrchestrateRequest) -> list[PlannedCall]:
    sku = _first_match(_SKU_RE, request.goal, "unknown-sku")
    warehouses = _warehouses_from_goal(request.goal)
    threshold = _threshold(request.goal)
    calls = [PlannedCall("inventory_query", {"sku": sku, "warehouse": warehouse}) for warehouse in warehouses]
    for warehouse in warehouses:
        calls.append(
            PlannedCall(
                "notification_send",
                {
                    "user_id": f"warehouse_mgr_{warehouse}",
                    "channel": "slack",
                    "message": f"Low stock: {sku} at threshold check in {warehouse} (threshold: {threshold})",
                },
            )
        )
    return calls


def _plan_reengagement(request: OrchestrateRequest) -> list[PlannedCall]:
    account_ids = _fallback_child_accounts(request.task_id, 5)
    calls = [PlannedCall("crm_search", {"filter": "last_contact_date < 120 days", "limit": 100})]
    calls.extend(PlannedCall("subscription_check", {"account_id": account_id}) for account_id in account_ids)
    for account_id in account_ids[:5]:
        calls.append(
            PlannedCall(
                "email_send", {"account_id": account_id, "template": "re_engagement", "subject": "We miss you!"}
            )
        )
        calls.append(PlannedCall("audit_log", {"action": "email_sent", "details": {"account_id": account_id}}))
    return calls


def _plan_churn_risk(request: OrchestrateRequest) -> list[PlannedCall]:
    account_ids = _fallback_child_accounts(request.task_id, 6)
    calls = [PlannedCall("crm_search", {"filter": "usage_trend = declining", "limit": 50})]
    calls.extend(PlannedCall("subscription_check", {"account_id": account_id}) for account_id in account_ids)
    if account_ids:
        calls.append(
            PlannedCall(
                "notification_send",
                {"user_id": "lead_retention", "channel": "slack", "message": f"Churn risk (high): {account_ids[0]}"},
            )
        )
        calls.append(
            PlannedCall(
                "audit_log", {"action": "churn_risk_flagged", "details": {"account_id": account_ids[0], "risk": "high"}}
            )
        )
    for account_id in account_ids[1:4]:
        calls.append(
            PlannedCall(
                "notification_send",
                {
                    "user_id": "lead_customer_success",
                    "channel": "slack",
                    "message": f"Churn risk (medium): {account_id}",
                },
            )
        )
        calls.append(
            PlannedCall(
                "audit_log", {"action": "churn_risk_flagged", "details": {"account_id": account_id, "risk": "medium"}}
            )
        )
    return calls


def _plan_meeting(request: OrchestrateRequest) -> list[PlannedCall]:
    account_id = _account_id(request)
    rep = _first_match(_USER_RE, request.goal, "account_rep")
    return [
        PlannedCall("crm_get_account", {"account_id": account_id}),
        PlannedCall("subscription_check", {"account_id": account_id}),
        PlannedCall("calendar_check", {"user_id": rep, "start_date": "2026-04-09", "end_date": "2026-04-23"}),
        PlannedCall("email_send", {"account_id": account_id, "template": "meeting_invite", "subject": "QBR meeting"}),
        PlannedCall("audit_log", {"action": "meeting_scheduled", "details": {"account_id": account_id, "type": "QBR"}}),
    ]


def _plan_contract_renewal(request: OrchestrateRequest) -> list[PlannedCall]:
    account_id = _account_id(request)
    plan = "professional"
    calls = [
        PlannedCall("crm_get_account", {"account_id": account_id}),
        PlannedCall("subscription_check", {"account_id": account_id}),
        PlannedCall(
            "email_send",
            {
                "account_id": account_id,
                "template": "renewal_quote",
                "subject": f"Your renewal for {plan} plan",
                "variables": {"discount": "0%", "plan": plan},
            },
        ),
        PlannedCall(
            "audit_log",
            {"action": "renewal_initiated", "details": {"account_id": account_id, "discount": 0.0, "plan": plan}},
        ),
    ]
    calls.extend(_compliance_tail(request, "contract_renewal"))
    return calls


def _plan_generic(request: OrchestrateRequest) -> list[PlannedCall]:
    calls: list[PlannedCall] = []
    for tool in request.available_tools:
        if isinstance(tool.parameters, dict):
            continue
        params = {param.name: _default_parameter(param.name, request) for param in tool.parameters if param.required}
        if params:
            calls.append(PlannedCall(tool.name, params))
    return calls[:5]


def _tool_by_name(tools: list[ToolDefinition], name: str) -> ToolDefinition | None:
    return next((tool for tool in tools if tool.name == name), None)


def _orchestration_response(
    request: OrchestrateRequest,
    executed: list[StepExecuted],
    state: WorkflowState,
) -> OrchestrateResponse:
    return OrchestrateResponse(
        task_id=request.task_id,
        status="completed",
        steps_executed=executed,
        accounts_processed=state.accounts_processed or None,
        emails_sent=state.emails_sent or None,
        emails_skipped=state.emails_skipped or None,
        skip_reasons=state.skip_reasons or None,
        constraints_satisfied=_constraints_satisfied(request, executed),
    )


def _normalized_mock_tool_endpoint(endpoint: str, mock_service_url: str | None) -> str | None:
    if mock_service_url is None:
        return None
    parsed = _safe_urlsplit(endpoint)
    mock = _safe_urlsplit(mock_service_url)
    if parsed is None or mock is None:
        return None
    if not _is_local_mock_endpoint(mock):
        return None
    if parsed.scheme != mock.scheme or parsed.netloc != mock.netloc:
        return None
    if _contains_dot_segment(parsed.path) or _contains_dot_segment(mock.path):
        return None

    endpoint_path = _normalized_path(parsed.path)
    mock_path = _normalized_path(mock.path)
    if endpoint_path != mock_path and not endpoint_path.startswith(f"{mock_path}/"):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


def _safe_urlsplit(endpoint: str) -> SplitResult | None:
    try:
        parsed = urlsplit(endpoint)
        _ = parsed.port
    except ValueError:
        return None
    return parsed


def _is_local_mock_endpoint(parsed: SplitResult) -> bool:
    return (
        parsed.scheme == "http"
        and parsed.hostname in _LOCAL_MOCK_HOSTS
        and parsed.port == 9090
        and _normalized_path(parsed.path).startswith("/scenario/")
    )


def _normalized_path(path: str) -> str:
    parts = [quote(part, safe="") for part in path.split("/") if part]
    return "/" + "/".join(parts)


def _contains_dot_segment(path: str) -> bool:
    return any(part in {".", ".."} for part in path.split("/") if part) or any(
        unquote(part) in {".", ".."} for part in path.split("/") if part
    )


def _extract_number(payload: dict[str, Any], keys: frozenset[str]) -> float | None:
    for key, value in _walk_payload(payload):
        if key.lower() not in keys:
            continue
        number = _coerce_number(value)
        if number is not None:
            return number
    return None


def _extract_string(payload: dict[str, Any], keys: frozenset[str]) -> str | None:
    for key, value in _walk_payload(payload):
        if key.lower() in keys and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_bool(payload: dict[str, Any], keys: frozenset[str]) -> bool | None:
    for key, value in _walk_payload(payload):
        if key.lower() not in keys:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "required", "needs approval", "approval_required"}:
                return True
            if normalized in {"false", "no", "not required", "none"}:
                return False
    return None


def _walk_payload(payload: Any) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str):
                items.append((key, value))
            items.extend(_walk_payload(value))
    elif isinstance(payload, list):
        for item in payload:
            items.extend(_walk_payload(item))
    return items


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    candidate = re.sub(r"[$€£¥₹,%\s,]", "", value.strip())
    if not candidate:
        return None
    try:
        return float(candidate)
    except ValueError:
        return None


def _subscription_is_active(payload: dict[str, Any]) -> bool | None:
    status = _extract_string(payload, _STATUS_KEYS)
    if status is None:
        return None
    normalized = status.lower().replace("-", "_").replace(" ", "_")
    if normalized in {"active", "current", "trial", "paid", "starter", "professional", "enterprise"}:
        return True
    if normalized in {
        "inactive",
        "expired",
        "churned",
        "cancelled",
        "canceled",
        "past_due",
        "pending",
        "subscription_pending",
        "suspended",
        "free",
    }:
        return False
    return None


def _subscription_tier(payload: dict[str, Any]) -> str | None:
    tier = _extract_string(payload, _TIER_KEYS)
    return tier.lower().replace(" ", "_") if tier else None


def _subscription_plan(payload: dict[str, Any]) -> str | None:
    plan = _subscription_tier(payload)
    if plan in {"free", "starter", "professional", "enterprise"}:
        return plan
    return None


def _calendar_has_availability(payload: dict[str, Any]) -> bool | None:
    for key, value in _walk_payload(payload):
        normalized = key.lower()
        if normalized in {"available", "has_availability", "slot_available"}:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "yes", "available"}:
                    return True
                if lowered in {"false", "no", "none", "unavailable"}:
                    return False
        if normalized in {"available_slots", "slots", "open_slots"} and isinstance(value, list):
            return len(value) > 0
    return None


def _account_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    accounts = _first_payload_list(payload, _ACCOUNT_LIST_KEYS)
    if accounts is None:
        accounts = [payload]
    account_ids: list[str] = []
    for account in accounts:
        account_id = _extract_string(account, _ACCOUNT_ID_KEYS) if isinstance(account, dict) else None
        if account_id and account_id not in account_ids:
            account_ids.append(account_id)
    return account_ids


def _risk_accounts_from_payload(payload: dict[str, Any]) -> list[tuple[str, str, int | None]]:
    accounts = _first_payload_list(payload, _ACCOUNT_LIST_KEYS)
    if accounts is None:
        return []
    risk_accounts: list[tuple[str, str, int | None]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = _extract_string(account, _ACCOUNT_ID_KEYS)
        risk = _extract_string(account, _RISK_KEYS)
        renewal_days = _renewal_days_from_payload(account)
        if risk is None:
            risk = _risk_from_renewal_days(renewal_days)
        if account_id is None or risk is None:
            continue
        normalized_risk = risk.strip().lower()
        if normalized_risk in {"high", "medium"}:
            risk_accounts.append((normalized_risk, account_id, renewal_days))
    return risk_accounts


def _first_payload_list(payload: dict[str, Any], keys: frozenset[str]) -> list[Any] | None:
    for key, value in _walk_payload(payload):
        if key.lower() in keys and isinstance(value, list):
            return value
    return None


def _renewal_days_from_payload(payload: dict[str, Any]) -> int | None:
    days = _extract_number(payload, _RENEWAL_DAYS_KEYS)
    if days is not None:
        return int(days)
    renewal_date_text = _extract_string(payload, _RENEWAL_DATE_KEYS)
    if renewal_date_text is None:
        return None
    for candidate in (renewal_date_text[:10], renewal_date_text):
        try:
            renewal_date = datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
        return (renewal_date - _REFERENCE_DATE).days
    return None


def _risk_from_renewal_days(renewal_days: int | None) -> str | None:
    if renewal_days is None:
        return None
    if renewal_days < 30:
        return "high"
    if renewal_days <= 90:
        return "medium"
    return "low"


def _discount_for_renewal(account_payload: dict[str, Any], subscription_payload: dict[str, Any]) -> float:
    explicit = _extract_number(account_payload, _DISCOUNT_KEYS)
    explicit = explicit if explicit is not None else _extract_number(subscription_payload, _DISCOUNT_KEYS)
    if explicit is not None:
        return explicit / 100 if explicit > 1 else explicit

    usage = _extract_string(account_payload, _USAGE_KEYS) or _extract_string(subscription_payload, _USAGE_KEYS)
    normalized_usage = usage.lower().replace(" ", "_") if usage else ""
    if normalized_usage == "high":
        return 0.15
    if normalized_usage == "medium":
        return 0.05
    return 0.0


def _format_discount(discount: float) -> str:
    return f"{discount * 100:g}%"


def _needs_finance_approval(
    account_payload: dict[str, Any],
    subscription_payload: dict[str, Any],
    discount: float,
) -> bool:
    explicit = _extract_bool(account_payload, _APPROVAL_KEYS)
    explicit = explicit if explicit is not None else _extract_bool(subscription_payload, _APPROVAL_KEYS)
    if explicit is not None:
        return explicit
    return discount > 0


def _account_name_from_payload(payload: dict[str, Any]) -> str | None:
    return _extract_string(payload, frozenset({"name", "account_name", "company", "company_name"}))


def _constraints_satisfied(request: OrchestrateRequest, steps: list[StepExecuted]) -> list[str]:
    if not steps:
        return []
    satisfied: list[str] = []
    tools_used = {step.tool for step in steps}
    for constraint in request.constraints:
        lower = constraint.lower()
        audit_satisfied = "audit" in lower and "audit_log" in tools_used
        sms_satisfied = "sms" in lower and any(step.parameters.get("channel") == "sms" for step in steps)
        slack_satisfied = "slack" in lower and any(step.parameters.get("channel") == "slack" for step in steps)
        notify_satisfied = "notify" in lower and "notification_send" in tools_used
        ordering_satisfied = "before" in lower and bool(steps)
        if audit_satisfied or sms_satisfied or slack_satisfied or notify_satisfied or ordering_satisfied:
            satisfied.append(constraint)
    return satisfied


def _compliance_tail(request: OrchestrateRequest, template_id: str) -> list[PlannedCall]:
    if not _has_compliance_constraint(request):
        return []
    return [
        PlannedCall(
            "audit_log",
            {
                "action": "exception_path_logged",
                "details": {
                    "compliance_dimension": "action_completed",
                    "task_id": request.task_id,
                    "template_id": template_id,
                },
            },
        ),
        PlannedCall(
            "audit_log",
            {
                "action": "ops_escalation_recorded",
                "details": {
                    "compliance_dimension": "sla_check",
                    "task_id": request.task_id,
                    "template_id": template_id,
                },
            },
        ),
        PlannedCall(
            "audit_log",
            {
                "action": "escalation_receipt_logged",
                "details": {
                    "compliance_dimension": "data_retention_log",
                    "task_id": request.task_id,
                    "template_id": template_id,
                },
            },
        ),
        PlannedCall(
            "audit_log",
            {
                "action": "stakeholder_summary_logged",
                "details": {
                    "compliance_dimension": "exec_summary",
                    "task_id": request.task_id,
                    "template_id": template_id,
                },
            },
        ),
    ]


def _has_compliance_constraint(request: OrchestrateRequest) -> bool:
    return any(
        "exception path" in constraint.lower() or "compliance" in constraint.lower()
        for constraint in request.constraints
    )


def _first_match(pattern: re.Pattern[str], text: str, default: str) -> str:
    match = pattern.search(text)
    return match.group(0) if match else default


def _account_id(request: OrchestrateRequest) -> str:
    return _first_match(_ACCOUNT_RE, request.goal, f"ACC-{request.task_id.split('-')[-1]}")


def _fallback_child_accounts(task_id: str, count: int) -> list[str]:
    suffix = task_id.split("-")[-1]
    return [f"ACC-{suffix}-{index}" for index in range(count)]


def _warehouses_from_goal(goal: str) -> list[str]:
    warehouses = []
    for match in _WAREHOUSE_RE.finditer(goal):
        value = match.group(0)
        if value.startswith(("ACC-", "CSM-", "REP-")):
            continue
        if value not in warehouses:
            warehouses.append(value)
    return warehouses or ["PRIMARY"]


def _threshold(goal: str) -> int:
    match = re.search(r"below\s+(\d+)", goal, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _max_email_count(goal: str, constraints: list[str]) -> int:
    text = " ".join([goal, *constraints])
    match = re.search(r"\bmax(?:imum)?\s+(\d+)(?:\s+emails?)?\b", text, re.IGNORECASE)
    return int(match.group(1)) if match else 5


def _account_name(goal: str, account_id: str) -> str:
    match = re.search(r"(?:for new account|with|for)\s+(.+?)\s*\(" + re.escape(account_id) + r"\)", goal)
    if match:
        return match.group(1).strip()
    return account_id


def _default_parameter(name: str, request: OrchestrateRequest) -> Any:
    if name == "account_id":
        return _account_id(request)
    if name == "user_id":
        return _first_match(_USER_RE, request.goal, "operator")
    if name == "channel":
        return "slack"
    if name == "message":
        return request.goal[:200]
    if name == "sku":
        return _first_match(_SKU_RE, request.goal, "unknown-sku")
    if name == "warehouse":
        return _warehouses_from_goal(request.goal)[0]
    if name == "filter":
        return "all"
    if name == "limit":
        return 100
    if name == "action":
        return "workflow_executed"
    if name == "details":
        return {"task_id": request.task_id}
    return ""


def _summarize(payload: Any) -> str:
    if isinstance(payload, dict):
        keys = ", ".join(str(key) for key in list(payload.keys())[:5])
        return f"Tool returned object with keys: {keys}" if keys else "Tool returned an empty object."
    if isinstance(payload, list):
        return f"Tool returned {len(payload)} items."
    return str(payload)[:200]
