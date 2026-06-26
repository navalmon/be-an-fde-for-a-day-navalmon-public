# Task 1: Signal Triage

`POST /triage`

## 📺 Briefings: watch these first

| | | |
|:---:|:---:|:---:|
| [<img src="https://img.youtube.com/vi/yLGHRmZPzu0/hqdefault.jpg" width="260">](https://youtu.be/yLGHRmZPzu0) | [<img src="https://img.youtube.com/vi/9crDxcGLzYA/hqdefault.jpg" width="260">](https://youtu.be/9crDxcGLzYA) | [<img src="https://img.youtube.com/vi/JnfGzRVc_xU/hqdefault.jpg" width="260">](https://youtu.be/JnfGzRVc_xU) |
| **V1: The Customer Problem**<br/>Cmdr. Kapoor | **V2: What "Good" Looks Like**<br/>Customer Architect | **V3: How FDEBench Scores You**<br/>Microsoft FDE |

> The videos cover priority intuition, escalation rules, engineering
> signals judges look for, and how scoring works. This README is the
> mechanical contract: endpoints, schemas, and the exact label strings
> you must emit.

Take an incoming mission signal and return a triage decision: what category, how urgent, which team, what's missing, what to do next.

The signals are messy: vague reports, contradictory subjects, noise from automated systems, and the occasional prompt injection. Your system needs to read through the noise and make a routing call.

Read the background:

- [customer_brief.md](customer_brief.md): team labels reference
- [routing_guide.md](routing_guide.md): gray areas and ambiguity disclosure
- [engineering_review.md](engineering_review.md): pointer to V2/V3

## Request contract

Input fields:

- `ticket_id`
- `subject`
- `description`
- `reporter`
- `created_at`
- `channel`
- `attachments`

See [../../../py/data/task1/input_schema.json](../../../py/data/task1/input_schema.json) for the formal schema.

## Response contract

Required output fields:

- `ticket_id`
- `category`
- `priority`
- `assigned_team`
- `needs_escalation`
- `missing_information`
- `next_best_action`
- `remediation_steps`

See [../../../py/data/task1/output_schema.json](../../../py/data/task1/output_schema.json) for the formal schema.

### Valid labels

Categories:

- `Crew Access & Biometrics`
- `Hull & Structural Systems`
- `Communications & Navigation`
- `Flight Software & Instruments`
- `Threat Detection & Containment`
- `Telemetry & Data Banks`
- `Mission Briefing Request`
- `Not a Mission Signal`

Teams:

- `Crew Identity & Airlock Control`
- `Spacecraft Systems Engineering`
- `Deep Space Communications`
- `Mission Software Operations`
- `Threat Response Command`
- `Telemetry & Data Core`
- `None`

Priorities:

- `P1`
- `P2`
- `P3`
- `P4`

Missing Information (16 exact strings):

`affected_subsystem`, `anomaly_readout`, `sequence_to_reproduce`, `affected_crew`, `habitat_conditions`, `stardate`, `previous_signal_id`, `crew_contact`, `module_specs`, `software_version`, `sector_coordinates`, `mission_impact`, `recurrence_pattern`, `sensor_log_or_capture`, `biometric_method`, `system_configuration`

#### What each `missing_information` term means

The 16 labels are space-themed wrappers around ordinary support-ticket
concepts. Emit a label in `missing_information` when the *evidence in
the description* for that concept is absent. If the description already
provides the evidence (even loosely), do **not** emit it — set F1
penalises both missed-true and over-emitted-false labels.

| Term | What concept it tracks |
|---|---|
| `affected_subsystem` | Specific component, service, console, antenna, sensor, etc. that's failing |
| `anomaly_readout` | The actual error message, code, alarm name, or readout displayed |
| `sequence_to_reproduce` | Steps / trigger that reproduces the anomaly |
| `affected_crew` | Who is impacted — named users, count, team, or shift |
| `habitat_conditions` | Environmental context — bay pressure, temperature, radiation, life-support mode |
| `stardate` | Concrete timestamp of when it started or last occurred (NB: `created_at` does not count) |
| `previous_signal_id` | Prior ticket / incident reference |
| `crew_contact` | Working channel for follow-up with the reporter |
| `module_specs` | Hardware / device / terminal model, serial, or build |
| `software_version` | Software / firmware version of the affected app or subsystem |
| `sector_coordinates` | Network or location context — VLAN, subnet, sector grid, docking bay |
| `mission_impact` | Operational consequence — what mission, deadline, or operation is blocked |
| `recurrence_pattern` | How often the anomaly recurs (cadence, intermittency) |
| `sensor_log_or_capture` | Sensor logs, screenshots, telemetry dump, or attachments |
| `biometric_method` | How the user authenticated — biometric mode, MFA factor, SSO method |
| `system_configuration` | Configuration state — mode, profile, policy, role, permission |

> Tip: walk the table per ticket and ask "is this concept present in the
> description?" Only emit when the answer is no. Empty
> `missing_information: []` is a valid and common answer for tickets that
> are fully described, are post-mortems forwarded for record-keeping, or
> are `Not a Mission Signal`.

## Scoring

Resolution is one of three components in your Tier 1 score. The exact
formula, weights, and per-dimension metrics live in
[`py/common/libs/fdebenchkit/`](../../../py/common/libs/fdebenchkit/) and
are walked through in [V3](https://youtu.be/JnfGzRVc_xU).
