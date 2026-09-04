<p align="center">
  <img src="assets/brand/banner.svg" alt="Dodging Infinity" width="100%">
</p>

# Dodging Infinity

**AI orchestration and mission control for agents.**

Dodging Infinity is built for work that is too large, too long-running, or too important to live inside one chat session.

```text
o──o──o
      \
       o──[ DI ]──o
```

---

# 1. What is Dodging Infinity?

Dodging Infinity is an orchestration layer for AI agents doing real work.

The unit of work is a **Mission**.

A Mission keeps the objective, scope, authorization, state, evidence, artifacts, and delivery decisions together while different models, agents, tools, and machines do their part.

That starts to matter once the job gets bigger than:

```text
"Write this function."
```

A real job might need research first. It might need engineering after that. It might involve a browser, a PDF, a screenshot, a simulator, several models, a second machine, a restart, a human approval, and finally a pull request.

Dodging Infinity keeps that work together instead of treating each chat or agent session as a separate universe.

The basic rule is:

> **Bots converse and collaborate. Dodging Infinity governs. Capabilities do bounded work. Workers execute. Humans authorize.**

The Mission stays the same even when the tools underneath it change.

```text
      01001        10110
          \        /
           \      /
            [ DI ]
           /      \
          /        \
      10110        01001
```

---

# 2. How does it work?

At a high level:

```text
                                  HUMAN
                                    │
                    text / voice / image / file
                    requests / approvals / status
                                    │
                                    ▼
              ┌──────────────────────────────────────┐
              │             INTERACTION              │
              │                                      │
              │   Coordinator Grok Bot               │
              │   Telegram                           │
              │   CLI                                │
              │   Specialist bots                    │
              └──────────────────┬───────────────────┘
                                 │
                                 ▼
      ┌─────────────────────────────────────────────────────────┐
      │                   DODGING INFINITY                      │
      │                                                         │
      │   Missions                Authorization                  │
      │   Routing                 Evidence                       │
      │   State                   Artifacts                      │
      │   Observation             Verification                   │
      │   Reconciliation          Delivery gates                 │
      └───────────────────────────┬─────────────────────────────┘
                                  │
                                  ▼
              ┌──────────────────────────────────────┐
              │       OPERATOR + MODEL ROUTING       │
              │                                      │
              │   Pi                                 │
              │   Codex                              │
              │   GPT / Claude / Grok / Muse         │
              │   Local / future models              │
              └──────────────────┬───────────────────┘
                                 │
                                 ▼
      ┌─────────────────────────────────────────────────────────┐
      │                  BOUNDED CAPABILITIES                   │
      │                                                         │
      │   Engineering ───────────────────────► Herdr            │
      │   Research                                              │
      │   Browser                                               │
      │   Document / Ops                                        │
      │   Media / Multimodal                                    │
      │   Publishing / Messaging                                │
      └───────────────────────────┬─────────────────────────────┘
                                  │
                                  ▼
              ┌──────────────────────────────────────┐
              │               WORKERS                │
              │                                      │
              │   Trusted Mac                        │
              │   GPU / simulator host               │
              │   Browser / SaaS worker              │
              │   Other hosts                        │
              └──────────────────┬───────────────────┘
                                 │
                                 ▼
                  GitHub / Web / SaaS / APIs
                   Simulators / GPUs / Devices
```

Dodging Infinity sits in the middle because that is where the Mission lives.

The interface can change. The model can change. The work can move between capabilities or machines. None of that should require starting the Mission over from scratch.

## Interaction

The interaction layer is how you talk to the system.

That can be as simple as sending a request from your phone:

```text
You:
"The export path is timing out.
Figure out why, fix it, and prove the fix.
Do not commit anything."
```

Telegram can carry that request into Dodging Infinity today. The larger interaction layer is built around a Coordinator Grok Bot that can sit across many Missions and work with specialist bots.

The conversation can get more interesting than one request at a time:

```text
You:
"Research Bot, did anything from the DBOS comparison
get handed to engineering?"

Research Bot:
"Yes. The durability findings are attached to Mission #202.
Engineering is using them in the worker design review."
```

Or:

```text
You:
"What needs me?"

Grok Bot:
"Mission #144 is waiting on content approval.
Mission #145 needs credentials.
Engineering and research are still running."
```

The point is not to make the bots sound clever. The point is that they can all reference the same Missions, artifacts, evidence, and status instead of making up their own version of what is happening.

```text
       .----.           .----.
      | •  • |  ─────  | •  • |
      |  --  |   ref   |  --  |
       '----'           '----'
         BOT              BOT
```

## Operator and model routing

The Operator handles reasoning for a Mission step.

Dodging Infinity does not need every job to run through the same model. A research step may want one provider. A code change may want another. A small classification job may be cheaper somewhere else. A privacy-sensitive task may eventually stay local.

```text
                         Mission step
                              │
                              ▼
                       OperatorSession
                              │
                              ▼
                        Model routing
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
            Pi              Codex          Other adapter
             │                │                │
        GPT / Claude      GPT / Claude      local model
        Grok / Muse                         future model
```

Codex is the current reference Operator path in the repo.

`OperatorSession` is the seam that keeps the Mission logic separate from the provider doing the reasoning. Codex sits behind an adapter instead of being spread throughout the control plane.

Pi fits at the same boundary. The integration is designed around an adapter/RPC path so Pi can provide its model and tool runtime without becoming responsible for Mission identity, authority, evidence, or delivery.

That lets the model runtime change without changing the Mission format.

## Capabilities

Capabilities are the kinds of work Dodging Infinity can hand out.

Engineering is one capability. Research is another. Browser work, document work, media, publishing, and operations can follow the same pattern.

### Engineering through Herdr

Engineering routes through Herdr.

```text
                   Engineering Mission
                           │
                           ▼
                         Herdr
                           │
                           ▼
                      Supervisor
                           │
                           ▼
                         Lead
                           │
                           ▼
                       Executor
                           │
                    ┌──────┴──────┐
                    ▼             ▼
                  Tests        Reviewer
                    │             │
                    └──────┬──────┘
                           ▼
                        Evidence
                           │
                           ▼
                  Dodging Infinity
```

Herdr was adapted around a bounded engineering handoff.

The Supervisor receives the objective, repository context, constraints, rules, desired outcome, and unresolved questions. From there, Herdr owns the engineering route.

The roles are deliberately separate:

- **Supervisor** owns engineering direction and decomposition.
- **Lead** coordinates the work and decides when the engineering task is ready to close.
- **Executor** implements and tests.
- **Reviewer** checks the result independently and can reject it.

The Reviewer is read-only. It does not grade its own work because it did not write the work.

Each Herdr instance is scoped to a repository and top-level engineering task. The result comes back with review and verification evidence for Dodging Infinity to evaluate as part of the larger Mission.

### Example: engineering

```text
You:
"Fix Mitiq issue #2802.
Do not ship it."
          │
          ▼
Dodging Infinity creates the Mission
          │
          ├─ target repository
          ├─ objective
          ├─ constraints
          ├─ proof requirements
          └─ no delivery authority
          │
          ▼
You approve
          │
          ▼
Engineering Capability
          │
          ▼
Herdr
          │
Supervisor → Lead → Executor ↔ Reviewer
          │
          ▼
tests + evidence
          │
          ▼
Dodging Infinity verifies
          │
          ▼
result comes back to you
```

If the work is good, you decide what happens next.

Fixing the issue did not automatically authorize a commit, push, merge, release, or deployment.

### Example: research

```text
You:
"Compare DBOS, Temporal, and Postgres
for durable execution."
          │
          ▼
Research Mission
          │
      ┌───┴───┐
      ▼       ▼
  Research   Browser
      │       │
      └───┬───┘
          ▼
       Evidence
          │
          ▼
  Research artifact
          │
     ┌────┴──────────────┐
     ▼                   ▼
    You           Engineering Mission
```

A follow-up can be simple:

```text
"Ask Engineering Bot whether the DBOS research
changes how we should build the worker layer."
```

The research artifact can move directly into that Mission instead of being flattened into a pasted summary.

### Example: multimodal

```text
   screenshot
       +
 screen recording
       +
   voice note
       +
    log file
       │
       ▼
 Mission intake
       │
  ┌────┼──────────────┐
  ▼    ▼              ▼
file  transcription  visual analysis
  │    │              │
  └────┴──────┬───────┘
              ▼
         Mission context
              │
              ▼
      appropriate capability
```

If the problem is visual, show it. If the evidence is a PDF, image, recording, log, source archive, or CSV, attach it.

The original material stays with the Mission.

## Workers

Workers are the machines or environments that run capabilities.

A trusted Mac can handle the normal local workflow. A simulation Mission may need a GPU host. Browser-heavy work may run somewhere else.

```text
Mission:
"Run the quantum simulation."

Needs:
- repository access
- simulator
- GPU

Worker A              Worker B
Mac                   GPU host
repo = yes            repo = yes
GPU  = no             GPU  = yes
                      simulator = yes

                         │
                         ▼
                     Worker B
```

Worker selection answers where the work can run. The Mission already defines what the work is allowed to do.

## How the pieces fit

**Herdr** runs the engineering organization: Supervisor, Lead, Executor, Reviewer. Dodging Infinity gives it a bounded engineering handoff and consumes the evidence that comes back.

**Pi** fits behind the Operator boundary as a provider-neutral runtime. Dodging Infinity keeps the Mission model; Pi focuses on reasoning and tools.

**Codex** is the current reference Operator path. It sits behind the Codex Gateway and `OperatorSession` instead of owning orchestration directly.

**Claude** is used heavily inside the engineering stack and can also be used as a reasoning provider.

**Grok / Grok Bot** have two different jobs. Grok is a model option. Grok Bot is the conversation layer across Missions and specialist bots.

**Telegram** is the current phone interface for remote Mission requests, approval, status, and verified results.

**GitHub** is a common source and delivery target for engineering Missions. Delivery actions stay behind separate human gates.

**DBOS, browsers, GPU hosts, SaaS APIs, and future workers** can be added underneath the same Mission and capability boundaries as the system expands.

```text
      o────o────o────o
           \        /
            o──────o
               │
            .------.
           |  •  •  |
           |   __   |
            '------'
```

---

# 3. System Requirements

Dodging Infinity runs locally. The full remote workflow is built around a machine that stays available while Missions are running.

## Computer requirements

| Component | Recommendation |
|---|---|
| **OS** | macOS for the full remote workflow; Linux is covered for development and CI |
| **CPU** | Apple Silicon or a modern multi-core system |
| **Memory** | 16 GB is a reasonable starting point |
| **Heavier use** | 24–32 GB gives multiple agent processes more room |
| **Storage** | SSD recommended; Missions may create isolated repository workspaces |
| **Network** | Stable internet access for GitHub and model providers |
| **Always-on use** | A dedicated Mac or Mac mini works well for unattended Missions |

These are practical recommendations, not hard hardware limits.

## Software requirements

| Software | What Dodging Infinity uses it for | Needed when |
|---|---|---|
| [Python 3.9+](https://www.python.org/downloads/) | Dodging Infinity runtime and CLI | Core |
| [Git](https://git-scm.com/downloads) | Repository state and delivery | Core |
| [Herdr](https://github.com/herdrdev/herdr) | Engineering capability | Engineering Missions |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) | Herdr roles and Claude-backed presets | Depends on preset |
| [Codex CLI](https://github.com/openai/codex) | Current reference Operator and Codex-backed roles | Current Operator / `max-quality` |
| [Pi](https://github.com/earendil-works/pi) | Provider-neutral Operator runtime path | Pi integration |
| Grok / xAI access | Grok model routing and Grok Bot interaction layer | Grok workflows |
| Telegram bot | Remote phone interface | Remote Mission control |
| GitHub credentials | Repository access and delivery targets | GitHub Missions |
| Tailscale / SSH | Break-glass remote access to a trusted worker | Optional |

Dodging Infinity itself uses the Python standard library; there is no separate `pip install` step for the project.

## Install Dodging Infinity

```bash
git clone https://github.com/TheMickeyDodger/dodging-infinity.git
cd dodging-infinity
bash scripts/install.sh
```

The installer adds:

```text
herdctl
codexgw
tgop
dirun
```

to:

```text
~/.local/bin
```

If needed:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Install the local Git safety guard:

```bash
herdctl safety-install
```

## Set up a repository

```bash
cd /path/to/your/repository

herdctl init \
  --alias my-repo \
  --preset max-quality \
  --test-command 'python3 -m pytest'
```

Check the machine and repository:

```bash
herdctl doctor --repo my-repo
herdctl health --repo my-repo
```

Start Herdr:

```bash
herdctl bootstrap --repo my-repo
```

Start a Mission:

```bash
herdctl task \
  'Find the failing test, explain what is wrong, fix it, and verify the result. Do not commit.' \
  --repo my-repo
```

Watch it:

```bash
herdctl status --repo my-repo
```

or:

```bash
herdctl observe --repo my-repo --json
```

## Remote setup

Create:

```text
~/Library/Application Support/DodgingInfinity/telegram/config.json
```

```json
{
  "bot_token": "YOUR_BOT_TOKEN",
  "allowed_user_ids": [123456789],
  "repository": "/path/to/your/repository"
}
```

Run the Telegram adapter and Mission runtime:

```bash
tgop run
dirun run
```

Or install them as macOS background services:

```bash
tgop install-agent
scripts/dirun-agent.sh install
```

Then from Telegram:

```text
/mission <intent>
/status
/help
```

For the full operating surface, see [Operational Reference](docs/operations.md).

---

# 4. Closing note

I built Dodging Infinity because I wanted to hand AI a real problem, walk away, and come back to something I could inspect without treating a chat transcript as the source of truth.

A Mission might start from Telegram, move through an Operator, hand engineering to Herdr, use several models, survive a restart, pick up evidence from another Mission, run on a different worker, and eventually come back ready for a delivery decision.

It should still be the same Mission when it gets there.

That is what this project is trying to make normal.

```text
                  .        .        .
             .       0 1 0 1 0       .
          .      1 0         0 1       .
        .      0       .---.      0       .
       .      1       | • • |      1       .
      .      0        |  ^  |       0       .
       .      1        '---'       1       .
        .      0         |        0       .
          .      1 0     |    0 1       .
             .       0 1 | 1 0       .
                  .      |      .
                         |
                     .---+---.
                    /    |    \
                  01     |     10
                 /       |       \
               10        |        01
                         / \
                        /   \
                      01     10

                 o────o────o
                      \
                       o────o

                DODGING INFINITY
```

[Architecture](docs/architecture.md) · [Current vs End State](docs/architecture.md#16-current-implementation-notes) · [Roadmap](docs/roadmap.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

## License

Apache 2.0. See [LICENSE](LICENSE).
