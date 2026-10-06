# Dodging Infinity roadmap

Status: Working roadmap\
Created: August 27, 2026\
Updated: October 3, 2026

This is the single roadmap for Dodging Infinity. It carries the long arc
(Phase 0 to Phase 11), the detailed near-term sequence (Phase I to Phase V,
Iterations 0 to 18), and the long-term milestones. Release history lives in
[CHANGELOG.md](../CHANGELOG.md); the evidence behind v0.7.0 is on the
[release evidence](release-evidence-v0.7.0.md) page; the system being built is
described in [architecture.md](architecture.md).

Two numbering systems appear below and they describe the same work from
different distances. The Phase 0 to Phase 11 arc says what each stage builds
and what it must prove. The Phase I to Phase V iterations are the detailed
near-term plan with per-iteration acceptance statements, and they are
test-pinned. Neither supersedes the other: Phases 0 to 2 correspond roughly to
Iterations 0 to 3 and the release gate, and the milestones map onto Phases 3 to
11. Where they disagree on the fine grain, the iterations are the nearer view.

Progress notation: ~~crossed out~~ = completed, or proven by the historical
external-target mountain before it terminated BLOCKED; uncrossed = still open.

## North Star

> I can leave the trusted Mac unattended for seven days, initiate
> multiple independent missions from anywhere, talk naturally about any
> one of them, observe all of them instantly, receive reviewed
> results and artifacts, authorize exact reviewed delivery actions from
> my phone, and recover any individual service failure without being
> physically present.

Dodging Infinity should evolve from one Codex thread reachable through
Telegram into a persistent remote mission fabric.

``` text
Human
  -> Telegram
  -> Telegram Adapter
  -> Mission Router
       -> Mission A -> Mission Codex -> Herdr
       -> Mission B -> Mission Codex -> Herdr
       -> Mission C -> Mission Codex -> Herdr
       -> New Mission
  -> Durable Mission State
       -> immediate status
       -> results
       -> artifacts
       -> delivery approvals / receipts
```

Separately:

``` text
Phone / Laptop -> Tailscale / SSH -> Trusted Mac
```

That is a break-glass recovery path, not normal mission authority.

## Controlling principles

-   Human owns intent and material delivery authority. Telegram may
    transport an exact human authorization, but it never mints delivery
    authority by itself.
-   Mission Router routes conversation identity only. It does not
    engineer.
-   Codex operates: intent, mission bounds, lifecycle decisions,
    independent verification.
-   Herdr engineers: Supervisor is the first strategy-bearing component.
-   Runtime remains deterministic and does not invent work.
-   Observability is read-only unless a separate bounded action is
    explicitly authorized.
-   Prefer truthful BLOCKED, STALE, AMBIGUOUS, or NEEDS HUMAN states
    over guessing or replaying uncertain effects.
-   One mission must never inherit another mission's authority, state,
    artifacts, approvals, or context.

# The long arc: Phase 0 to Phase 11

## Phase 0: v0.7.0, complete

The v0.7.0 release and its reference proof. DI-REMOTE-2 acceptance is complete
for the release tree: reconciled public documentation, hermetic clean-clone CI
(run `33330263889`), the historical mountain preserved as terminal diagnostic
evidence, the Runtime stabilization lineage integrated into `main`, final
certification on the stable tree (continuation task `20260901-165812-045b0c`,
2,048 tests, `OK (skipped=1)`), and the release tree proven green. Tag
publication is governed separately by the human authorization gate.

v0.7.0 is the reference proof for remote mission authorization, remote target
materialization, Herdr bootstrap, independent verification, exactly-once result
delivery, and human Git gates. The evidence trail is on the
[release evidence](release-evidence-v0.7.0.md) page.

Completed foundations, accumulated through v0.6.1, v0.6.2, v0.6.3, and v0.7.0:
the durable Herdr mission boundary; Supervisor to Lead to Executor and Reviewer
orchestration; repository isolation; the deterministic review protocol; human
commit, push, and tag gates; the Codex Operator contract; the plan-scoped
operator protocol; `herdctl health` and `herdctl observe` with a
schema-versioned observation model; Codex Gateway v0.1 with live compatibility
validation; the Telegram Remote Operator MVP with a numeric allowlist,
private-chat enforcement, one-shot fully bound plan approval, resumed sessions,
status, and verified-result delivery; the optional per-user LaunchAgent; and
DI-REMOTE-2 Remote Target Repository Routing.

## Phase 1: host survival and seams

In progress. Host survival and architectural seams, with v0.7.0 behavior
preserved. The list: Tailscale and SSH break-glass recovery; reboot and login
survival; service identity and readiness; `HumanInteractionAdapter`;
`OperatorSession`; `DurableExecution`; `Capability`; `Worker`; a DBOS spike; a
Pi spike; a Grok Bot spike. Telegram and Codex stay reference implementations
throughout. Correction (Task 8): the Grok Bot spike was built and is now
retired from the active product; Dots replaces the Grok Bot as the
human-facing mobile interface, with the limits stated in
[Task 8](#task-8-dots-as-the-human-facing-mobile-interface-and-grok-bot-retirement).

What exists in this checkout are the initial `OperatorSession`
`prepare()` / `execute()` seam, the `HumanInteractionAdapter` seam with
`TelegramHumanInteractionAdapter` behind it, and the first `DurableExecution`
and `Capability` seams. All are initial abstractions rather than the target
lifecycles, and the rest of the list is open. See
[architecture.md](architecture.md#16-current-implementation-notes).

## Phases 2 to 5

| Phase | Builds | Proves |
|---|---|---|
| 2: Mission Harness | Mission Manifest, Mission Registry, `M-####` identity, PREFLIGHT, the Mission Authorization gate, AWAITING_MISSION_AUTHORIZATION and NEEDS_REAUTHORIZATION, the lifecycle state machine, Authority Ledger, Evidence Graph, Blocker Ledger, proof requirements, Artifact Registry, budgets, continuation and checkpoints, event journal, snapshots, readiness graph, Reconciler, Observation Service. | The actual mission operating system exists and canonical state is the truth. |
| 3: Routing, attention, and Grok (corrected in Task 8: the human interface is Dots) | The natural-language Mission Router with deterministic ambiguity handling, the Attention Router, instant read-only status queries, the Grok interaction adapter with authorization and result cards and exact approval transport (corrected: the Grok Bot surface is retired; Dots is the human-facing mobile interface, and exact approval transport over it needs an authenticated exact approval binding that is NOT ESTABLISHED) (corrected again in Task 8, the ACTIVE policy: Mission approval over Dots is Operator-attested Dots-chat approval. The human replying exactly `approved` or `approve` IS the approval, under the human's declared trust that the Outer Operator relays the reply truthfully. It is recorded as `operator_attested_not_independently_verified`, which is explicitly NOT cryptographic or independently verified human provenance, and it carries a residual same-user fabrication risk. An authenticated exact approval binding remains NOT ESTABLISHED; it is deliberately delegated with that risk accepted, and is not a Task 8 acceptance blocker), the Telegram fallback adapter, shared-computer security tests, recovery and parity tests. | Grok becomes the preferred front door only once proven; Telegram remains the fallback. Corrected: Dots becomes the preferred mobile front door only once proven, and carries a Mission approval only once an authenticated route is demonstrated; Telegram remains the fallback. Corrected again in Task 8 (ACTIVE policy): Dots carries a Mission approval as Operator-attested Dots-chat approval under declared Operator trust, not as authenticated or independently verified human provenance. Full live Mission acceptance with real human participation remains unproven. |
| 4: Provider-neutral Operator | The full `OperatorSession` lifecycle, the Codex adapter, the Pi RPC adapter, bounded DI tools for Pi, provider and model selection, Domain Operator Profiles, Skill Packs, generated capability documentation, cross-model evaluations. | Provider replacement does not change mission authority or semantics. |
| 5: True multi-mission execution | Independent execution lanes, bounded admission, the Scheduler, per-mission queues, workspace, artifact, and approval isolation, fair scheduling, P0 to P3 priority, independent Herdr Pods, mission relationships, pause, resume, and abort, expensive-verification deduplication. | Three simultaneous missions with zero cross-contamination. |

## Phases 6 to 9

| Phase | Builds | Proves |
|---|---|---|
| 6: Evidence-native capabilities and delivery | BrowserCapability with read and write classification, stale-reference failure, screenshot and snapshot evidence, console and network evidence, ambiguous side-effect reconciliation, human browser handoff; Artifact Registry delivery and richer file types; the Action Risk Envelope; proof-complete feedback loops; the exact remote delivery ceremony in Grok (corrected in Task 8: over an authenticated human interface; over Dots this needs the authenticated exact approval binding that is NOT ESTABLISHED) (corrected again in Task 8: Mission approval over Dots is now Operator-attested Dots-chat approval under declared Operator trust. It is NOT cryptographic or independently verified human provenance, it carries a residual same-user fabrication risk, and it confers NO delivery authority. Delivery stays the separate, exact P1-A6 delivery authorization. Its authenticated remote ceremony over Dots, verified-result delivery fidelity and the live phone-to-PR loop all remain NOT ESTABLISHED). | Real user-path evidence and exact remote delivery are first-class. |
| 7: Chaos and worker fabric | Injected failure of Grok (corrected in Task 8: of Dots, where offline and revoked access are not cancellation), Telegram, the Runtime, the Operator, Herdr, sleep and wake, reboot, network, GitHub, model, quota, stale process, blocked mission, ambiguous browser action, interrupted result, artifact, and Git or release action, missed events; then the Worker Registry, leases, simulator, VPN, and GPU capability matching, retention, archival, compaction. | Multiple missions survive a hostile day without losing identity, authority, observability, progress, results, artifacts, or recovery information. |
| 8: Organizational learning | The Ops Steward, repeated-failure and same-mistake-twice detection, repetition-to-automation, bounded nightly missions, recurring monitoring, the postmortem flow, Skill and Profile proposal flow, control metrics, cost reporting. | Recurring interventions become deterministic machinery through a governed path; the Steward cannot change its own authority. |
| 9: Productization | A deterministic installer and upgrader, migrations, rollback, generalized target onboarding, a safe "go solve this issue" flow, an operational desktop, worker and environment onboarding. | Install once, connect a transport, point at a repository, start engineering, without hand-assembling infrastructure. |

The distribution end state is part of Phase 9: an installer that verifies the
host, installs and verifies the Herdr runtime, `herdctl`, `codexgw`, the
Operator integration, operator contracts, safety guards, the Telegram adapter,
and the Mac background service, then runs a deterministic health check and
confirms readiness; repository onboarding reduced to `herdctl init`; a desktop
app later, as a client of the same operator boundaries and never a replacement
execution path.

## Phases 10 and 11

Phase 10 is the general autonomous work fabric: engineering, research,
automation assessment, report generation, analysis, monitoring, planning,
browser QA, release preparation, and maintenance formalized as native mission
classes sharing identity, routing, observability, evidence, and authority
infrastructure, plus end-to-end operations transformation missions.

Phase 11 is the Visual Mission OS. It is last. It is an immersive projection of
canonical state over the Harness's APIs and events (a snapshot on startup, a
subscription after the last event sequence, catch-up or a fresh snapshot when
events were missed), and it never spawns Herdr, approves missions, marks
missions complete, creates Git authority, deploys, or rewrites durable state.
Acceptance requires every lower layer to be stable first. Munder Difflin is the
presentation design reference for it: its ideas about showing a multi-agent
organization are reused; its orchestration and authority backend are not.

### Why the visual world is last

A visual world is only worth building over state that is already canonical,
durable, observable, and recoverable. Built earlier, it would either invent
state the backend does not hold or become a second control path around the
authority model, which is the one thing the design forbids. Built last, it is a
consequence of real events: Supervisor planning, Executor working, Reviewer
rejected, mission blocked, verification running, artifact registered, human
approval ready, delivery locked. If it crashes, the missions keep running. The
renderer is never the backend.

# Where the system is today

## Foundation proven by the historical external-target mountain

The historical external-target mountain is TERMINAL. It exposed genuine
post-dispatch policy drift and correctly terminated BLOCKED at
`broker_verification_policy_drift`.
Its identifiers, for the record: workflow `wf-2c901885473fc4781bf82296`,
target Herdr task `20260830-094026-9fef2d`, target baseline
`3e1833d930723ef4f7220698c98155a925591d4d`, from a natural-language request
targeting an external repository issue.

Before it terminated, that mountain did prove the following DI-REMOTE-2
foundation outside tests:

-   ~~A natural-language Telegram request can trigger a fresh restrictive
    Codex planning turn and render a bounded Mission Authorization.~~
-   ~~A Telegram button approval can durably authorize exactly that mission
    while typed text carries no authority.~~
-   ~~Runtime can claim the authorization from the durable workflow store
    without dispatching another Gateway turn.~~
-   ~~Runtime can materialize an isolated target workspace at the exact
    authorized baseline with no manual clone, registration, terminal, or
    target Herdr setup.~~
-   ~~Target instructions can be collected during preparation and the exact
    bounded handoff can pass a fresh restrictive handoff-validation turn.~~
-   ~~Broker/Runtime can dispatch the byte-exact handoff while preserving
    `delivery_authority: none`.~~
-   ~~The target Herdr can bootstrap unattended with Supervisor, Lead,
    Executor, and Reviewer all registered and interactive-ready.~~
-   ~~Supervisor remains the first strategy-bearing component and can hand
    execution to Lead/Executor while Reviewer waits adversarially.~~
-   ~~Durable `/status` can report a live DI-REMOTE-2 workflow phase and
    target task while the mission continues independently.~~
-   ~~The live target preserves human Git gates (`commit: require-human`,
    `push: require-human`) and cannot deliver from Mission Authorization.~~
-   ~~Owned role-turn spawning preserves PATH lookup and process/session
    ownership, with the full committed regression suite green.~~
-   ~~The target Herdr task reached COMPLETE and a canonical target Reviewer
    APPROVE was recorded.~~
-   ~~Target observation refreshed from a stale `ACTIVE` reading to
    `COMPLETE`.~~

Terminal outcome. The workflow then stopped BLOCKED at
`broker_verification_policy_drift`. `verified_result` and
`result_delivery` remained null. No target Git delivery occurred: the
target stayed at baseline `3e1833d930723ef4f7220698c98155a925591d4d`
carrying an implementation diff only. Everything downstream of the drift stop did not run in that historical
execution. The defects it exposed were subsequently closed, the Runtime
stabilization lineage was integrated into `main`, and the corrected
verification plus exactly-once final-result path was certified hermetically
and adversarially for v0.7.0. A fresh post-fix live mountain is not used as
release evidence. Separate artifact delivery remains outside that
certification. DI-REMOTE-2 acceptance is COMPLETE for the v0.7.0 release
candidate.

## Mission Core and Mission State progress

Two provider-neutral Mission layers now exist under `mission/`, and a third
that consumes them only through a read-only observation contract exists
under `coordination/`, in different states of delivery. The distinction
matters and is recorded exactly:

-   **Task 4, Mission Core: MERGED to `main`** (PR #33). Stable Mission
    identity, exact revisioned proposals, transport-neutral human
    APPROVE / EDIT / DENY decisions with truthful provenance, exact Mission
    Authorization, the append-only Authority Ledger, and the one
    fail-closed validation path. Approval moves a Mission to AUTHORIZED
    and starts nothing.
-   **Task 5, Mission State: MERGED to `main`** (PR #34, merge commit
    `5ff9ff3`). It extends the same `missions.json` document and
    `MissionService` with the proof contract (approved inside the proposal
    and bound by its digest, so changing it is an EDIT plus a fresh
    APPROVE), the Evidence Graph with separate submission and acceptance,
    the Artifact Registry with original-input linkage, the Blocker Ledger,
    restart-safe checkpoints, bounded continuation and closure, and
    Mission-safe dependency and readiness state. Evidence is deterministic
    proof; narrative claims and process exit can never satisfy a
    requirement. Nothing in it routes, dispatches, schedules, observes,
    reconciles, fetches, executes or delivers. It has not been released or
    run live; its acceptance rested on the focused hermetic suite and the
    single serialized full CI-shaped validation of the frozen candidate.
-   **Task 6, Mission Routing + Attention + Bot Coordination: IMPLEMENTED
    on branch `phase1/mission-routing`, NOT delivered.** A separate
    provider-neutral package, `coordination/`, that never imports
    `mission`: canonical Mission facts reach it only as observation values
    a caller injects through one narrow read-only contract (exact Mission
    and proposal revision, a decimal state cursor, freshness and
    provenance, conditions, and Mission-local evidence/artifact reference
    records carrying their validity provenance). It holds its own atomic
    document (`coordination.json`, own lock, closed keys, schema version,
    write-sequence conflict guard, hard caps, fail-closed load) with four
    record families: bounded conversation bindings (every kind
    revision-exact; stale context clarifies, and rebinding is a separate
    act routing never performs), durable route decisions (context-bound
    idempotent replay; the deterministic tiers of Iteration 1 — explicit
    Mission id, reply-to, approval/result presentation, exact repository
    or issue reference, known project, durable alias, unique conversation
    match — with the closed outcomes `existing_mission` / `new_mission` /
    `clarification_required`, and the engineering lane selected as a
    VALUE with explicit refusal reasons), attention records (BLOCKED,
    NEEDS_HUMAN, AUTHORIZATION_READY, RESULT_READY with deterministic
    priority, durable duplicate suppression, and presentation only under a
    fresh matching observation; acknowledgment records the human act and
    resolves nothing), and bot handoffs (eligible roster participants,
    citations only at the validity level the observation proves, one
    proven forwarding path, exact revision compatibility on every
    transition; a handoff transfers context and request only). Every
    record carries `authority: "none"`. The **bounded natural-language
    routing turn (Iteration 1 step 8) is NOT implemented**, and neither
    are the Event Journal, the Mission Observation Service (the binding of
    the observation contract to the live Mission store), the Reconciler, a
    scheduler, live bot messaging or live dispatch; those remain Task 7
    and later. Nothing in it launches a Mission or Capability, sends a
    message, starts Herdr work or performs delivery. It has not been
    merged, released or run live.

## Task 8: Dots as the human-facing mobile interface, and Grok Bot retirement

**Status: IN PROGRESS (lifecycle ACTIVE), UNACCEPTED, not delivered.** A
bounded local candidate is reviewed (canonical APPROVE) and Lead accepted as
a LOCAL candidate only; that is not Task 8 acceptance. [SUPERSEDED: that was
the earlier local candidate. Since then the operator-attested approval was
accepted at canonical round 13. The run route was REJECTED at rounds 14 and
15, and its fixes are in a single final combined candidate awaiting one
bounded final review. That candidate's exact identity is in the operator
checkpoint. Task 8 stays ACTIVE and UNACCEPTED.] [Current, 2026-10-04: one
Operator-mediated live V6 exercise ran end to end, as observation (4)
below: Dots → Codex → DI → Herdr → Dots, plus a separate standalone P1-A6
PR. That is not Task 8 acceptance. Task 8 stays ACTIVE and UNACCEPTED until
the Outer Operator's own confirmation, and nothing here starts Task 9.] The
position, in full in
[architecture.md](architecture.md#3-interaction-dots-the-human-facing-mobile-interface):
Dots is the human-facing mobile interface; Codex is the Outer Operator;
Dodging Infinity alone owns Mission identity, authority, lifecycle, durable
state, dispatch, evidence, reconciliation, and delivery gates; Herdr is the
engineering execution system; Muse is not the Operator. The Grok Bot
interaction surface is retired; Grok as a model provider is not.

DEMONSTRATED, user-observed and unedited: (1) reach, read, and reply, where a
phone-initiated local Codex task read a repository file and returned its exact
content plus session id `01a10275-e9b0-70fd-bc29-9724f5fd60e9`; (2) a
same-task connected continuation that read `.herd/state/task.json` and returned
task `20261003-115300-782f3a` / `ACTIVE` with no edits, a status answer from a
durable record rather than chat history. That is a connected same-task
continuation and a small durable read only: not a new task, not a reconnect
after an outage, not a large payload.

(3) Live phone exercise, 2026-10-03, human-reported and independently
corroborated from the durable stores: phone → Dots → the SAME local Codex task
→ a local DI proposal (`python3 direquest.py … propose`) → durable status →
phone, with matching request `lr-06dfa637b5a65affd594b085b5dc6432`, Mission
`mn-5d9af9029527d73a9b38c22781d9d7a8`, revision 1 and proposal digest
`90cfd77bf1ef9a32d1a1ec37c6b960ef4f850790197178c589326320c7fedeb0`; the request
OPEN, the Mission AWAITING_DECISION and NOT_STARTED. There were ZERO approval
attempts, zero decisions, zero authorization ids, zero authorizations and no
dispatch. The approval block in the status output describes the fail-closed
policy; it does not show that `approve` was invoked, and no refusal was
observed. This establishes proposal identity presentation and connected
status retrieval only: not authenticated intent, not full-payload or result
fidelity, not outage recovery. The pending Mission has not executed.

(4) Live V6 exercise, 2026-10-04: Operator-mediated, not Dots-autonomous.
- Dots (the same local Codex task) showed the exact Mission
  `mn-079a81327f76cda72d79eb241ada92d3` (revision 1, digest `7a83af04…`), and
  the human sent a separate `approved` reply.
- The Outer Operator ran the effectful DI commands; Dots' local command
  permission repeatedly refused DI propose and approve.
- The approval is Operator-attested, with the same-user fabrication risk
  accepted. Independent authenticated human provenance is still NOT
  ESTABLISHED.
- DI recorded one dispatch receipt. Herdr child `20261004-172125-960973`
  completed in an isolated smoke clone (Lead verified, Reviewer round 2
  APPROVE), changing only the one-line marker `docs/task8-dots-smoke.md`
  (`15e0281d…`).
- DI `verify` completed the Mission with `engineering_verified: true`
  (evidence `mv-5e4e0a62…`).
- The human reports Dots read the token-free durable files
  `dots-phone-status-v6.json`, `dots-phone-verified-result-v6.json` and
  `dots-phone-pr-result-v6.json` and returned them. Dots did not rerun
  verification or query GitHub or DI.
- Delivery stayed separate. The standalone P1-A6 delivery
  `prd-85cad864f6ae89b72c1c483d` (`mission: null`), approved by its own
  Operator-attested Dots reply, completed COMMIT, PUSH and PR_CREATE and
  opened PR #37, which is open and not merged.
- An earlier delivery, `prd-e87f7490…`, is REVOKED (GitHub GH007, private
  email), with no remote branch or PR.
- The Outer Operator, not this herd, independently checked GitHub and the
  delivery store.
- The Mission's DI status truthfully stays `delivered: false`: there is no
  Mission-parent receipt.
- The parent's `.herd/state/children.json` still caches the child as
  `ACTIVE`, while the copied verified result and checkpoint report it
  COMPLETE.

Not demonstrated live: an unavailable machine or session, disconnection or
restart around a decision or dispatch, and live pause or cancel of a running
Herdr child. Local deterministic tests are not substitutes. DI pause gates
DI progression only, not worker suspension, and a production Herdr cancel
can report HOLD.

NOT ESTABLISHED, and blocked wherever success is described: authenticated exact
approval binding [SUPERSEDED: deliberately delegated under the user's trust
decision, with the same-user fabrication risk accepted; not a Task 8
prerequisite or acceptance blocker]; outage and restart recovery; background notification and
durable event delivery; verified-result delivery fidelity [SUPERSEDED in part
by (4): one human-reported return read from a durable file]; full live
Mission acceptance [SUPERSEDED in part by (4): one Operator-mediated live
run; acceptance itself is still open]. Offline is not revoked, and neither is a cancellation mechanism: a
running local Dots task may finish after admin access is disabled. The
structured plugin / MCP route is UNPROVEN, not impossible: the inline or
file-declared MCP import is Desktop-only and not the mobile surface; a
connection would need a registered app reference, a Secure MCP Tunnel, or
public HTTPS (no usable DI connection has been established, and setup is not
authorized in this task); and OAuth or mTLS alone would
not prove exact human approval of a Mission revision. No existing Dodging
Infinity structured connector may be assumed, and the user states there is
likely none; a catalog search cannot enumerate installed plugins, so no match
is not absence.

Acceptance criteria, each with its current status:

1.  The Grok Bot interaction surface (`grok_mcp`, `grokmcp.py`) is absent from
    the active product and startup path; history stays in this roadmap and
    [CHANGELOG.md](../CHANGELOG.md); Grok model-provider references stay.
    Status: implemented, locally verified, reviewed and Lead accepted as a
    local candidate (pinned by `tests/test_grok_bot_retirement.py` and
    `tests/test_static.py`); not delivered.
2.  The roadmap, architecture, operations, and README state the position above
    with the DEMONSTRATED / NOT ESTABLISHED split. Status: the local request
    surface and its direct `python3 direquest.py` setup are documented and
    reviewed (local candidate, not delivered); the installer is untouched.
    [Live V6: the docs now record observation (4) and its limits (local
    candidate, under review).]
3.  Mission approval over Dots fails closed until an authenticated exact
    approval binding is demonstrated, and reports which route is missing what.
    A typed identity, a synthetic fixture, a local console decision, or a
    generic Codex task permission never satisfies it. Status: the fail-closed
    local behaviour is implemented and tested (reviewed local candidate);
    actual Dots authorization is BLOCKED on a missing authenticated channel
    and exact decision binding. No Dots adapter, ingress, MCP server, or
    tunnel is built. Investigation: no supported DI-verifiable route is
    established in the inspected interfaces (provisional, not universal). A
    decision-ready DRAFT approval-page design (APPROVE-only; phone browser,
    transaction-bound WebAuthn assertion, existing `apply_human_decision`;
    declining stays the existing cancel / withdraw path, which is not a
    Mission decision) awaits the human's
    product and setup decisions. Its gates: G1, a privilege-path preflight; G2,
    the unproven assumption that a same-user agent cannot silently use a
    synced passkey (a hardware key is the stronger factor). Nothing is built or
    activated. [SUPERSEDED: the approval-page plan is frozen history; the user
    chose operator-attested Dots-chat approval. Current status: implemented
    locally. The whole-string `approved`/`approve` reply is relayed through
    `attest-approval`, labelled `operator_attested_not_independently_verified`,
    with the residual same-user fabrication risk recorded, and it is exactly
    bound with one authorization. Independent authenticated human provenance
    remains NOT ESTABLISHED and is DELIBERATELY DELEGATED, with the fabrication
    risk ACCEPTED; it is not a Task 8 prerequisite or acceptance blocker.]
    [Live V6: the Operator-attested Mission approval and a separate
    Dots-attested P1-A6 delivery approval were each exercised live once,
    Operator-mediated.]
4.  Cancellation and late-write prevention are a durable local Dodging Infinity
    record that the write and decision paths consult and that fails closed,
    never the transport, the connection, or the vendor grant. Status:
    reviewed local candidate (not delivered), synthetic test evidence only, for
    the creator's own pending revision-1 proposal: `direquest.py cancel` with
    the one-time control
    capability cancels the local request and records a withdrawal marker
    that Mission Core's decision path refuses on. Not a lifecycle state; the
    Mission stays `AWAITING_DECISION`. Not exercised by the live phone
    exercise. Control of a running Mission or Herdr work: UNPROVEN, not
    implemented. [SUPERSEDED, current candidate: `cancel-run` records a durable
    cancel first, so every later write is refused, and reports which of four
    achieved states occurred. Quiescence is never claimed when unprovable: a
    production Herdr target exposes no Dodging Infinity-owned process group,
    so its cancel reports HOLD. `pause` halts Dodging Infinity's progression
    only; external in-flight work is NOT suspended, and no supported suspend
    seam exists.] [Live V6: neither pause nor cancel was exercised against a
    running Herdr child, so this stays demonstrated locally only.]
5.  Status answers come from durable records. Status: DEMONSTRATED for
    connected same-task task status (observation 2) and for proposal and
    Mission status (observation 3). Reconnect, outage, a missing session,
    large results and background delivery are not established. [Live V6:
    the completed Mission's status and verified result were returned from
    durable files (observation 4). Reconnect, outage and a missing session
    are still not established.]
6.  Regression coverage labels synthetic coverage as synthetic, and no fixture
    simulates an authenticated Dots principal or approval. Status: focused
    tests and an independent adversarial review passed; fixtures cannot
    establish a Dots principal. The full per-file suite is NOT green: it fails
    only in `tests/test_workspace_trust.py` (the promptable-screen assertion),
    a failure reproduced on the pristine baseline `96c3f47`, whose root cause
    is UNPROVEN.
7.  A bounded, reversible live phone acceptance with real user participation.
    Status: the proposal and status sub-exercise passed (observation 3). Full
    authorized DI → Herdr execution, progress, review and proof
    reconciliation, and verified-result return remain NOT ESTABLISHED, so the
    criterion is not satisfied. A local Reviewer APPROVE is never full Task 8
    acceptance. [Current candidate: the callable run route exists locally.
    `direquest.py dispatch | observe | reconcile | prove | verify | result |
    pause | resume | cancel-run` drives an AUTHORIZED Mission through the real
    Herdr spawn bridge, tested only with injected recorders. Request
    compatibility is checked against Herdr's own validator. VERIFIED needs the
    whole approved contract, with a recoverable pending-proof state. Delivery
    is derived from attested P1-A6 receipts and stays separately authorized.
    None of this has run live; real live dispatch and the phone-to-PR loop
    still need exact human authorization.] [SUPERSEDED, live V6
    (observation 4): one Operator-mediated live run covered real user
    participation, one dispatch, the child's review, DI verification
    (`engineering_verified: true`), a human-reported verified-result return
    and a separate standalone PR (#37), with the Mission still
    `delivered: false`. Earlier live attempts (w37, w39 and two later smoke
    Missions) each exercised HOLD, then `reconcile`, then BLOCKED after a
    failed start. Disconnection, restart and progress recovery of a running,
    successful Mission, and live pause or cancel, were not demonstrated. The
    criterion is not satisfied as Task 8 acceptance until the Outer
    Operator's own confirmation.]

The local candidate changes 37 files (20 unstaged modifications, ten staged
deletions, seven untracked files), shown as 34 collapsed `git status
--porcelain` lines. [SUPERSEDED: that count describes the earlier candidate.
The final combined candidate's identity, with its untracked hashes and
covering review rounds, is recorded in the operator checkpoint.]

## Immediate release gate: DI-REMOTE-2 acceptance before Phase I

The remote mission fabric does not begin from an unaccepted moving target.
The release sequence is now:

1.  [x] Complete the README and documentation reconciliation.
2.  [x] Repair clean-clone CI hermeticity: runner-equivalent local validation
    passed, and all four PR matrix jobs (macOS and Ubuntu x Python 3.9 and
    3.13) are green in CI run `33330263889` at
    `4eea64f2a915e988dbfd73ad51dd9f6546bc6a8f`; the branch also passed at
    `52a97b71a3b5c9f20ff33d4feb1332284cd825b7`.
3.  [x] Preserve the historical external-target mountain as terminal diagnostic
    evidence: it reached target Herdr COMPLETE and then correctly stopped
    BLOCKED at `broker_verification_policy_drift`.
4.  [x] Integrate the reviewed and pushed Runtime stabilization commit
    `d8ec2af409e4086f985be03371a872a84a3767ec` from branch
    `fix/runtime-terminal-reconciliation` into `main`.
5.  [x] Complete final DI-REMOTE-2 certification on the stable tree:
    continuation task `20260901-165812-045b0c` reached COMPLETE, Reviewer
    persisted APPROVE, and the authoritative discovery ran 2,048 tests with
    `OK (skipped=1)` and exit 0.
6.  [x] Prepare the v0.7.0 release tree with reconciled public docs and
    preserved historical evidence.
7.  [x] Prove the exact v0.7.0 release tree green in CI; tag publication is
    governed separately by the human authorization gate.

Historical stabilization evidence remains part of the release record:
task `20260830-185309-4c3db7`, final canonical Reviewer round 6 APPROVE,
focused regression 159/159, `tests/test_target_runtime.py` 250/250, static
checks PASS, Python 3.9.6 compile PASS, and `git diff --check` PASS. The
historical repository-wide LIVE working-tree loop stood at 35/37 solely
because pre-existing live `.herd` specimen assertions in
`tests/test_hermetic_git.py` and `tests/test_reconcile_audit.py` predate
that task.

The historical external-target mountain remains truthful historical evidence:
it terminated BLOCKED before final verification/result delivery. The corrected
final-result contract is certified by the later hermetic/adversarial release
evidence; a fresh post-fix live mountain is not used as release evidence.
Separate artifact delivery is not claimed by that certification.

Acceptance:

> DI-REMOTE-2 acceptance is complete when the public repository, exact release
> commit CI, canonical review evidence, and authoritative unchanged-tree test
> run all describe the same bounded system.

# Phase I: Remote Mission Fabric

## Iteration 0: Trusted Mac stabilization and break-glass access

Reconcile the host before more feature work.

Work:

-   ~~synchronize `main` and `origin/main` at
    `cda06d8c502882672667d94821b8bd00e7060a52`~~
-   ~~migrate Telegram durable state to the current schema~~
-   ~~reload current tgop and dirun after code changes and verify fresh
    running processes~~
-   ~~verify dirun, target Herdr bootstrap, Codex execution, Git human
    gates, launchd, config, and durable workflow state through the active
    portion of the live DI-REMOTE-2 mountain~~
-   ~~integrate the pushed Runtime stabilization commit
    `d8ec2af409e4086f985be03371a872a84a3767ec` and assemble a stable `main`~~
-   ~~certify independent verification, VERIFIED/COMPLETED, and exactly-once
    final-result delivery hermetically and adversarially on the stable tree~~
-   configure Tailscale plus SSH, restricted to trusted devices/accounts
-   avoid public inbound SSH exposure
-   verify persistence across reboot/login

Current state: the Runtime stabilization lineage is integrated, DI-REMOTE-2
acceptance is complete for the v0.7.0 release tree, and the corrected
final-result contract is certified on the stable tree. A fresh post-fix live
mountain is not used as release evidence. Remaining Iteration-0 work is the
break-glass Tailscale/SSH and reboot/login persistence work; release tagging
is governed separately by its human authorization gate.

Test from a genuinely remote network with Telegram healthy, Telegram
stopped, Runtime stopped, Codex wedged, Herdr wedged, and a stale
LaunchAgent.

Acceptance:

> If Telegram completely dies while I am away, I can still securely
> reach the trusted Mac and recover it.

## Iteration 1: Durable Mission Registry and Mission Router

Create a first-class layer above mission-specific Codex sessions.

Each mission receives a durable ID such as M-0042 plus: - title -
original intent - mission type - status - source chat - Codex session -
Herdr task - workflow ID - target - aliases - timestamps - result
state - artifact state

Routing order should prefer deterministic evidence: 1. explicit mission
ID 2. Telegram reply-to binding 3. approval/result binding 4. exact repo
or issue reference 5. known company/project 6. durable aliases 7. unique
contextual match 8. bounded fresh routing-model turn

Allowed model outcomes:

``` text
existing_mission: M-0042
new_mission
clarification_required
```

If "Why is this taking so long?" could refer to several missions, ask
which one rather than guessing.

Progress: durable `wf-*` workflow identity, Telegram binding, target identity,
and task identity now exist and survive independently of the Gateway turn. The
first-class Mission registry is the merged Mission Core (`mn-*` identities,
Task 4). The deterministic routing tiers 1 to 7 above, the closed outcomes,
bounded conversation bindings, durable idempotent route decisions, the
Attention Router's projection of what needs a human, and bot coordination
against a per-Mission roster are implemented in `coordination/` on branch
`phase1/mission-routing` (Task 6, not delivered; see "Mission Core and Mission
State progress"). Step 8, the bounded natural-language routing-model turn,
remains open: when no deterministic tier resolves, Task 6 clarifies rather
than guessing.

Acceptance: the historical external target, Silvi, and another mission can all be active and
natural-language follow-ups reliably reach the right mission.

## Iteration 2: Per-mission asynchronous execution lanes

Remove long-running missions from Telegram's single serialized worker.

``` text
Telegram inbound
  -> Mission Router
       -> M-0041 queue -> Codex / Runtime / Herdr
       -> M-0042 queue -> Codex / Runtime / Herdr
       -> M-0043 queue -> Codex / Runtime / Herdr
```

~~After durable authorization/dispatch, the Telegram request ends and the
mission continues independently under Runtime/Herdr.~~

The remaining work is true per-mission concurrency and queue isolation rather
than merely decoupling one long-running mission from the inbound Telegram turn.

Add bounded concurrency: - maximum active missions - maximum
simultaneous Herdr tasks - explicit capacity behavior - fair
scheduling - isolation between queues, approvals, contexts, state, and
artifacts

Acceptance: start Silvi, then an external-target mission, then a third mission, and continue
interacting with all three while Telegram remains responsive.

## Iteration 3: Out-of-band observability and mission control

Core commands:

``` text
/missions
/status
/status M-0042
```

Global status should show adapter health/version, disk vs running
commit, schema, Runtime health, active model turns, active/queued
missions, Herdr tasks, blocked/stale missions, and result/artifact
delivery backlog.

Mission status should show lifecycle phase, elapsed time, current Codex
activity, Herdr task, agent states, latest durable progress, review
round, result state, artifact state, and health classification.

Hard requirement: status reads durable state and bounded read-only
observability directly. It never waits for the mission-specific Codex
turn.

~~Live proof: `/status` read the durable v2 workflow store while the
historical external-target mission WAS ACTIVE and reported Runtime state, workflow phase,
target, and target Herdr task without waiting for the mission Codex turn.~~ The richer
mission-control surface above remains open.

Acceptance:

> /status responds within roughly five seconds while an eight-hour
> mission is running.

## Iteration 4: Runtime identity, upgrade readiness, and safe service lifecycle

Prevent a repeat of the stale v1 Telegram process.

Every long-running service should expose:

``` text
service
pid
started_at
running_version
running_commit
disk_version
disk_commit
state_schema
required_schema
config_path
health
```

Detect new code on disk, schema mismatch, missing migration, outdated
LaunchAgent, missing/moved executables, and detectable auth problems.

Add fixed operational actions such as health, restart Telegram operator,
restart Runtime, and reload after upgrade. These are not arbitrary
shell. They must inspect in-flight state and refuse unsafe restart.

Acceptance:

> New code on disk cannot leave an apparently healthy obsolete daemon
> running silently.

Progress: ~~Telegram Operator and Runtime can be deliberately reloaded onto a
new committed control-plane increment and verified with fresh singleton PIDs.~~
Automatic running-commit/disk-commit skew detection and safe in-flight restart
policy remain open.

## Iteration 5: Telegram exact delivery authority and Git decision surfaces

Make the phone capable of completing the delivery ceremony without weakening
the human gate. This is now a core requirement, not an optional someday
feature.

The phone-facing ceremony is explicit and ordered:

``` text
Verified result
  -> Inspect exact diff / evidence
  -> Prepare commit
  -> Approve commit
  -> Commit receipt
  -> Approve push OR Open PR
  -> Push / PR receipt
  -> Optional later: Approve merge / tag / release / deploy
```

Mission Authorization grants **ZERO delivery authority**. Delivery begins only
after the mission is complete, Reviewer-approved, and independently verified.
Each delivery action is an independent one-shot capability; no action inherits
authority from Mission Authorization or from another delivery action.

The delivery model must use closed, one-shot capabilities:

-   `Prepare commit` is read-only: compute and render the exact target repo,
    mission/workflow ID, baseline, current HEAD, diff summary, changed paths,
    staged-tree/diff digest, validation evidence, Reviewer decision, and
    proposed commit message.
-   `Approve commit` binds the exact repository, mission/result revision,
    HEAD, exact staged bytes/digest, commit message, human/chat, expiry, and a
    one-shot nonce. Any byte, HEAD, mission revision, or policy change
    invalidates it. Typed Telegram text cannot authorize it.
-   Commit execution is deterministic and uses the existing Herdr/Git commit
    gate; no `--no-verify`, no arbitrary shell, and no authority reuse.
-   `Approve push` is a **separate** one-shot capability bound to the exact
    resulting commit SHA, remote/ref, expected remote state, human/chat,
    expiry, and nonce. Commit approval never implies push approval.
-   `Open PR` / PR update is another closed action bound to the exact source
    commit, destination, title/body digest, and current remote state.
-   `Approve merge` is separate from PR creation and binds the exact PR, head
    SHA, base, merge method, required checks/reviews state, human/chat, expiry,
    and nonce.
-   `Approve tag`, `Approve release`, and any future `Approve deploy` each
    require their own capability. Release binds the exact tag/commit and
    release body/artifact digests. Deploy binds the exact immutable revision
    and environment.
-   No delivery action inherits authority from Mission Authorization, commit,
    push, PR creation, or any other delivery action or mission.
-   Every delivery attempt writes a durable receipt with `prepared`,
    `authorized`, `executing`, `succeeded`, `failed`, or `ambiguous` state and
    reconciles uncertain external effects before allowing another attempt.
-   `/status` must surface pending delivery decisions, exact bound commit/ref,
    expiry, and any ambiguous or blocked delivery state.

Acceptance:

> I can receive a verified engineering result in Telegram, inspect the exact
> diff/commit proposal, approve one local commit from the phone, then separately
> approve its exact push or PR without touching the Mac, while a stale or
> replayed button can never authorize a different result.

## Iteration 6: First-class artifact delivery

Start with reviewed Markdown artifacts.

Artifacts must: - belong to one mission - live in an approved artifact
location - be registered in durable state - have an exact digest - have
allowed type and bounded size - pass containment checks - reject
symlinks/devices/FIFOs/unrelated files - have explicit delivery state

Delivery states should distinguish pending, reserved, partial,
delivered, ambiguous, and failed.

Later add PDF, CSV, XLSX, DOCX, PPTX, and images deliberately rather
than allowing arbitrary files.

Acceptance:

> Request the Silvi mission from the phone and receive the
> Reviewer-approved Markdown artifact without touching the Mac.

## Iteration 7: Multi-mission mountain and chaos test

Run at least three concurrent missions: - external-target engineering -
Silvi operational-automation research - one unrelated third mission

Inject failures: - kill Telegram adapter - kill Runtime - kill a Herdr
process - sleep/wake Mac - disconnect/reconnect network - reboot Mac -
temporarily lose Codex access - hit model quota - leave one mission
blocked - create stale runtime after update - interrupt result/artifact
delivery

One mission failure must not take down another. Uncertain external
effects must reconcile deterministically, block durably, or require
explicit human recovery.

Acceptance:

> Operate multiple missions remotely for a full day under hostile
> conditions without losing identity, authority, observability,
> progress, results, artifacts, or recovery information.

# Phase II: Always-On Trusted Host

## Iteration 8: Reboot, login, sleep/wake, and network resilience

Validate cold reboot, login startup, service ordering, sleep/wake, long
sleep, Wi-Fi loss/recovery, router restart, DNS failure, and temporary
GitHub/Telegram/model outages.

Acceptance: ordinary host and network lifecycle events do not require
physical intervention.

## Iteration 9: Host readiness and dependency graph

Create one deterministic readiness model covering Telegram, Mission
Router, Runtime, Codex, Herdr, GitHub, Git credentials, artifact
delivery, and target child bootstrap.

Expose READY, DEGRADED, BLOCKED, and actionable reasons.

~~Target-child bootstrap readiness is now a durable production receipt: the
historical external-target mountain recorded all four logical roles registered and
interactive-ready before engineering proceeded.~~ The broader host dependency
graph remains open.

Fail before consequential dispatch when a required dependency is known
unavailable.

Acceptance:

> Before accepting consequential work, Dodging Infinity can tell whether
> the host can actually execute it.

## Iteration 10: Durable-state hygiene and long-run maintenance

Define bounded retention, archival, compaction, and cleanup for
completed missions, Codex metadata, Herdr history, reviews, workflow
records, Telegram bindings, artifacts, logs, stale approvals, and
migration backups.

Cleanup must never destroy active authority or audit evidence.

Acceptance: months of operation without manual state-directory
housekeeping or unbounded growth.

# Phase III: Mission Fabric Maturity

## Iteration 11: Mission priority, capacity, and scheduling

Add explicit resource management: - mission priority - queued vs
active - bounded concurrent Herdr work - model quota awareness - fair
scheduling - pause/resume where semantics permit - urgent capacity
reservation - starvation prevention

Router owns identity. Scheduler owns capacity. Do not combine them.

## Iteration 12: Mission relationships and compound work

Support explicit parent/child, dependency, follow-up, and supersedes
relationships.

Example:

``` text
Parent: Research Silvi
Child: Build pilot architecture
```

Related missions never automatically inherit each other's authority.

## Iteration 13: Rich remote decision surfaces

Evolve Telegram into a clear mission console with bounded controls for:

-   mission selection
-   status
-   artifacts
-   blocked-condition acknowledgement
-   permitted recovery
-   mission approval/rejection
-   exact diff inspection
-   `Prepare commit`
-   `Approve commit`
-   `Approve push`
-   `Open PR` / PR update
-   `Approve merge`
-   tag/release/deploy approval when enabled
-   authorization history
-   expiry/replay state
-   exact-result receipts

Every control remains bound to exact mission, revision, human, chat, and
durable state.

# Phase IV: Distribution and Productization

## Iteration 14: Deterministic installer and upgrader

Target:

``` text
Install Dodging Infinity
  -> verify host
  -> install/verify Herdr, herdctl, Codex integration, Telegram, Runtime, Router, guards
  -> configure credentials
  -> run readiness check
  -> READY
```

The upgrader must understand runtime/disk skew, migrations, service
reload, and rollback/recovery.

## Iteration 15: Repository and target onboarding

~~For the historical external-target path, remote target setup now requires no manual
clone, registration, terminal, or Herdr setup: Runtime materialized the pinned
workspace, collected target instructions, bootstrapped Herdr, and dispatched the
mission from one Telegram authorization.~~

Generalize that proof across supported repositories and complete remaining
target hardening exposed by live DI-REMOTE-2 testing, including path-scoped
target instruction handling and repository-specific compatibility edges.

Acceptance:

> "Go solve this issue" is enough to establish a safe isolated target
> mission when the target is supported.

## Iteration 16: Desktop control application

Build a local client for setup, health, missions, configuration,
credentials, service management, logs, and recovery.

It remains a client of the same authority model and never becomes an
alternate execution path around Codex, Runtime, Broker, or Herdr.

# Phase V: Broader Autonomous Work Platform

## Iteration 17: Generalized mission types

Formalize native mission classes: - engineering - research - operational
automation assessment - document/report generation - analysis -
monitoring - planning

Different mission types can have different artifact expectations and
Herdr guidance while sharing identity, routing, observability, evidence,
and authority infrastructure.

## Iteration 18: AI operations transformation missions

Formalize the Infinity/Ocean Block style of work.

Given a company or industry, Dodging Infinity should: - investigate
manual processes - find humans acting as middleware between systems -
reconstruct current-state workflows - identify reconciliation, inbox,
spreadsheet, document, approval, and exception work - design AI
orchestration that sits across existing systems of record - identify
what human work disappears - estimate ROI mechanisms - design pilots -
adversarially review the analysis - produce implementation-ready
artifacts

Acceptance:

> A company prompt can become a Reviewer-approved automation opportunity
> map and pilot architecture.

# Long-Term Milestones

## Milestone A: Remote survivability

The Mac is recoverable from anywhere even when Telegram is broken.

## Milestone B: DI-REMOTE-2 released

The v0.7.0 release tree has completed DI-REMOTE-2 acceptance and exact
release-tree CI is green. Tag publication is governed separately by the human
authorization gate. The v0.7.0 release tree is the baseline for Mission Router
and concurrency work.

## Milestone C: Multi-mission operation

Telegram can naturally control multiple concurrent missions without
context collision.

## Milestone D: Immediate observability

Status is always available independently of active work.

## Milestone E: Full remote delivery authority

A verified result can move through exact, one-shot, human-approved commit and
separately authorized push/PR actions from Telegram without granting ambient or
replayable delivery authority.

## Milestone F: Artifact-native work

Research and engineering missions return durable reviewed files, not
only chat text.

## Milestone G: Unattended reliability

The trusted Mac survives ordinary host/network events and exposes
readiness before work starts.

## Milestone H: Productization

Install, configure, upgrade, diagnose, and operate Dodging Infinity
without manually assembling its infrastructure.

## Milestone I: General autonomous work fabric

Engineering, research, operational automation analysis, and other
bounded mission types all operate through the same durable mission
architecture.

# Final Product Principle

> Codex operates. Herdr engineers. Humans authorize consequential
> boundaries from wherever they are. The transport never weakens those
> boundaries. The Mission Router keeps the mission fabric coherent.

The interface should become simpler while the authority model underneath
remains rigorous.
