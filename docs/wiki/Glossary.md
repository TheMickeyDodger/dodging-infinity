# Glossary

Back to [Home](Home.md). Labels are defined on the Home page.

## Terms

One definition per term, alphabetical, each with its status.

| Term | Definition | Status |
|---|---|---|
| Action Risk Envelope | Classification of a proposed external action by confidence, blast radius, reversibility, external effect, credential scope, ambiguity risk, and data sensitivity. Changes review, cadence, and escalation; cannot create authority. | PLANNED / TARGET |
| AMBIGUOUS | The state of a mission, or of one external effect, when a non-idempotent action may have happened and its outcome is unknown. Nothing is retried until reconciled. In v0.7.0 the adapter reports dispatched-but-unconfirmed work as AMBIGUOUS and never replays it. | IMPLEMENTED / PROVEN for the adapter; PLANNED / TARGET as a mission state |
| Artifact | A mission-owned file such as Markdown, PDF, CSV, XLSX, DOCX, PPTX, or an image, with a digest, allowed type, size bound, containment check, and delivery state. Artifact delivery is separate from Git authority. | PLANNED / TARGET |
| Artifact Registry | The durable record of a mission's artifacts and their review and delivery state. | PLANNED / TARGET |
| Attention Router | Push: sends the human only the things that need attention. Never steers the Operator or Herdr. | PLANNED / TARGET |
| Authority | Permission to perform a consequential action. Held by humans; carried by transports; requested by models; displayed by UIs; minted by none of them. | IMPLEMENTED / PROVEN as a rule of the v0.7.0 chain |
| Authority Ledger | The durable record of every authorization and its consumption. | PLANNED / TARGET |
| Blocker Ledger | The durable record of what is stopping a mission and why. | PLANNED / TARGET |
| BrowserCapability | A persistent browser behind Dodging Infinity's authority, with read operations, write operations, and human handoff kept distinct. Playwright is the planned dependency. | PLANNED / TARGET |
| Capability | A specific thing a mission is allowed to do, brokered against its authorization at the moment of use. | PLANNED / TARGET |
| Capability Broker | The component that brokers capabilities. The v0.7.0 Target Broker (nine fixed lifecycle actions, Runtime-minted one-shot capabilities) is its narrow ancestor. | IMPLEMENTED / PROVEN for the Target Broker; PLANNED / TARGET for the general broker |
| Codex | The current reference implementation of the Operator role, reached through the Codex Gateway, and the fallback until another path is proven. Not the role itself. | REFERENCE / FALLBACK |
| Codex Gateway | The local, transport-neutral interface boundary in front of the reference Operator. Cannot reach Herdr. | REFERENCE / FALLBACK |
| DBOS | Candidate durable workflow and queue substrate behind the DurableExecution interface. Not selected. | CANDIDATE |
| Delivery authority | Permission to commit, push, open a PR, merge, tag, release, or deploy. Always `none` at mission authorization. Today: the local commit, push, and release-tag gates exist as separate one-shot human authorizations. Target: PR, merge, release, and deploy as their own gates, and exact one-shot Telegram-native receipts for every step. | IMPLEMENTED / PROVEN for the local commit, push, and release-tag gates and for `none` at authorization; PLANNED / TARGET for PR, merge, release, and deploy gates and the one-shot receipt model |
| DI-REMOTE-2 | Remote Target Repository Routing: one exact bounded mission against a remote GitHub target from a Telegram request, with this repository as the permanent control and policy repository. Released in v0.7.0. | IMPLEMENTED / PROVEN |
| `dirun` | The DI-REMOTE-2 Runtime entry script. A separate deterministic process that claims authorized workflows and advances the lifecycle. | IMPLEMENTED / PROVEN |
| Dodging Infinity | The governed mission fabric: the authoritative system for mission identity, lifecycle, rules, authority, evidence, blockers, artifacts, scheduling, budgets, workers, checkpoints, recovery, reconciliation, canonical status, and delivery receipts. | IMPLEMENTED / PROVEN for the v0.7.0 control chain; PLANNED / TARGET for the Mission Harness |
| Domain Operator Profile | Reusable specialization for a mission class: Skill Pack, context sources, proof requirements, worker requirements, escalation policy. Improves reasoning; grants no authority. | PLANNED / TARGET |
| DurableExecution | The interface (start, enqueue, schedule, cancel, resume, checkpoint, inspect, recover) between the Harness and a durability substrate. | PLANNED / TARGET |
| Evidence Graph | Durable proof of what was tested, reviewed, measured, or verified, per mission. | PLANNED / TARGET |
| Executor | The Herdr role that implements. | IMPLEMENTED / PROVEN |
| Grok Bot | The preferred target conversational plane with named specialist experiences. Owns no authority. | PLANNED / TARGET |
| Herdr | The engineering organization inside an engineering mission: Supervisor, Lead, Executor, Reviewer. Not the mission control plane. | IMPLEMENTED / PROVEN |
| Herdr Pod | The isolated Herdr created for one engineering mission. | IMPLEMENTED / PROVEN for one mission at a time; PLANNED / TARGET for independent Pods per concurrent mission |
| `herdctl` | The Herdr command-line tool: setup, presets, rules, missions, tasks, review decisions, human Git gates, health, observation. | IMPLEMENTED / PROVEN |
| Human gate | A deterministic one-shot human authorization bound to exact state: the commit, push, and release-tag gates. | IMPLEMENTED / PROVEN |
| HumanInteractionAdapter | The provider-neutral human interaction boundary. The initial seam is on `main`; Telegram is the current reference implementation and Grok Bot remains the target interaction plane. | IN PROGRESS |
| Lead | The Herdr role that owns acceptance and validates the Reviewer's decision. | IMPLEMENTED / PROVEN |
| Mission | One durable piece of work with its own objective, rules, evidence, budget, status, and authority. The v0.7.0 ancestor is a DI-REMOTE-2 workflow. | PLANNED / TARGET as the `M-####` object; IMPLEMENTED / PROVEN as a `wf-*` workflow |
| Mission Authorization | The human approval of an exact mission before consequential execution. In v0.7.0, a closed-schema document binding destination and boundaries, approved one-shot on the bound message. | IMPLEMENTED / PROVEN |
| Mission Harness | The authoritative core: registry, manifest, authorization, ledgers, lifecycle, evidence, artifacts, budgets, checkpoints, readiness, scheduling, Reconciler, Observation Service, event journal. | PLANNED / TARGET |
| Mission Manifest | The written contract describing exactly what a mission may do and what counts as success. | PLANNED / TARGET |
| Mission Registry | The durable list of missions and their identity. | PLANNED / TARGET |
| Mission Router | Inbound identity: which mission a conversation refers to. Routes identity only; does not engineer. | PLANNED / TARGET |
| Munder Difflin | Presentation source for the visual mission world. Not the backend. | DESIGN REFERENCE |
| Observation Service | Pull: read-only status from canonical state. Never interrupts or steers. `herdctl observe` is its ancestor for one herd. | PLANNED / TARGET; IMPLEMENTED / PROVEN for `herdctl observe` |
| Operator | The replaceable reasoning role for one mission. A role, not a model brand. | REFERENCE / FALLBACK today (Codex); PLANNED / TARGET as a provider-neutral role |
| OperatorSession | The provider-neutral boundary around the Operator. Today: `prepare()` / `execute()`, on `main`. Target: `create`, `prompt`, `steer`, `follow_up`, `abort`, `status`, `events`, `restore`, `close`. | IN PROGRESS for the seam; PLANNED / TARGET for the lifecycle |
| Ops Steward | The organizational learning layer that finds repeated failures and proposes improvements through a governed path. Cannot expand its own authority. | PLANNED / TARGET |
| Pi | Preferred candidate provider-neutral operator runtime. Not selected. | CANDIDATE |
| Playwright | Planned browser automation dependency behind BrowserCapability. | PLANNED / TARGET |
| PostgreSQL | Potential durable store, only through DBOS. | CANDIDATE |
| pstack | Source of persistent-browser and skill-pack design ideas. Its authority model is not inherited. | DESIGN REFERENCE |
| Reconciler | Deterministic machinery that compares durable expected state with reality. Not an AI agent. | PLANNED / TARGET |
| Reviewer | The independent, read-only Herdr role that returns exactly one canonical decision. Its APPROVE is necessary, never sufficient. | IMPLEMENTED / PROVEN |
| Runtime | The DI-REMOTE-2 process (`target_runtime/`, `dirun`) coupled to the control chain only through the durable workflow store. | IMPLEMENTED / PROVEN |
| Scheduler | Decides which missions and workers get capacity. Never combined with the Mission Router. | PLANNED / TARGET |
| Skill Pack | Encodes how to approach a class of mission. Guides reasoning; authorizes nothing. | PLANNED / TARGET |
| Supervisor | The first strategy-bearing Herdr role. Owns decomposition, route, sequencing, and the validation workflow. | IMPLEMENTED / PROVEN |
| Target Broker | The v0.7.0 privileged fixed-action component that validates and consumes Runtime-minted one-shot capabilities. | IMPLEMENTED / PROVEN |
| Telegram | The current reference transport for remote intent, approval, status, and results. Carries authority; never mints it. | REFERENCE / FALLBACK |
| `tgop` | The Telegram adapter entry script. | IMPLEMENTED / PROVEN |
| Verified result | A result that passed the evidence gate. In v0.7.0, a Broker-decided conjunction of eight conjuncts against a fresh disk read; never a model's declaration. | IMPLEMENTED / PROVEN |
| Visual Mission OS | The final projection of canonical state, built last. Never the backend. | PLANNED / TARGET |
| Worker | A capability-aware bounded execution host. The trusted Mac is Worker 1, not a permanent singleton. | PLANNED / TARGET as an abstraction; IMPLEMENTED / PROVEN for the trusted Mac as the only host |
| Worker Registry | The durable list of workers, capabilities, credential classes, capacity, leases, and health. | PLANNED / TARGET |
| Workflow authority store | The v0.7.0 durable `workflow_authority/` store, schema-2, atomic, cross-process-locked, fail-closed. | IMPLEMENTED / PROVEN |
