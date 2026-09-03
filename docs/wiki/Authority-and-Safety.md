# Authority and Safety

Back to [Home](Home.md). Labels are defined on the Home page.

This is the most important page in the wiki. Everything else depends on the rule it states: humans are the root of consequential authority, and nothing else mints it.

## Intent is not authority

**[IMPLEMENTED / PROVEN]** A natural-language request communicates intent. A Mission Authorization communicates permission. They are different things, and v0.7.0 keeps them apart in code: a Telegram request produces a closed-schema Mission Authorization through a separate fresh planning turn; the human approves that exact rendered text once, on the bound message; and the durable store consumes the approval. Typed chat text never carries authority, and a marker-bearing user line is visibly quoted before forwarding so it can never forge the adapter's decision envelope. The user's exact typed message is stored verbatim in the workflow record, adapter-stamped, bound by its own sha256, and rendered into the approved mission text, which is what makes "the Operator can never change what the human said" true. Pinned by the release narrative suite and the `telegram_operator` suites.

## What a Mission Authorization is

**[IMPLEMENTED / PROVEN]** In v0.7.0 a Mission Authorization is a closed-schema document that binds the destination and its boundaries only: objective, constraints, rules, desired outcome, acceptance, unresolved questions, execution scope, control identity and policy digest, canonical target, issue or PR, approved baseline, bounded handoff, revision, `delivery_authority: none`, and the exact human request. Implementation-strategy keys are refused by normalized name at any nesting depth; the target Herdr Supervisor owns the engineering route. Validation is structural, and [SECURITY.md](../../SECURITY.md) states the limit of that and the protections around it.

**[PLANNED / TARGET]** The target Mission Authorization is the human approval of an exact revision of a Mission Manifest: objective, target and baseline, allowed and forbidden actions, proof required, budget, priority, worker requirements, runtime and profile, Herdr topology, stop conditions, and `delivery_authority = none`. The human sees the whole card and approves, edits the rules, or rejects. A material rule change later returns the mission to the human as NEEDS_REAUTHORIZATION. See [Missions and Lifecycle](Missions-and-Lifecycle.md).

## The separate authorization chain

**[IMPLEMENTED / PROVEN]** Mission authorization does not authorize a commit. A commit does not authorize a push. A push does not authorize a merge. A merge does not authorize a release. A release does not authorize deployment. In v0.7.0 the machine path holds no delivery authority anywhere: remote missions carry `delivery_authority = none`, the Runtime is structurally incapable of delivery (no subprocess outside the pinned read-only git transport seam, no delivery verb in the package, enforced by literal scan and subprocess confinement), and commit, push, and release tag are three separate local human gates with their own one-shot approvals. The commands and bindings are on the [human Git gates](../reference/human-git-gates.md) page.

**[PLANNED / TARGET]** Consequential authorization targets exact, one-shot, action-bound, revision-bound, mission-bound, human-authorized, expiry-bound, replay-resistant receipts, for each of prepare commit (read-only), approve commit, approve push or open PR, approve merge, approve tag, approve release, and approve deploy. No delivery action inherits authority from the Mission Authorization or from another delivery action. Every attempt writes a durable receipt (`prepared`, `authorized`, `executing`, `succeeded`, `failed`, `ambiguous`) and reconciles uncertain external effects before another attempt is allowed. Exact Telegram-native delivery approval is a Phase-I requirement in the [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md), not implemented behavior.

## What a transport, a model, and a UI cannot do

**[IMPLEMENTED / PROVEN]** for the v0.7.0 chain; the same rule extends unchanged to every target surface.

- A transport can carry authority but cannot mint it. Telegram carries a one-shot button approval bound to the exact rendered mission; it cannot commit, push, open a PR, tag, release, or deploy, and its decision envelope says so.
- A model can request authority but cannot grant it. The Operator proposes a plan or a Mission Authorization; the Runtime mints the one-shot capabilities, never Codex; the Broker validates and consumes them.
- A UI can display authority but cannot create it. `/status` renders durable state; it changes nothing. The target visual world reads canonical state and never approves, completes, or delivers.
- Credential availability does not grant mission permission. The trusted Mac holds Git and Codex credentials; a mission uses them only inside its authorized scope, and the target Worker model makes that explicit per credential class.

This list also covers Grok Bot, Pi, GPT, Claude, Grok models, DBOS, Skill Packs, Domain Profiles, the Ops Steward, the Reconciler, BrowserCapability, Herdr, worker machines, the desktop admin, and the visual world. None of them may create missing authority.

## Credential availability is not permission

**[PLANNED / TARGET]** A worker declares credential classes it holds. A mission declares the credential classes it needs. The Scheduler matches them, and the authorization binds the match. A worker having a credential available does not mean every mission may use it, and a capability being installed does not mean a mission may invoke it. Priority changes how closely a mission is watched; it never changes authority, credential scope, mission scope, Git gates, deployment gates, or allowed side effects. See [Capabilities and Workers](Capabilities-and-Workers.md).

## Trust boundaries today

**[IMPLEMENTED / PROVEN]** The v0.7.0 boundaries, relocated from the README. They describe the released local-mission path and the remote-target path that runs on top of it.

### Phone and Telegram

The phone is a remote human interface. It may submit intent, receive plans, approve or reject bounded plans, query mission status with `/status` (each answer explicitly requested and queued behind active Gateway work, never streamed), receive restart and recovery notices about work it sent that was interrupted, and receive verification results. It must not receive arbitrary shell access, invoke Herdr directly, construct Herdr missions itself, bypass Operator reasoning, silently broaden permissions, expose Mac credentials or repository secrets, or authorize commits, pushes, PRs, tags, releases, deployments, or merges.

### MacBook

The MacBook is the trusted execution node. It owns the repositories, Git credentials, Codex credentials and session state, the Codex Gateway, the Herdr runtime and agents, local test environments, repository-scoped `.herd` state, and the commit and push authorization gates. All engineering execution stays local to this node unless a future architecture explicitly changes that boundary; the target Worker model does, by making the Mac Worker 1 rather than the only host.

### Codex Gateway

The Gateway is a transport-neutral front door. It accepts human intent, validates the target repository, starts or resumes Operator sessions, returns structured results, preserves source and request identity, and fails closed on malformed output. It does not import Herdr, call `HerdrControlPlane`, invoke `herdctl`, prompt Herdr agents, create Herdr missions, dispatch engineering work, grant approvals, commit, push, merge, or release. The isolation is enforced by the static suite. Detail is on the [Codex Gateway](../reference/codex-gateway.md) page.

### Operator

The Operator owns the human-to-engineering boundary: it understands intent, inspects the target repository, gathers context, resolves genuine ambiguity, proposes a bounded plan, receives human approval, creates the Herdr handoff, dispatches and monitors Herdr, handles routine recovery inside the approved scope, independently inspects the result, creates bounded follow-up work when necessary, prepares verified work for delivery, and requests protected human authorization. It does not become the Executor. Codex is the current implementation of that role; the role is not a model brand.

### Herdr

Herdr owns engineering. The Supervisor determines the engineering route and owns decomposition, role assignment, execution planning, sequencing, strategy, and the validation workflow. The Lead owns acceptance. The Executor implements. The Reviewer adversarially validates and can reject until the work satisfies the mission. Herdr does not hold delivery authority and does not govern the mission. See [Herdr](Herdr.md).

### Human

The human is the ultimate delivery authority. Normal engineering execution requires one bounded-plan approval. Delivery is separately protected: the human explicitly authorizes commit, push, pull-request publication where required, tag, release, and any destructive or materially expanded authority.

## Ambiguous external effects

**[IMPLEMENTED / PROVEN]** If a non-idempotent external effect may already have happened, the system does not blindly repeat it. In v0.7.0: an ambiguous placeholder creation fails closed before dispatch; dispatched-but-unconfirmed Gateway work is reported AMBIGUOUS after a crash and never replayed; an ambiguous edit outcome on the bound result message is retried only as an idempotent edit of that same message; and a placeholder-bound workflow can never fall back to a second delivery call. "Exactly once" means never twice and never silently dropped, not that the result always eventually arrives; the terminal states are disclosed in `/status`, and recovering one is a human step. The exact contract is in the [README](../../README.md#remote-target-repository-routing-di-remote-2-v070).

**[PLANNED / TARGET]** The same rule applies to browser form submissions, API mutations, Git pushes, release creation, deployment triggers, and every other non-idempotent action. When certainty is lost the mission enters AMBIGUOUS, the deterministic Reconciler checks reality, and only after reconciliation can the system decide whether a retry is safe. See [Observation and Recovery](Observation-and-Recovery.md) and, for browser writes, [Capabilities and Workers](Capabilities-and-Workers.md).
