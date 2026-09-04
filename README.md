<p align="center">
  <img src="assets/brand/banner.svg" alt="Dodging Infinity — AI orchestration and mission control for agents" width="100%">
</p>

# Dodging Infinity

[![CI](https://github.com/TheMickeyDodger/dodging-infinity/actions/workflows/ci.yml/badge.svg)](https://github.com/TheMickeyDodger/dodging-infinity/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheMickeyDodger/dodging-infinity)](https://github.com/TheMickeyDodger/dodging-infinity/releases/latest)

**An AI orchestration system and mission control for agents.**

Dodging Infinity turns an objective written in ordinary language into a **Mission**: a bounded, explicitly authorized unit of work with a fixed scope, a required standard of proof, and a list of things it may not do. Models, bots and agent frameworks then work inside that boundary. The Mission itself — its identity, state, evidence and authority — belongs to Dodging Infinity, not to whichever tool happens to be doing the work.

That separation is the point. Bots converse and collaborate, Dodging Infinity governs, capabilities do bounded work, workers execute, humans authorize. Capability is never authority: permission to solve a problem is not permission to ship the result.

---

## What it does today

Released behavior as of v0.7.0. The current-vs-target status and supporting evidence for these capabilities are tracked in [Current vs End State](docs/wiki/Current-vs-End-State.md).

- **Turns a request into a bounded Mission you explicitly approve.** A natural-language message becomes a Mission bound to the exact plan text you were shown, and nothing starts until you approve that plan. The approval covers the mission itself and never any later delivery action; typed text carries no authority.
- **Isolates the work to the intended target.** Each remote mission runs in its own managed workspace, checked against the intended repository and the approved starting point before any work begins.
- **Runs engineering through Herdr.** Supervisor, Lead, Executor and Reviewer are bootstrapped inside that target. The Reviewer is independent and read-only, and can reject work and force another round.
- **Requires evidence, not an agent's word.** A mission is verified only when the required evidence is present, checked against a fresh read of disk. Herdr reporting COMPLETE can never verify a mission on its own.
- **Fails closed rather than repeating itself.** The verified result is delivered once. If an external effect may already have happened, the mission stops and reports that rather than retrying blind.
- **Stays observable without becoming steerable.** `herdctl observe` is a strictly read-only projection and `herdctl health` a readiness probe. Neither mutates, repairs, prompts, or gates anything.
- **Keeps delivery in human hands.** Commit, branch push and release-tag push are three separate one-shot local approvals. No remote message can operate any of them.

---

## How it works

```text
You
 │   objective · approval · status question
 ▼
Interaction plane           Telegram today · Coordinator Grok Bot target
 │
 ▼
DODGING INFINITY            mission truth · authorization · routing
                            observation · evidence · verification · delivery gates
 │
 ▼
Operator / model routing    Codex today · provider-neutral by design
 │
 ▼
Bounded capabilities        engineering today · research, browser, document/ops,
 │                          media and publishing in the target architecture
 ▼
Workers                     one trusted Mac today · more hosts later
 │
 ▼
GitHub · web · SaaS · APIs · devices
```

Everything below Dodging Infinity is meant to be replaceable. Model routing picks a provider; it does not acquire the Mission. A capability does bounded work; it cannot widen its own scope. A worker's readiness describes what it is able to run, never what it is allowed to do. Swapping the bot, the provider, the engineering system, the durability substrate or the worker should never change what a Mission is.

Engineering is the most developed capability, and Herdr is how it executes:

```text
Engineering capability
        │
        ▼
      Herdr

   Supervisor
        ↓
      Lead
        ↓
   Executor ↔ Reviewer
```

Herdr performs the engineering. Dodging Infinity owns the Mission around it: the authorization Herdr runs under, the evidence its work has to produce, and the gates its result still has to pass. Herdr is not the control plane and not the product.

The full picture is in [Architecture](docs/wiki/Architecture.md); what is built versus what is designed is in [Current vs End State](docs/wiki/Current-vs-End-State.md).

---

## Today, and where it is going

**Today — the reference implementation**

| Layer | Current |
|---|---|
| Remote interaction | Telegram adapter, allowlisted private chats |
| Operator | Codex CLI, behind a gateway with no path to Herdr |
| Capability | Engineering, via Herdr |
| Worker | One trusted local Mac |
| Delivery | Local human commit / push / tag gates; remote missions carry `delivery_authority = none` |

**Direction — target architecture, not implemented**

A Coordinator Grok Bot as the universal, multimodal front door, with specialist bots collaborating over mission and artifact references rather than borrowed authority. Provider-neutral operator and model routing behind `OperatorSession`. Mission types beyond engineering — research, browser, document and ops, media, publishing — reached through a capability broker. A capability-aware worker registry spanning several machines. Durable multi-mission orchestration, reconciliation against real-world state, and exact one-shot delivery approval from a phone.

Each item on both lists carries its real status in [Current vs End State](docs/wiki/Current-vs-End-State.md), and the sequencing is in [Roadmap](docs/wiki/Roadmap.md).

---

## Requirements

**Required**

- **macOS or Linux.** There is no Windows support. Linux is covered for development and CI; the full remote workflow is macOS-first, because the background services install as per-user LaunchAgents.
- **Python 3.9 or newer.** Standard library only — there is nothing to `pip install`. CI covers macOS and Ubuntu on Python 3.9 and 3.13.
- **Git.**
- **[Herdr](https://github.com/herdrdev/herdr)**, available on `PATH` as `herdr`.
- **The agent CLI your preset uses,** installed and signed in: `claude` for the `all-claude` and `conservative` presets, `claude` and `codex` for `max-quality`. `herdctl doctor` names anything missing.
- **Read/write access** to the repositories you want missions to run in.

**Additionally required for the remote workflow (macOS)**

- **Codex CLI**, signed in — Codex is the current reference Operator implementation.
- A **Telegram bot token** and your numeric Telegram user id.
- A Mac that stays awake, online and authenticated for as long as the mission runs. There is no cloud service behind this: the adapter polls outbound, so nothing listens on an inbound port.

### Recommended system

Practical guidance, not hard or benchmarked minimums.

| Component | Recommendation |
|---|---|
| Machine | Apple Silicon Mac — where the full remote workflow is developed and run |
| Memory | 16 GB as a practical starting point |
| Heavier use | 24–32 GB when several agent processes run concurrently |
| Storage | SSD, with room for one isolated workspace per mission |
| Network | Stable broadband; model providers have to stay reachable |
| Unattended missions | A dedicated always-on Mac — a Mac mini works well |

---

## Install

```bash
git clone https://github.com/TheMickeyDodger/dodging-infinity.git
cd dodging-infinity
bash scripts/install.sh
```

That installs `herdctl`, `codexgw`, `tgop` and `dirun` into `~/.local/bin`. Put it on your `PATH` if it is not there already:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Then install the local command guard once:

```bash
herdctl safety-install
```

It configures the global Claude Code commit/push guard. The per-repository Git guards are installed by `herdctl init` and `herdctl upgrade`.

---

## Quick start

Initialize a repository you want missions to run in:

```bash
cd /path/to/your/repository

herdctl init \
  --alias my-repo \
  --preset max-quality \
  --test-command 'python3 -m pytest'
```

`herdctl presets` lists the presets. If the verification command is not known yet, initialize without it and set it later with `herdctl set-test 'YOUR_TEST_COMMAND' --repo my-repo`.

Check readiness:

```bash
herdctl doctor --repo my-repo   # tooling, agent CLIs, Git guards
herdctl health --repo my-repo   # this repository's herd
```

Start the herd, then run a first mission:

```bash
herdctl bootstrap --repo my-repo

herdctl task \
  'Find the failing test, determine the cause, fix it, and verify the result. Do not commit.' \
  --repo my-repo
```

Watch it:

```bash
herdctl status  --repo my-repo
herdctl observe --repo my-repo --json
```

The guarded workflow cannot commit without separate human approval. The full command surface is in [Herdr operations](docs/reference/herdr-operations.md).

---

## Remote control

Optional, and macOS-only today.

Telegram is the current reference transport for remote intent, plan approval, status and verified results. It is an adapter, not an execution system: it has no direct path to Herdr or `herdctl`, and the static suite enforces that isolation.

Create a bot with @BotFather and get your numeric Telegram user id, then write a config **outside any repository**, readable only by you (directory `700`, file `600`), at `~/Library/Application Support/DodgingInfinity/telegram/config.json`:

```json
{
  "bot_token": "<token from @BotFather>",
  "allowed_user_ids": [123456789],
  "repository": "/path/to/your/repository"
}
```

Run the adapter, and the Runtime that advances authorized missions beside it:

```bash
tgop run     # foreground; tgop install-agent installs the LaunchAgent
dirun run    # foreground; scripts/dirun-agent.sh install installs the LaunchAgent
```

In the chat, send an objective in plain language (or `/mission <intent>`), then approve or reject the returned plan **with its inline buttons** — typed text never approves anything. `/status` reports the current state, `/help` lists the surface. Only the numeric ids in `allowed_user_ids` are served, in private chats only.

What remote deliberately does not do today: no message can commit, push, tag, release or deploy. There is no continuous progress streaming today; use `/status` for current state. Details are in [Telegram remote operator](docs/reference/telegram-remote-operator.md) and [Runtime and host](docs/reference/runtime-and-host.md).

In the target architecture, Coordinator Grok Bot becomes the preferred universal front door: multimodal intake, mission requests, approvals, status across many missions at once, and collaboration with specialist bots. It is the interaction and conversation plane, not an authority holder — it owns no Mission authority, and canonical Mission truth remains in Dodging Infinity. It is not implemented today; Telegram remains the current reference transport and the fallback until a replacement is proven.

---

## Human control

> **Permission to solve the problem is not permission to ship the result.**

Three deterministic one-shot gates are enforced today by the installed Git guards:

```bash
herdctl approve-commit --repo my-repo   # binds repo, branch, HEAD, exact staged diff, short TTL
herdctl approve-push   --repo my-repo   # binds the commit and the remote ref
herdctl approve-push --tag vX.Y.Z       # binds one annotated tag object
herdctl push-tag vX.Y.Z
```

No approval inherits from another and none is reusable. Mission authorization does not authorize a commit, a commit does not authorize a push, a push does not authorize a merge, a merge does not authorize a release, and a release does not authorize a deploy. PR creation, merge, release and deploy remain outside the current automated delivery path.

These are deterministic controls, not a sandbox: Git's own bypass forms still exist, and runtime protections and role contracts complement human authorization rather than replace it. See [Human Git gates](docs/reference/human-git-gates.md) and [Authority and Safety](docs/wiki/Authority-and-Safety.md).

---

## Documentation

| Page | What it covers |
|---|---|
| [Wiki home](docs/wiki/Home.md) | Index, and the status label attached to every claim |
| [Architecture](docs/wiki/Architecture.md) | The full system picture |
| [Current vs End State](docs/wiki/Current-vs-End-State.md) | What is built and what is designed, one row per subsystem |
| [Missions and Lifecycle](docs/wiki/Missions-and-Lifecycle.md) · [Evidence and Verification](docs/wiki/Evidence-and-Verification.md) | What a Mission is, and what counts as done |
| [Authority and Safety](docs/wiki/Authority-and-Safety.md) | The authority model |
| [Capabilities and Workers](docs/wiki/Capabilities-and-Workers.md) · [OperatorSession](docs/wiki/OperatorSession.md) · [Herdr](docs/wiki/Herdr.md) | The layers below the Mission |
| [Operational reference](docs/reference/README.md) | Commands, Telegram, host, delivery gates, observability |
| [Roadmap](docs/wiki/Roadmap.md) · [CHANGELOG](CHANGELOG.md) · [Security](SECURITY.md) | Direction, history, disclosure |

---

## Development

The test suite is standard-library only and runs from the repository root:

```bash
PYTHONPATH="$PWD" python3 tests/test_static.py
PYTHONPATH="$PWD" python3 tests/test_release_narrative.py
```

CI runs every suite on macOS and Ubuntu against Python 3.9 and 3.13. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and report security issues through [SECURITY.md](SECURITY.md).

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
