# Capabilities and Workers

Back to [Home](Home.md). Labels are defined on the Home page.

Everything on this page is design unless a paragraph says otherwise. The v0.7.0 tree has one worker, the trusted Mac, and its capabilities are implicit in what is installed there.

## Capabilities are brokered, not granted

**[PLANNED / TARGET]** A Capability is a specific thing a mission is allowed to do: inspect a repository, edit an isolated worktree, run tests, benchmark, drive a browser, call an external API, dispatch Herdr. Every capability is brokered by Dodging Infinity against the mission's authorization at the moment of use. A capability being installed on a worker does not mean a mission may invoke it. An Action Risk Envelope classifies each proposed external action by confidence, blast radius, reversibility, external effect, credential scope, ambiguity risk, and data sensitivity; the classification can raise required review, supervision cadence, rollback and reconciliation requirements, model choice, and human escalation. It cannot create missing authority.

**[IMPLEMENTED / PROVEN]** The ancestor in the tree is the v0.7.0 Target Broker: privileged, fixed-action, nine fixed lifecycle actions, each performed only against a Runtime-minted one-shot capability bound to exactly one `(workflow_id, action, revision)` tuple. Sensitive values are resolved from the protected workflow record and never supplied by the caller. Capabilities are minted by the Runtime, never by Codex. The contract is stated in the [README](../../README.md#current-architecture-on-main-remote-target-routing-di-remote-2-v070) and pinned against the code by the release narrative suite.

## Workers are capability-aware hosts

**[PLANNED / TARGET]** A Worker is a trusted machine or environment that can perform bounded mission work. It declares:

```yaml
worker_id: W-001
health: READY
os: macos
arch: arm64
capabilities:
  repos: [customer-platform]
  browser: true
  ios_simulator: true
  gpu: false
  vpn: [corp-a]
credential_classes: [github-dev, staging]
capacity:
  herdr_pods: 3
  browser_sessions: 2
leases: [M-1842]
```

The Scheduler matches a mission's worker requirements against declared capabilities and capacity, and the lease binds the match for the mission's lifetime. Readiness (READY, DEGRADED, BLOCKED, STALE) is checked before consequential dispatch, and a required dependency known to be unavailable fails the mission before it starts rather than partway through.

## The trusted Mac is Worker 1

**[IMPLEMENTED / PROVEN]** Today every component from the adapter onward runs on the trusted Mac. It owns the repositories, Git and Codex credentials, the Gateway, the Herdr runtime and agents, local test environments, `.herd` state, and the commit and push gates. It must stay powered, connected, authenticated, and able to reach the configured repository; the [runtime and host](../reference/runtime-and-host.md) page lists what it must do and what survives a restart.

**[PLANNED / TARGET]** The Mac is Worker 1, not a permanent singleton. Later workers may be a Linux GPU host, a VPN-connected host, a browser-only host, or an iOS simulator host. Nothing about the authority model changes when a second worker appears: a worker having a credential available does not mean every mission may use it, and the Mac's current possession of every credential is a fact about Worker 1, not a rule of the fabric.

## BrowserCapability: reads, writes, and human handoff

**[PLANNED / TARGET]** BrowserCapability puts a persistent browser behind Dodging Infinity's authority and splits its operations into three classes:

```text
Operator
   |
DI BrowserCapability
   +-- READ:   snapshot, text, screenshot, console, network
   +-- WRITE:  navigate, click, fill, upload, submit
   +-- HUMAN HANDOFF:  MFA, CAPTCHA, manual takeover
           |
     Playwright / Chromium
```

Reads produce evidence and carry no external effect. Writes are external effects and are authorized, recorded, and reconciled as such. Human handoff covers MFA, CAPTCHA, and takeover, and never tries to automate past them. Stale element references fail rather than guess. Screenshots, snapshots, console health, and network traces are first-class evidence in the UI proof contract on [Evidence and Verification](Evidence-and-Verification.md). Playwright is the planned dependency behind it.

**[DESIGN REFERENCE]** pstack is the source of the useful persistent-browser, element-reference, QA, and skill-pack ideas this design borrows. Dodging Infinity does not inherit pstack's authority model and does not depend on it.

## Ambiguity in browser writes

**[PLANNED / TARGET]** Browser writes follow the same ambiguity model as every other non-idempotent external effect. For each write, the capability records the mission, target, action, pre-state, risk classification, execution receipt, post-state, and reconciliation result. If the effect is uncertain (the process crashed after submit, the network dropped mid-request, the page shows a duplicate mutation), the mission blocks further submit attempts, enters AMBIGUOUS, and the Reconciler establishes reality before any retry is considered. A browser QA mission that finds a duplicate mutation in a network trace does not press the button again to see what happens. See [Authority and Safety](Authority-and-Safety.md).

## The Worker Registry

**[PLANNED / TARGET]** The Worker Registry is the durable list of workers, their declared capabilities and credential classes, their capacity, their leases, and their health. It is written by workers registering and by the Reconciler observing them, and read by the Scheduler. It is a Phase 7 deliverable on the [Roadmap](Roadmap.md), after the chaos tests that prove one worker can survive injected failure. Until then Worker 1 is implicit and the registry does not exist. See [Architecture](Architecture.md) for where it sits in the stack.
