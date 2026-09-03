# Examples

Back to [Home](Home.md). Labels are defined on the Home page.

**These are end-state scenarios. Nothing in them is implemented today.** Every scenario on this page is PLANNED / TARGET. They describe what the finished fabric should make normal, drawn from the end-state design, so a reader can judge the target by concrete cases rather than by a component list. Where a scenario touches something v0.7.0 already proves, the sentence says so and links the evidence; everything else is design.

## How to read these

Each scenario runs through the same three loops: mission authorization, then execution and verification with the result returning through canonical state, then constant observation without interruption. The mechanics behind them are on [Missions and Lifecycle](Missions-and-Lifecycle.md), [Capabilities and Workers](Capabilities-and-Workers.md), and [Authority and Safety](Authority-and-Safety.md). Names, numbers, and timings in the scenarios are illustrative.

What v0.7.0 does prove, for contrast: one exact remote engineering mission from a Telegram request, through a one-shot Mission Authorization, an isolated target, an unattended Herdr, and evidence-gated verification, to an exactly-once Telegram result, with delivery locked behind local human gates. It proves that once, hermetically, and the one historical live run of it reached target COMPLETE with a Reviewer APPROVE and then correctly terminated BLOCKED. See [Current vs End State](Current-vs-End-State.md).

## Remote engineering incident

**[PLANNED / TARGET]**

You are on the way to an airport. A large customer reports that CSV exports above roughly fifty thousand rows time out, and their executive demo is at three. Your trusted Mac is at home. You have a phone.

1. You tell the Mission Coordinator: "Acme's large exports are timing out. Figure out why, fix it if we safely can, prove the fix, and get a release ready before the demo. Do not deploy anything without me."
2. The fabric drafts mission `M-1842`: type engineering, priority P1, target `customer-platform` at the exact current revision. No engineering has started.
3. It proposes the rules. Allowed: inspect, isolated edits, tests, benchmark, browser QA, a Herdr Pod. Locked: commit, push, merge, release, deploy, credentials. It lists the proof required: root cause, regression test, focused tests, benchmark, browser user path, Reviewer approval, authoritative verification.
4. Your phone shows the Mission Authorization card. You approve. Only now is execution authorized. (v0.7.0 proves this step for one remote engineering mission over Telegram.)
5. The Operator, on the runtime and provider the profile selected, traces the export path and finds that the service buffers the entire CSV before sending a byte, pushing large requests past the gateway timeout.
6. The Operator requests a bounded Herdr Pod. Round one is rejected because the implementation leaks a resource on early disconnect. Round two fixes it. The Reviewer approves. (v0.7.0 proves unattended Herdr bootstrap, independent Reviewer decisions, and the fact that an APPROVE is necessary and not sufficient.)
7. BrowserCapability runs a large export through the actual UI and records baseline versus candidate: a gateway timeout at seventy-four seconds against first bytes in under two seconds and completion in eleven.
8. Your flight boards. The mission keeps running.
9. The Operator process disappears. The Reconciler detects the missing process and restores the bounded session from durable state. No human intervention.
10. Later you ask "what is happening with the export fix?" The Observation Service answers from canonical state. It does not interrupt Herdr.
11. Reviewer approval, tests, benchmark, browser QA, and authoritative verification all become evidence. The mission becomes VERIFIED.
12. The Release experience shows: root cause found, Reviewer APPROVE, browser QA pass, authoritative suite pass, no delivery authority used, with Inspect Evidence and Prepare Commit as the next exact actions.
13. You inspect the prepared commit and approve exactly it. Push is still locked.
14. You separately authorize the push or PR. CI runs.
15. Merge approval is separate. Deployment approval is separate. Post-deploy monitoring proves health.

Outcome: root cause, reviewed code, regression tests, performance evidence, browser evidence, CI evidence, exact approval receipts, deployment evidence, recovery history, and cost history, without opening a terminal.

## Research mission

**[PLANNED / TARGET]**

You are at a conference. A competitor launches a new product tier and you want a real answer before an afternoon strategy meeting. You tell the Research experience: "Figure out exactly what changed in their product and pricing, how customers are reacting, what it means for us, and give me a decision memo by two. No code work."

1. The fabric creates `M-1901`: type research, priority P1, engineering false.
2. The authorization card grants web research, browser observation, public screenshots, and Markdown and PDF artifacts. It forbids code changes, Herdr engineering, Git delivery, and any authenticated customer or admin action. You approve.
3. The Operator runs the research Skill Pack: source discovery, credibility checks, pricing comparison, customer-reaction synthesis, strategic analysis, adversarial review.
4. BrowserCapability collects direct evidence in read mode: pricing pages, feature pages, launch materials, help-center changes, public reactions.
5. You ask "what have we learned so far?" The Observation Service reports sources evaluated, primary sources, reaction sources, the pricing matrix complete, synthesis running, the artifact in draft, no blockers. The research Operator keeps working.
6. Adversarial review catches that one claimed feature is announced, not generally available. The memo is corrected.
7. Before the meeting the Research experience returns `M-1901 VERIFIED` with a two-page decision memo, a pricing comparison, a source appendix, screenshots, and response options. You open the PDF on your phone.

This scenario has no engineering mission in it. It is what makes the fabric a general mission fabric rather than a coding system.

## Operations transformation analysis

**[PLANNED / TARGET]**

You are evaluating a regional logistics company whose operations team spends hours moving information between email, spreadsheets, a transportation-management system, and accounting. You want to know what can realistically be automated. From your desk: "Map how dispatch exceptions are handled today, identify where humans are acting as middleware, estimate the automation opportunity, and design a ninety-day pilot."

1. The fabric creates `M-2020`: type automation assessment, priority P2.
2. Allowed: research, inspect supplied documents, analyze process data, create process maps, produce an ROI model, create a pilot architecture. Forbidden: connect to production systems, send customer communications, change operational records, purchase software. You approve.
3. The Skill Pack reconstructs the current state: an inbound exception email, a dispatcher who reads it, copies an id into a spreadsheet, checks the TMS, messages a driver, updates the spreadsheet, and a finance reconciliation later.
4. Evidence accumulates: supplied SOPs, screenshots, interview notes, spreadsheet examples, timings, exception volumes.
5. The analysis identifies the dispatcher as translating events between systems that already contain the data.
6. You leave the office and continue from your phone: "what is the highest-confidence automation opportunity?" Observation returns the current canonical synthesis without interrupting the analysis.
7. The final artifact contains the current-state and target-state processes, integration boundaries, human approval points, an exception policy, the ninety-day pilot, expected time savings, risk assumptions, and a measurement plan.
8. If the pilot becomes an implementation and the same pattern appears elsewhere, the Ops Steward can propose the repeated work as a reusable Skill Pack or a deterministic capability, through the governed path.

Outcome: an implementation-ready operations transformation package, not a brainstorming transcript.

## Browser QA

**[PLANNED / TARGET]**

An engineering mission has built a new subscription upgrade flow. Unit and integration tests are green and the code looks right. You want to know whether the actual user path is safe. You tell the Browser QA experience: "Validate the upgrade flow end to end before I merge it. Do not make any production purchase."

1. The authorization grants staging login, navigation, form interaction, screenshots, console and network observation, and the test payment path. It forbids a production purchase, credential changes, and production mutations. You approve.
2. BrowserCapability starts a persistent browser against the authorized staging environment.
3. The mission signs in, opens billing, selects the upgrade, verifies the displayed price, submits a test purchase, verifies the redirect, checks account state, captures screenshots, inspects the console, inspects network calls.
4. It finds what the tests missed: the UI shows the correct plan, and the network trace shows a duplicate mutation request after a retryable client error. Under the wrong backend behavior that is a double external effect.
5. The capability records the uncertain effect as an external-effect risk. The mission blocks further submit attempts until state is reconciled. It does not press the button again to see.
6. The QA mission proposes an engineering child mission. You receive a new Mission Authorization for that scope and approve it. The child does not inherit the QA mission's authority.
7. Herdr changes the retry logic and adds a regression test. The Reviewer verifies.
8. QA reruns the proof path. The trace now shows exactly one mutation.
9. The Release experience returns: upgrade flow VERIFIED, duplicate request fixed, browser proof pass, Reviewer APPROVE, CI pass, Prepare Commit.

Outcome: a real user-path failure that code-only tests did not prove, fixed safely, with the exact delivery decision back in your hands.

## Overnight multi-mission

**[PLANNED / TARGET]**

You go to sleep with three missions running: `M-2201` an engineering feature, `M-2202` a research report, `M-2203` a dependency security audit. Nightly maintenance is scheduled.

1. The Scheduler keeps the missions isolated: separate identity, context, budget, authority, evidence, and workspace or worker leases.
2. Bounded maintenance missions inspect flaky tests, dependency alerts, CI runtime, documentation drift, and stale flags. They cannot merge or deploy.
3. `M-2201` loses an Executor process. The Reconciler detects it. Only `M-2201` enters recovery; the other two continue.
4. The network drops briefly and GitHub checks become unavailable. Durable state is preserved and the dependency is marked degraded. Nothing pretends checks passed.
5. The network returns. The Reconciler refreshes GitHub state. Missions continue.
6. `M-2203` hits a credential problem and becomes BLOCKED, credential renewal required. The Attention Router queues a human item. It does not try to broaden credentials itself.
7. The Ops Steward notices the same flaky-test signature across four recent missions and prepares a learning proposal: a deterministic quarantine and reproduction rule plus regression coverage for the underlying race. No trusted policy changes automatically.
8. At seven the Mission Coordinator sends the overnight summary: `M-2202` completed; `M-2201` running, recovered after the Executor failure, Reviewer round three; `M-2203` blocked on credential renewal; maintenance found two dependency findings, one flaky-test pattern, one documentation-drift patch prepared; zero unauthorized effects, zero cross-mission contamination, one automatic bounded recovery, a compute cost; waiting on you, one credential action and one prepared maintenance PR.
9. You open the visual world. The desktop reconstructs the same state from the registry and the event stream: one active engineering Pod, one completed research mission, one blocked security mission.

Outcome: the fabric operated overnight as a durable organization, not as one fragile chat session.

## What the five scenarios are for

Together they describe five product truths the target must satisfy: remote approval, Herdr execution, recovery, proof, and exact mobile delivery; non-engineering missions that return reviewed artifacts; complex business analysis with implementation-ready output; real user-path evidence with ambiguous-effect handling; and multi-mission isolation with deterministic recovery, scheduled work, organizational learning, and morning attention summaries. The phases that build toward each are on the [Roadmap](Roadmap.md). None of them is implemented today.
