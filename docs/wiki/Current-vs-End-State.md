# Current vs End State

Back to [Home](Home.md). Labels are defined on the Home page.

## Why this page exists

Most of what this wiki describes is design. A reader who wants to know whether a sentence describes running code should be able to find out in one table, without trusting prose. This page is that table, one row per subsystem, with the pin that backs every IMPLEMENTED / PROVEN row. Nothing on it is softened. Where the evidence is ambiguous, the row takes the narrower claim.

## Proven in v0.7.0

| Behavior | Status | Pin |
|---|---|---|
| Remote mission authorization: a natural-language Telegram request produces a closed-schema Mission Authorization; approval is one-shot and bound to the exact rendered mission text; typed text carries no authority | IMPLEMENTED / PROVEN | `tests/test_release_narrative.py`; CHANGELOG v0.7.0 |
| DI-REMOTE-2 routing, route (b): the legacy turn's marker is a routing signal only; a separate fresh planning turn produces the authorization | IMPLEMENTED / PROVEN | CHANGELOG v0.7.0; README DI-REMOTE-2 section |
| Telegram reference interaction: numeric allowlist, private chats only, authentication before parsing, one-shot Approve / Reject bound to the exact plan message | IMPLEMENTED / PROVEN | `telegram_operator` suites; v0.6.3 live validation |
| Isolated managed target materialization with containment, canonical-remote, and baseline verification | IMPLEMENTED / PROVEN | `tests/test_target_runtime.py` (250/250 in the stabilization record); `tests/test_workspace_trust.py` |
| Unattended target Herdr bootstrap: Supervisor, Lead, Executor, Reviewer registered and interactive-ready | IMPLEMENTED / PROVEN | release narrative suite; historical mountain receipt |
| Supervisor to Lead to Executor and Reviewer execution, with the Supervisor as the first strategy-bearing component | IMPLEMENTED / PROVEN | Herdr suites; README |
| Independent Reviewer decisions: exactly one canonical `HERD_DECISION` token, persisted by the harness | IMPLEMENTED / PROVEN | `herdctl review-decision`; Herdr suites |
| Evidence-gated verification: `verified_result` necessary, never sufficient; eight conjuncts, ten independent problem codes; Herdr COMPLETE alone can never verify | IMPLEMENTED / PROVEN | release narrative suite (gate registry pin); CHANGELOG v0.7.0 |
| Exactly-once final Telegram result: a bot-owned placeholder bound before dispatch, edited after verification, never a second delivery call; terminal states disclosed | IMPLEMENTED / PROVEN | release narrative suite (mutation-pinned I5); CHANGELOG v0.7.0 |
| Ambiguity fails closed: ambiguous placeholder creation stops before dispatch; dispatched-but-unconfirmed work is reported AMBIGUOUS and never replayed | IMPLEMENTED / PROVEN | adapter and Runtime suites |
| Separate human Git gates: commit, push, and release tag are three one-shot approvals, none inheriting from another | IMPLEMENTED / PROVEN | Git guard suites; [human Git gates](../reference/human-git-gates.md) |
| Codex as the current reference Operator behind a Gateway that cannot reach Herdr | IMPLEMENTED / PROVEN | static isolation suite; [Codex Gateway](../reference/codex-gateway.md) |
| Deterministic Runtime and Broker boundaries: nine fixed lifecycle actions, Runtime-minted one-shot capabilities, sensitive values never caller-supplied | IMPLEMENTED / PROVEN | release narrative suite (`BROKER_ACTIONS` pin) |
| Protected workflow authority state: schema-2 store, atomic, cross-process-locked, fail-closed, group/other-accessible files refused | IMPLEMENTED / PROVEN | `workflow_authority` suites; CHANGELOG v0.7.0 |
| Read-only observation, schema v3, with the model-observability limit stated in its own diagnostics | IMPLEMENTED / PROVEN | `tests/test_docs_i8.py` |
| Clean-clone CI green on all four macOS/Ubuntu x Python 3.9/3.13 jobs | IMPLEMENTED / PROVEN | GitHub CI run `33330263889` at `4eea64f2a915e988dbfd73ad51dd9f6546bc6a8f` |

The one historical external-target mountain reached target Herdr COMPLETE with a canonical target Reviewer APPROVE, then terminated BLOCKED at `broker_verification_policy_drift` with `verified_result` and `result_delivery` null and no target Git delivery. The corrected verification and final-result path was certified later, hermetically and adversarially. A fresh post-fix live mountain is not release evidence, and separate artifact delivery is not certified. The exact record is on the [release evidence](../reference/release-evidence-v0.7.0.md) page.

## In progress in Phase 1

| Item | Status | Where it lives |
|---|---|---|
| `OperatorSession` abstract `prepare()` / `execute()` seam, `PreparedTurn`, `PreparedTurnError` | IN PROGRESS | `operator_session/session.py`; `tests/test_operator_session.py` |
| `CodexOperatorSession`, the package's only provider import, resolving the Gateway's `build_request` and `submit` at call time | IN PROGRESS | `operator_session/codex.py` |
| `FunctionOperatorSession`, adapting two injected callables | IN PROGRESS | `operator_session/session.py` |
| The Telegram adapter constructing the OperatorSession seam and routing its serialized Gateway turn through it | IN PROGRESS | `telegram_operator/adapter.py` |
| `HumanInteractionAdapter`, the provider-neutral human interaction contract | IN PROGRESS | `human_interaction/contract.py`; `tests/test_human_interaction.py` |
| `TelegramHumanInteractionAdapter`, the current/reference transport implementation | IN PROGRESS | `telegram_operator/interaction.py` |
| Production Telegram transport operations routed through the HumanInteractionAdapter seam | IN PROGRESS | `telegram_operator/adapter.py` |

These are the Phase 1 seams currently on `main`. Both stay IN PROGRESS because they are initial abstractions rather than the complete target lifecycles. The rest of Phase 1 (break-glass access, reboot and login survival, service readiness, DurableExecution, Capability, Worker, and the DBOS, Pi, and Grok Bot spikes) remains open. See [OperatorSession](OperatorSession.md), [Architecture](Architecture.md), and [Roadmap](Roadmap.md).

## Target subsystems

| Subsystem | Status | Page |
|---|---|---|
| Full OperatorSession lifecycle: `create`, `prompt`, `steer`, `follow_up`, `abort`, `status`, `events`, `restore`, `close` | PLANNED / TARGET | [OperatorSession](OperatorSession.md) |
| Broader HumanInteractionAdapter surface set, including the Grok interaction adapter | PLANNED / TARGET | [Architecture](Architecture.md) |
| Grok Bot plane | PLANNED / TARGET | [Architecture](Architecture.md) |
| DurableExecution interface | PLANNED / TARGET | [Architecture](Architecture.md) |
| Capability and Worker abstractions; Worker Registry | PLANNED / TARGET | [Capabilities and Workers](Capabilities-and-Workers.md) |
| Mission Harness: Mission Registry, Mission Manifest, Mission Authorization ledger, Authority Ledger, Evidence Graph, Blocker Ledger, Artifact Registry | PLANNED / TARGET | [Missions and Lifecycle](Missions-and-Lifecycle.md) |
| Budgets and checkpointing; budget-aware closure | PLANNED / TARGET | [Missions and Lifecycle](Missions-and-Lifecycle.md) |
| Reconciler; Observation Service; Attention Router | PLANNED / TARGET | [Observation and Recovery](Observation-and-Recovery.md) |
| Scheduler; multi-mission execution | PLANNED / TARGET | [Missions and Lifecycle](Missions-and-Lifecycle.md) |
| BrowserCapability; Action Risk Envelope; richer artifact delivery | PLANNED / TARGET | [Capabilities and Workers](Capabilities-and-Workers.md) |
| Provider selection; Domain Operator Profiles; Skill Packs | PLANNED / TARGET | [OperatorSession](OperatorSession.md) |
| Ops Steward | PLANNED / TARGET | [Roadmap](Roadmap.md) |
| Visual Mission OS | PLANNED / TARGET | [Roadmap](Roadmap.md) |
| Telegram-native exact delivery authorization (commit, push, PR, merge, tag, release, deploy receipts) | PLANNED / TARGET | [Authority and Safety](Authority-and-Safety.md) |

## Third-party roles and what they are not

| Third party | Role | Status | What it is not |
|---|---|---|---|
| Telegram | Current remote transport | REFERENCE / FALLBACK | Not the product. Not an execution node. Not an authority source. |
| Codex | Current Operator implementation and fallback until another path is proven | REFERENCE / FALLBACK | Not the Operator role itself. Not permanent architecture. |
| Grok Bot | Preferred target interaction plane | PLANNED / TARGET | Not an authority holder. Dodging Infinity is not a Grok wrapper. |
| Pi | Preferred candidate provider-neutral operator runtime | CANDIDATE | Not selected. Not a dependency. Dodging Infinity is not a Pi wrapper. |
| GPT, Claude, Grok models | Potential providers behind the OperatorSession | PLANNED / TARGET | Not a permanent assignment to any role. |
| DBOS | Candidate durability substrate | CANDIDATE | Not the product brain. Not an authority holder. |
| PostgreSQL | Potential dependency through DBOS | CANDIDATE | Not a direct dependency. |
| Playwright | Planned BrowserCapability dependency | PLANNED / TARGET | Not authority. Behind the Capability Broker. |
| pstack | Persistent browser patterns | DESIGN REFERENCE | Not a dependency. Its authority model is not inherited. |
| Munder Difflin | Visual design and presentation | DESIGN REFERENCE | Not the backend. Its orchestration is not inherited. |
| GitHub, GitHub Actions | Source control, release, CI | IMPLEMENTED / PROVEN | Not an authority source. Every consequential Git action stays human-gated. |
| Herdr | Engineering organization inside an engineering mission | IMPLEMENTED / PROVEN | Not the mission control plane. Not the product. Dodging Infinity is not merely Herdr and not a Herdr UI. |

## How to check a claim yourself

1. Find the row. If the status is IMPLEMENTED / PROVEN, the pin column names a test file, a CHANGELOG entry, or an evidence identifier.
2. Run the pin from the repository root, for example:

   ```bash
   PYTHONPATH="$PWD" python3 tests/test_operator_session.py
   PYTHONPATH="$PWD" python3 tests/test_docs_i8.py
   PYTHONPATH="$PWD" python3 tests/test_release_narrative.py
   ```

3. For a CHANGELOG pin, open [CHANGELOG.md](../../CHANGELOG.md) at the v0.7.0 entry and match the identifier.
4. For a CI pin, open the run id on GitHub Actions and match the commit SHA.
5. If the status is IN PROGRESS, read the module the row names. The seam is on `main`; confirm with `git merge-base --is-ancestor b53f51c main`, which succeeds.
6. If the status is PLANNED / TARGET, CANDIDATE, or DESIGN REFERENCE, expect to find nothing in the tree. A grep that finds an implementation means this page is stale, and the [Home](Home.md) page says which source wins.
