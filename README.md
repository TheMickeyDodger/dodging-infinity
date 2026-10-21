![Dodging Infinity — AI orchestration and mission control for agents](https://raw.githubusercontent.com/TheMickeyDodger/dodging-infinity/84f28e2bc7ed48967d96e8f30c962b03f22c95ac/assets/brand/banner.jpeg)

# Dodging Infinity

**AI orchestration and mission control for agents. A living world for the teams doing the work.**

[![Multi-Agent Orchestration](https://img.shields.io/badge/Multi--Agent%20Orchestration-8B5CF6?style=flat-square)](#inside-the-engineering-team) [![Grok Bot Control](https://img.shields.io/badge/Grok%20Bot%20Control-06B6D4?style=flat-square)](#3-connect-grok-bot) [![Pixel Mission World](https://img.shields.io/badge/Pixel%20Mission%20World-F59E0B?style=flat-square)](#a-living-world-for-your-mission-teams) [![Research + Engineering](https://img.shields.io/badge/Research%20%2B%20Engineering-10B981?style=flat-square)](#built-through-real-work) [![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-A6E64B?style=flat-square)](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/LICENSE)

[Why DI](#why-dodging-infinity) · [How it works](#from-request-to-reviewed-delivery) · [Real work](#built-through-real-work) · [The visual world](#a-living-world-for-your-mission-teams) · [Get started](#get-started) · [Where DI is going](#where-di-is-going) · [Documentation](#documentation)

Dodging Infinity turns complex work into **Missions**: persistent records of the objective, approvals, progress, evidence, and results. It coordinates AI engineering teams through Herdr and gives you a clear way to direct the work, inspect what happened, and decide what gets delivered.

Start a Mission from your phone through **Grok Bot**, monitor it, update the work, review the result, and authorize commits and pushes. That workflow has been verified end to end. Codex provides the Outer Operator, Dodging Infinity manages the Mission and Runtime, and Herdr carries out the engineering.

And the team has a place you can explore. The **Pixel Agents DI office** turns observed Mission teams into selectable characters inside a living metropolis, with working offices, forests, a waterfront, a cathedral quarter, and a subway. Select an agent, inspect its Mission, and see the work in context. An office you deliberately configure for it can also take an in-world request, the optional [Codex Orchestrator](#an-optional-in-world-request-the-codex-orchestrator). After setup, open the running office by typing **`pixel`** in your terminal.

![The complete Dodging Infinity living metropolis, with the Mission inspector alongside the world map](https://raw.githubusercontent.com/TheMickeyDodger/dodging-infinity/84f28e2bc7ed48967d96e8f30c962b03f22c95ac/assets/pixel-world/world-map.png)

*The running DI world. The inspector connects the scene to recorded Missions, agents, and engineering activity.*

## Why Dodging Infinity

The work I want AI to help with can stretch across many sessions, repositories, tools, and rounds of review. A scientific investigation can lead into molecular modeling, method selection, source-code analysis, and a decision about what should run next. A quantum-software bug can cross library boundaries before the correct fix reaches an upstream maintainer.

That work needs continuity. The objective should stay clear. Decisions should remain attached to the work they authorize. Findings should retain their sources. A completed implementation should arrive with something you can inspect.

I built Dodging Infinity to carry that whole process as a Mission, from the initial request through execution, review, verification, and delivery. Its design has been exercised through computational mechanosynthesis research, scientific-software contributions, remote engineering workflows, and the development of its own visual world.

| What you get | What it means in practice |
|---|---|
| **Persistent Missions** | Objectives, revisions, approvals, progress, and evidence stay attached to the work across sessions. |
| **An engineering team** | Supervisor, Lead, Executor, and Reviewer have distinct responsibilities, with independent review built into the workflow. |
| **Remote operation** | Use Grok Bot to initiate Missions, monitor progress, request changes, inspect results, and carry approved work through commits and pushes. |
| **Inspectable results** | Review the implementation, test evidence, findings, and remaining questions against the Mission’s requirements. |
| **Clear delivery decisions** | Authorization to perform engineering work and authorization to deliver it are handled explicitly. |
| **A visual home** | Explore the office, select observed agents, switch Missions, and inspect the records behind the activity. An office configured for it can also take an in-world request (optional). |

## From request to reviewed delivery

A Mission carries the work through six steps:

1. **Describe the objective.** Explain the problem, target repository, constraints, and desired result.
2. **Review the proposed Mission.** The Operator turns the request into a scope you can inspect and approve.
3. **Launch the work.** DI’s Runtime advances the authorized workflow and hands engineering to Herdr.
4. **Stay involved.** Ask for progress, inspect blockers, and request updates. Changes to the approved scope receive their own decision.
5. **Review the result.** Herdr returns its work and review evidence; DI checks the result against the Mission’s requirements.
6. **Authorize delivery.** Commit and push the approved change through the delivery workflow, with the decision and outcome recorded.

### Current engineering workflow

```mermaid
flowchart TD
    U["You"] -->|Request and decisions| G["Grok Bot"]
    G -->|Intent and planning| C["Codex Outer Operator"]
    C -->|Mission proposal| D["DI Mission records and Runtime"]
    G -->|Mission approval| D
    D -->|Authorized engineering| H["Herdr"]
    H -->|Work, review, and evidence| D
    D -->|Status and results| G
    D -->|Recorded observations| O["Pixel Agents office"]
    D -->|Verified work| A["Delivery workflow"]
    G -->|Delivery approval| A
    A -->|Commit and push| R["GitHub repository"]
```

Grok Bot is the conversation interface. The Operator interprets the request. DI keeps the Mission’s state and advances its workflow. Herdr owns engineering execution. The office presents the recorded activity.

The same Mission remains the reference point as the work moves between these parts. The [target architecture](#target-architecture-one-mission-system) extends this foundation with Dots, shared attention, adaptive teams, and richer visual interaction.

### A conversation can carry the work forward

These are example requests you can make through the configured Grok interface:

**Start**

> Create a Mission for this repository. Investigate the failing export, identify the cause, add regression coverage, and propose the scope for my approval.

**Monitor**

> What is the team working on, what has been verified, and what needs a decision from me?

**Update**

> Add backward compatibility with the existing export format to the acceptance criteria. Show me the revised Mission before continuing with that change.

**Deliver**

> Show me the reviewed diff and verification results, then prepare the commit and push for my approval.

Progress and results can be requested while the host and its services remain available. Mission and delivery decisions stay attached to their specific scope.

## Built through real work

DI has been used on projects that require technical depth, sustained context, and careful review. These examples show the kinds of work that have shaped it.

### Computational mechanosynthesis

[**di-mechanosynthesis**](https://github.com/TheMickeyDodger/di-mechanosynthesis) investigates computational models of mechanically controlled carbon transfer at a silicon surface.

The work brings together literature analysis, molecular structure construction, computational chemistry, numerical validation, and reproducible provenance. DI has coordinated the preparation and review of atom-mapped donor candidates, a **409-atom tool–surface model**, study protocols, and an auditable research record. Independent review has driven corrections to method definitions and numerical acceptance criteria, while engine-validation work has led into source-level investigation.

The published work includes candidate geometries, reviewed research definitions, and documented validation attempts. Proposed structures and methods retain their scientific status throughout review; the repository distinguishes them from validated chemistry.

This is the continuity DI is built for: carrying the question, sources, models, evidence, and decisions through an extended research effort.

[Explore the research](https://github.com/TheMickeyDodger/di-mechanosynthesis) · [Study package](https://github.com/TheMickeyDodger/di-mechanosynthesis/tree/main/docs/ms-001) · [Structures](https://github.com/TheMickeyDodger/di-mechanosynthesis/tree/main/structures/ms-001) · [Evidence policy](https://github.com/TheMickeyDodger/di-mechanosynthesis/blob/main/docs/evidence-policy.md)

### Quantum software: a merged qBraid contribution

DI has also supported work on quantum software, including a [**merged qBraid SDK fix**](https://github.com/qBraid/qBraid/pull/1333) for converting Cirq reset operations into pyQuil.

Cirq circuits containing a reset could fail on the direct conversion path. The change adds the corresponding Quil `RESET` instruction while preserving the target qubit and operation order. Regression tests cover the conversion and explicitly reject unsupported resets on higher-dimensional systems.

The contribution passed upstream checks and was accepted and merged by a qBraid maintainer. It is a concrete example of taking a real defect through investigation, a focused implementation, verification, and upstream review.

| Project | Work carried through the process | Public result |
|---|---|---|
| **Computational mechanosynthesis** | Research planning, molecular models, method definitions, numerical criteria, provenance, and independent review | [Published research repository](https://github.com/TheMickeyDodger/di-mechanosynthesis) |
| **qBraid SDK** | Quantum-circuit conversion, reset semantics, regression tests, and upstream review | [Merged contribution](https://github.com/qBraid/qBraid/pull/1333) |
| **Pixel Agents DI office** | Mission catalog integration, selectable agents, observed state, and a connected visual world; with an optional, separately configured request panel (Codex Orchestrator) | [Integration and operating guide](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/pixel-agents-office.md) |

## Inside the engineering team

<img width="1774" height="887" alt="Pixel World Mission Handoff" src="https://github.com/user-attachments/assets/459f553c-2e7b-416f-93bc-09e6379432e5" />

Herdr gives engineering work an explicit organization.

| Role | Responsibility |
|---|---|
| **Supervisor** | Understands the engineering objective, decomposes the work, and establishes its direction. |
| **Lead** | Coordinates execution, follows dependencies, and determines when the work is ready for closure. |
| **Executor** | Implements the change and runs the required checks. |
| **Reviewer** | Inspects the result independently and can return it for correction. |

```mermaid
flowchart TD
    S["Supervisor"] -->|Direction and decomposition| L["Lead"]
    L -->|Scoped work| E["Executor"]
    E -->|Implementation and tests| R["Reviewer"]
    R -->|Corrections requested| E
    R -->|Review decision| L
    L -->|Engineering result and evidence| D["DI verification"]
```

The Reviewer works independently from the implementation. DI then evaluates the engineering result within the larger Mission, including its approved scope and proof requirements. Delivery proceeds through its own authorization. These responsibilities also underpin the [planned adaptive team](#target-engineering-a-team-chosen-for-the-assignment), where qualified models are selected for individual assignments.

[Engineering architecture](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/architecture.md#8-engineering-herdr) · [Operator protocol](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/OPERATOR_PROTOCOL.md)

## A living world for your Mission teams

The Pixel Agents DI office gives the system a place you can explore.

**Open your world with one command.** After [setup](#4-open-the-visual-office), with the office running:

```bash
pixel
```

Your default browser opens directly to the office. You can run the command from any directory.

The original palace opens into forests, mountain lakes, farms, a harbour, and a city of distinct districts. The **Botanical Atrium** and **Deco Observatory** add two more office complexes. A Gothic cathedral quarter, shops, cafés, galleries, and a subway connect the wider metropolis.

![The palace office and surrounding forest, with the observation deck and gateway](https://raw.githubusercontent.com/TheMickeyDodger/dodging-infinity/84f28e2bc7ed48967d96e8f30c962b03f22c95ac/assets/pixel-world/palace-and-forest.png)

*The palace remains the home office, with connected workspaces and an observation deck leading into the forest.*

| Inside the office | Along the waterfront |
|---|---|
| ![Characters inside the palace’s colourful offices and hallways](https://raw.githubusercontent.com/TheMickeyDodger/dodging-infinity/84f28e2bc7ed48967d96e8f30c962b03f22c95ac/assets/pixel-world/office-life.png) | ![Waterfront buildings, promenade, beach, and ocean](https://raw.githubusercontent.com/TheMickeyDodger/dodging-infinity/84f28e2bc7ed48967d96e8f30c962b03f22c95ac/assets/pixel-world/waterfront.png) |
| Connected rooms, desks, galleries, and characters moving through the workplace. | A harbour district with cafés, landmarks, and a long oceanfront. |

| Grand offices and cathedral | River Valley Farmland |
|---|---|
| ![The Botanical Atrium, Deco Observatory, and Gothic cathedral quarter](https://raw.githubusercontent.com/TheMickeyDodger/dodging-infinity/84f28e2bc7ed48967d96e8f30c962b03f22c95ac/assets/pixel-world/grand-offices-and-cathedral.png) | ![Farmland, barns, crops, river, and bridge](https://raw.githubusercontent.com/TheMickeyDodger/dodging-infinity/84f28e2bc7ed48967d96e8f30c962b03f22c95ac/assets/pixel-world/river-valley-farmland.png) |
| Two additional office complexes beside the cathedral and city destinations. | Countryside and river crossings extend the world beyond the city. |

The included living-metropolis preset spans **352 × 312 tiles**, with **145 decorative residents**, **four subway stations**, and **two trains**.

### See the work behind the characters

- **Select an agent** to inspect its role, source, and associated Mission.
- **Switch Missions** in the side panel, and open the inspector to follow available task chains and review records.
- **Recognize current activity** through the working-agent glow and visible freshness information.
- **Explore the world** with the map, district navigation, and camera controls.
- **Return to the office** and reconstruct the current recorded Mission observations.

Real agents come from DI’s Mission catalog. Ambient residents give the world its everyday life and are identified separately. An office that is simply running is a read-only view of recorded work; Mission commands and approvals remain in the operating interfaces unless you deliberately configure the optional request panel described below.

The screenshots above show the running local world. The living metropolis is an optional preset that can be imported into the office.

### A calm overview and a full-size inspector

The side panel answers three questions at a glance: **what is running, what is its observed status, and who is on it.** Each Mission shows its objective, one status (Running, Waiting, Blocked, Failed where the source reports a failure, Ended, Stale, Disconnected or Unknown) written as a word and a shape rather than colour alone, and its team. What could mislead you stays on the face: a stale or not-live view, a team that is not bound, an identity that is not verified.

![The side panel's overview under its “LIVE - FIXTURE DATA” header: two Missions, each with its objective, observed status and team, and an Open inspector button](assets/pixel-world/operational-panel-overview.png)

**Open inspector**, on a Mission or on the selected agent, opens a full-size inspector over the office: the objective as its source reports it (not the whole original task prompt), the status and the source fields it was derived from, the team, task chains and checkpoints where the source reports them, evidence and review records, and the freshness and limits of what was observed. It behaves as a dialog: focus moves in, Tab stays inside, and Escape or Close returns you to where you were. **Show in the office** takes you from a team member to that agent on the map. Evidence, code review and Mission requests stay reachable from the panel and inspector wherever your office offers them.

![The full-size inspector for a Mission: heading and status, objective, and the team with each member's identity and observed state](assets/pixel-world/operational-panel-inspector.png)

Both screenshots use synthetic test data, and the product says so in the frame (“LIVE - FIXTURE DATA”); how they were made is noted in [assets/pixel-world/operational-panel.md](assets/pixel-world/operational-panel.md). Status is what was observed, never assumed: unknown stays unknown rather than zero, a reported result is not shown as a verified one, and where a Mission's source exported no goal, such as a task row whose objective is only a section header, the panel says no goal was reported instead of inventing one. This is still a read-only view; it adds no new authority.

### An optional in-world request: the Codex Orchestrator

An office that is merely running and observing has **no** request or approval capability, and an existing or older setup does not gain one by upgrading. The office takes requests only when you deliberately **configure** it: you start the request panel and the progression runtime with the same state, control and workspace locations, and give the office the panel’s discovery file. Once configured, you can ask for work in ordinary words, read an exact proposal, give an exact human approval (an operator attestation, not verified human authorship), press an explicit Start, and then follow automatic, supervised progress to a retrievable verified result. Git delivery stays **separate and human-gated**: the panel does not commit, push, merge, or release.

This is a **different lane from the Grok Bot workflow above**. It is built on Mission Core and the local request surface; it is not an upgrade of the Grok route and not interchangeable with it, and Grok Bot does not gain this capability. Setup, the approval model, and the limits are in the [Codex Orchestrator guide](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/pixel-agents-office.md#requesting-missions-the-codex-orchestrator).

[Office setup](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/pixel-agents-office.md) · [World presets](https://github.com/TheMickeyDodger/dodging-infinity/tree/main/integrations/pixel-agents/packaging/di/worlds) · [Pixel Agents provenance](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/integrations/pixel-agents/UPSTREAM.md)

## Components and integrations

DI keeps the Mission’s records and operating rules separate from the tools that carry out the work.

| Component | Role |
|---|---|
| **Dodging Infinity** | Mission identity, approvals, state, Runtime progression, evidence, verification, and delivery records. |
| **Grok Bot** | The verified remote conversation interface for Mission deployment, monitoring, updates, and approved delivery. |
| **Codex** | The default Outer Operator for interpreting requests, preparing scoped work, and reasoning about results. |
| **Herdr** | Engineering execution through Supervisor, Lead, Executor, and Reviewer roles. |
| **Pixel Agents office** | Visual observation of recorded Missions, agents, task chains, and review information. |
| **Pi** | An optional Operator session implementation, selected explicitly and using the configured model and provider. |
| **Telegram** | An additional remote transport with its own setup and operating commands. |
| **Dots** | Optional access through a local Codex task; a dedicated integration is part of the broader interface direction. |
| **GitHub** | A source repository, collaboration surface, and delivery destination for engineering work. |

Herdr roles can use the model CLIs supported by their configured presets. The Operator boundary also allows an explicit choice of runtime. Automatic selection of the best model for each task is part of the project’s future direction.

[Grok Bot](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/grok-bot.md) · [Operator sessions](https://github.com/TheMickeyDodger/dodging-infinity/tree/main/operator_session) · [Telegram](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/operations.md#9-telegram-remote-control)

## Get started

### Requirements

The established host for the full remote workflow is **macOS**. Linux is also covered by core development and CI. Keep the host and the relevant services available while Missions run.

| Requirement | Used for |
|---|---|
| **Python 3.9+** | DI’s core Runtime and command-line tools. The core uses the Python standard library. |
| **Git** | Repository workspaces and delivery. |
| **[Herdr](https://github.com/herdrdev/herdr#install)** | Engineering execution. Install it and make the `herdr` command available on your path. |
| **[Codex CLI](https://github.com/openai/codex)** | The default Outer Operator and Codex-backed roles. Authenticate it before use. |
| **[Claude Code](https://code.claude.com/docs/en/overview)** | Claude-backed engineering roles. Authenticate it for presets that use Claude. |
| **Node.js 20+ and npm** | The optional Pixel Agents office. |
| **Grok Bot connector access** | The featured remote interface. |
| **Repository credentials** | Access to private repositories and authorized pushes. |

[Detailed requirements](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/operations.md#1-requirements)

### 1. Install the DI commands

After installing and authenticating the required tools:

```bash
git clone https://github.com/TheMickeyDodger/dodging-infinity.git
cd dodging-infinity
bash scripts/install.sh
export PATH="$HOME/.local/bin:$PATH"
herdctl safety-install
```

The installer adds **`herdctl`**, **`codexgw`**, **`tgop`**, **`dirun`**, and **`grokbot`** to `~/.local/bin`. Add that directory to your shell profile to keep the commands available in new terminals.

Model tools, credentials, remote configuration, and the office build are configured separately in the current setup.

### 2. Prepare a repository for local engineering

From the repository you want to work on:

```bash
cd /absolute/path/to/your-repository

herdctl init \
  --alias my-repo \
  --test-command 'npm test && npm run build'

herdctl doctor --repo my-repo
herdctl bootstrap --repo my-repo
herdctl health --repo my-repo
```

Replace the path and verification command with those appropriate to your project. The example uses a JavaScript project’s test and build commands.

To dispatch a local Herdr engineering task:

```bash
herdctl task \
  'Investigate the failing test, explain the cause, fix it, and verify the result. Do not commit.' \
  --repo my-repo

herdctl status --repo my-repo
herdctl observe --repo my-repo --json
```

This local path is useful for preparing a repository and working directly with its engineering team. Grok Missions use the DI Runtime workflow described next.

### 3. Connect Grok Bot

The remote setup connects three parts: the **Grok connector**, the **local DI endpoint**, and the **DI Runtime**.

Create the protected state directories and credentials described in the [Grok setup guide](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/grok-bot.md#running-the-endpoint-locally). Put the Runtime configuration inside the workflow-store directory it names:

```json
{
  "schema": "dodging-infinity.runtime-config.v1",
  "control_repository": "/absolute/path/to/dodging-infinity",
  "workflow_store": "/absolute/path/to/workflow-store"
}
```

Use canonical absolute paths. The workflow store belongs outside the DI checkout, with directory permissions `700` and configuration-file permissions `600`.

Start the Runtime in one terminal:

```bash
dirun \
  --runtime-config /absolute/path/to/workflow-store/runtime-config.json \
  run
```

Create the `mission-workspaces` directory outside your repositories before starting the endpoint. Then start the endpoint in another terminal, using the same workflow-store directory:

```bash
grokbot \
  --state-dir /absolute/path/to/grok-state \
  --repository /absolute/path/to/dodging-infinity \
  --control-repo /absolute/path/to/dodging-infinity \
  --workflow-store /absolute/path/to/workflow-store \
  --workspace-repository /absolute/path/to/your-repository \
  --workspaces-root /absolute/path/to/mission-workspaces \
  serve \
  --port 8765 \
  --auth-token-file /absolute/path/to/bearer-token
```

Expose the loopback endpoint through the configured HTTPS forwarder, register its `/mcp` URL and bearer credential in Grok’s custom connector, and enable the documented local-command capability for approval steps. Keep the Runtime and endpoint running.

You can then propose, approve, monitor, and update Missions through Grok, review the results, and authorize delivery. Grok-only operation uses the Runtime’s own configuration and does not require Telegram credentials.

[Complete Grok configuration](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/grok-bot.md) · [Runtime configuration](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/operations.md#running-a-runtime-without-telegram) · [HTTPS tunnel](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/tunnel.md)

### 4. Open the visual office

The office has its own installation and build inside the DI checkout:

```bash
cd /absolute/path/to/dodging-infinity
cd integrations/pixel-agents

git config --get core.hooksPath

HUSKY=0 SCARF_ANALYTICS=false DO_NOT_TRACK=1 \
  npm ci --foreground-scripts --no-audit --no-fund --no-update-notifier

git config --get core.hooksPath
npm run build
```

The two Git hook-path readings should match. An unset value prints nothing and returns status 1. The environment flags skip Husky setup and opt out of Scarf analytics.

From the DI checkout root, register the stores used by your running setup in a dedicated office catalog:

```bash
cd /absolute/path/to/dodging-infinity

python3 dicatalog.py register \
  --registry /absolute/path/to/office-catalog \
  --source-id mission_core \
  --kind mission_core \
  --store-dir /absolute/path/to/mission-core-store \
  --producer dodging-infinity

python3 dicatalog.py register \
  --registry /absolute/path/to/office-catalog \
  --source-id workflows \
  --kind workflow_store \
  --store-dir /absolute/path/to/workflow-store \
  --producer dodging-infinity

python3 dicatalog.py sources \
  --registry /absolute/path/to/office-catalog
```

The Mission Core store contains `missions.json`; the WorkflowStore contains `workflows.json`. Use the real stores from your setup. The [source-registration guide](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/pixel-agents-office.md#register-the-sources) explains how to identify them.

Start the catalog bridge from the DI checkout root:

```bash
python3 dicatalog_http.py \
  --registry /absolute/path/to/office-catalog \
  --discovery-file /absolute/path/to/office-bridge/backend-discovery.json \
  --discover-roots \
  --contract-version 2
```

In another terminal, also from the checkout root, start the office:

```bash
node integrations/pixel-agents/dist/di-cli.js \
  --state-dir /absolute/path/to/office-state \
  --backend-discovery /absolute/path/to/office-bridge/backend-discovery.json \
  --contract-version 2
```

Both processes use the same discovery file and contract version. Version 2 includes task-chain and review information.

Install the **`pixel`** launcher once, from the DI checkout root:

```bash
cd /absolute/path/to/dodging-infinity

mkdir -p "$HOME/.local/bin"
ln -s "$PWD/integrations/pixel-agents/packaging/di/pixel" \
  "$HOME/.local/bin/pixel"

export PATH="$HOME/.local/bin:$PATH"
```

Keep `~/.local/bin` on your shell’s path as described in step 1. If the launcher is already installed, reuse that installation.

Save the office location so the launcher remembers it across terminal sessions:

```bash
PIXEL_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agents-di"
mkdir -p "$PIXEL_CONFIG_DIR"

cat > "$PIXEL_CONFIG_DIR/pixel.json" <<'JSON'
{
  "state_dir": "/absolute/path/to/office-state"
}
JSON
```

Use the same absolute `state_dir` passed to the office process. An existing `PIXEL_AGENTS_DI_STATE_DIR` environment setting takes precedence over this file.

**From then on, with the catalog and office running, just type:**

```bash
pixel
```

The launcher opens your office in the default browser. The roadmap extends this everyday command with automatic presentation startup or reconnection and clearer connection diagnostics.

To load the world shown above, export any layout you want to keep, choose **Settings → Import Layout (paste JSON)**, paste the contents of [**`living-metropolis.json`**](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/integrations/pixel-agents/packaging/di/worlds/living-metropolis.json), and reload the page.

[Office installation and launch](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/pixel-agents-office.md) · [Launcher instructions](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/pixel-agents-office.md#the-pixel-launcher)

## How DI keeps the work accountable

- **The Mission defines the scope.** Objectives, constraints, acceptance criteria, and approvals belong to a specific version of the work.
- **Evidence has a source.** Test results, review decisions, research findings, and artifacts remain connected to the claims they support.
- **Review is independent.** The engineering Reviewer can reject a change and require another implementation pass.
- **Uncertainty stays visible.** Missing evidence, stale observations, and blockers remain explicit until they are resolved.
- **Delivery is deliberate.** Commit and push decisions are recorded for the work being delivered.

These principles apply across the technical work DI supports, from a repository fix to a sustained research project.

[Architecture](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/architecture.md) · [Security](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/SECURITY.md) · [Operator rules](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/AGENTS.md)

## Where DI is going

<img width="1774" height="887" alt="Pixel world teamwork hub" src="https://github.com/user-attachments/assets/9d6a4d47-ecfb-4113-b4ea-b3b05c8f0a2b" />

The goal is a system you can direct in ordinary language, leave working through supported interruptions, and return to with a clear account of what happened. You should be able to move between your phone, the Operator, and the office while following the same Missions, teams, decisions, and evidence.

This direction follows **roadmap v320, dated October 17, 2026**. The diagrams below show the **intended architecture as that roadmap is completed**. Development builds on the verified Grok workflow and existing DI, Herdr, and Pixel capabilities, with each addition demonstrated through its own acceptance scenarios.

### The six main stages

| Stage | What it brings to DI |
|---|---|
| **R1 · Dependable integrated operation** | Reliable unattended work, recovery, shared attention, evidence, reusable Mission memory, and a repeatable research workflow. Complete demonstrated gaps across the combined system. |
| **R2 · Adaptive teams** | Select qualified models for individual assignments, compare frontier and local execution, and escalate through a recorded handoff that preserves the work. |
| **R3 · Dots alongside Grok** | Use either interface to interact with the same Missions, decisions, progress, results, and delivery process. |
| **R4 · An everyday visual Mission world** | Dependable `pixel` launch, an operational overview, a reception inbox, distinct team headquarters, evidence browsing, and richer atmosphere. Validate the office integration in CI. |
| **R5 · Guided installation and product packaging** | One installation command, guided account and repository setup, a first verified Mission, useful diagnostics, and repeatable updates and recovery on another supported Mac. |
| **R6 · A measured durability decision** | Evaluate whether DBOS would improve recovery and operations. Adopt, defer, or retain the current design based on evidence. This is the last main decision. |

R1 also covers the selected document, operations, browser, media, and communication workflows; reusable context and verification recipes; capacity and priority controls; and specific security, Git, migration, and operational improvements. Existing tools and working behavior remain the starting point.

### Target architecture: one Mission system

Grok and Dots converge on the same Codex Operator and DI contracts. The visual world reads those records and, as desktop controls are added, routes actions through DI’s established authorization APIs.

```mermaid
flowchart TD
    U["You"] --> G["Grok Bot"]
    U --> B["Dots"]
    U --> P["Visual Mission World"]
    G <--> C["Codex Outer Operator"]
    B <--> C
    C <-->|Proposals, decisions and results| D["DI Mission state and authority"]
    D --> R["Runtime, readiness and recovery"]
    R --> H["Herdr engineering teams"]
    R --> T["Approved tools and research recipes"]
    H -->|Results and evidence| D
    T -->|Artifacts and sources| D
    D <-->|Relevant sourced lessons| M["Mission memory"]
    D --> O["Observation bridge and shared inbox"]
    O --> P
    P -.->|Future controls through DI APIs| D
```

**DI remains responsible for Mission identity, scope, authority, state, and delivery records.** Codex remains the Outer Operator. Herdr owns bounded engineering execution. Research and other selected capabilities use suitable existing tools and reviewed recipes, with results tied back to the Mission.

The shared inbox carries questions, blockers, approval requests, failed verification, results ready for review, and delivery decisions. Grok, Codex, and the office reception desk should explain the same pending item and its route for action. Dots joins those shared contracts as its integration is completed.

Work stays on supported local execution environments unless a selected workload needs another host or specialized capability. Expansion to additional machines must preserve identity, ownership, authority, evidence, and recovery. The visual presentation continues to use a thin DI bridge as its renderer and packaging evolve.

### Target engineering: a team chosen for the assignment

A Mission may contain very different kinds of work. A small interface change, a numerical algorithm, and an authorization change can require different models, tools, and levels of review.

The Supervisor defines the work and role requirements. A versioned selection policy chooses among approved, qualified configurations. Herdr launches and coordinates the resulting team.

```mermaid
flowchart TD
    S["Supervisor"] -->|Assignment requirements| P["Versioned selection policy"]
    Q["Approved models and qualifications"] --> P
    P -->|Choose coordination| L["Lead"]
    P -->|Choose and pin execution| E["Executor"]
    P -->|Choose independent review| R["Reviewer"]
    L -->|Scoped work| E
    E -->|Implementation and tests| R
    R -->|Corrections| E
    R -->|Review decision| L
    E -->|Permitted escalation| H["Checkpoint and release ownership"]
    H -->|Select next qualified Executor| P
    L -->|Reviewed result and evidence| V["DI verification"]
```

Selection considers the assignment’s required tools, context, correctness risks, permitted providers, available resources, and demonstrated results. The selected model stays fixed for its assignment. A change of model becomes an explicit reassignment with a recorded reason. If no qualified option is available, the assignment waits or surfaces a specific decision.

Escalation carries the exact workspace, objective, findings, attempted fixes, failed checks, logs, remaining budget, and applicable authority. The source relinquishes ownership before the destination continues, preserving one writer and the same Mission identity.

The rollout starts with a controlled **frontier-versus-local Executor comparison**, holding the Supervisor, Lead, Reviewer, interface, and checks fixed. Executor selection follows; a hybrid local-to-frontier path follows verified handoffs. Adaptive Lead and Reviewer selection then receive their own measured trials. Defaults are chosen by verified quality, missed defects, correction rounds, elapsed time, human interruptions, and total cost.

Model selection and handoff events become visible in Mission observations and the office. Available records should explain which model was requested, which identity was confirmed, why it was chosen, and whether work was transferred.

### Target workflow: continuity, evidence, and delivery

Reliable operation carries the Mission through interruptions, decisions, review, and delivery while retaining the information needed to continue correctly.

```mermaid
flowchart TD
    A["Authorized Mission"] --> W["Runtime execution"]
    W --> C["Canonical review"]
    C -->|Corrections| W
    C --> V["Independent verification"]
    V -->|Further work needed| W
    V -->|Verified candidate| E["Exact delivery proposal"]
    E -->|Separate approval| D["Authorized delivery"]
    D -->|Outcome confirmed| P["Delivery receipts"]
    W -->|Interrupted| K["Checkpoint and reconciliation"]
    K -->|Resume authorized work| W
    K -->|Needs your decision| I["Shared Mission inbox"]
    I -->|Decision through DI| K
    D -->|Outcome uncertain| Q["Reconcile remote effects"]
    Q -->|Already completed| P
    Q -->|Safe authorized retry| D
```

The runtime should explain why work stopped, what remains authorized, which checkpoints exist, and what can happen next. Recovery reconciles uncertain effects before retrying so a reconnect does not create a second dispatch or duplicate delivery.

Mission memory preserves useful repository knowledge, engineering decisions, and recovery lessons with sources and their applicable context. A later team can assess a lesson’s freshness and use it to avoid repeating a solved problem. Historical approvals remain records of earlier decisions; new work follows its applicable authorization.

The delivery lane prepares the candidate, documentation, and evidence for an exact decision tied to the repository, branch, operations, and destination. Valid approval can be reused within its binding, avoiding unnecessary repeated decisions. Commit, push, and PR receipts remain inspectable. Selected merge, release, deployment, publishing, or communication workflows retain their own supported routes and applicable approvals.

Research builds on the work DI already supports. The next milestone formalizes a repeatable research Mission with permitted sources, a useful report, supported claims, and an explicit verification rubric.

### The office becomes an everyday operating view

The world should help you answer three practical questions: **What is happening? What needs me? What did the team produce?**

| Planned experience | What you will be able to do |
|---|---|
| **Dependable `pixel` launch** | Open or reconnect to the presentation with one command. Get a specific remedy when the catalog or runtime is unavailable. Reopening the viewer preserves the running work. |
| **Reception desk and shared inbox** | Find questions, blockers, approval requests, failed checks, and results awaiting review in one consistent attention view. |
| **Daily operational overview** | Distinguish running, waiting, blocked, disconnected, and unknown states. Inspect runtime health, observation freshness, checkpoints, and actionable failure explanations, with a practical list alongside the world. |
| **Distinct Mission headquarters** | Give concurrent teams identifiable homes, readable labels, simple Mission switching, and a “find this team” camera action. Follow observed team membership and activity. |
| **Evidence library and Mission inspector** | Open artifacts and inspect assignments, dependencies, review findings, independent verification, and delivery receipts. See the source and freshness of the records. |
| **Visible model choices and handoffs** | Inspect recorded role selections, confirmed model identities where available, and the reasons for an escalation. |
| **Richer atmosphere** | Add day/night lighting, weather, and moving traffic while preserving responsive navigation, readable labels, reduced-motion support, and persistent preferences. |

The office remains a view over DI’s records. Future Mission controls use the same action and approval contracts as the other interfaces. Decorative residents and atmospheric effects retain their presentation role.

Office validation expands to lockfile installation, type checking, lint, server and UI tests, a production build, and browser checks through the actual catalog connection and Mission/agent rendering. These checks should catch an integration break during PR validation, alongside the existing core checks.

### Installation becomes one guided experience

The intended first-run experience starts with **one installation command from this README**. It detects the supported Mac environment, coordinates the required components, offers a recommended configuration, and guides the user through repository selection and account connections.

The installer then prepares DI and the selected office, checks their communication, explains what is running, and guides a small first Mission through approval, execution, and a verified result. Required account sign-in and operating-system prompts remain part of that guided experience. Advanced configuration and large local-model downloads remain optional.

Everyday operation adds coherent setup/resume, start, status, diagnosis, and update commands. A failed dependency, unavailable service, or configuration problem should come with a specific remedy. Supported upgrades and recovery preserve configuration, evidence, and authority on a second installation as well as the original host.

### The next reliability milestone

The daily-use track starts with execution reliability, dependable office launch, and the shared inbox. The operational overview, team headquarters, and evidence browsing build on those foundations. Delivery, reusable lessons, and the repeatable research workflow follow, with atmospheric upgrades after daily operation is dependable.

Its integrated acceptance exercise follows one bounded Mission through:

1. A request through Grok, with the same Mission and its bound team visible in Pixel.
2. A permitted interruption and supported recovery that preserve identity, work, and evidence.
3. A shared human decision where needed, followed by canonical review and independent verification.
4. Result and artifact inspection, then exact delivery approval and a PR with receipts.
5. Retries, reconnections, and viewer reopening that leave one execution and one intended delivery.

Adaptive team trials and Dots integration retain their own acceptance gates. Office, CI, and installer work can progress against stable shared contracts.

### Further capabilities, evaluated through real workloads

The roadmap also preserves a set of focused investigations. Each should earn its place through a useful result and measured improvement.

| Evaluation | Potential contribution |
|---|---|
| **Specialist advisor consultations** | Bounded advice on difficult designs while preserving the team’s canonical review and verification. |
| **External Herdr / Connecting the Machines** | Execution on another host when a real workload needs its tools or resources, with ownership and disconnect recovery demonstrated. |
| **OpenEvolve** | Algorithm improvement against a fixed correctness evaluator, held-out cases, and an explicit compute budget. |
| **TLA+/TLC or Lean** | Formal analysis of a bounded lifecycle behavior or mathematical property, with explicit assumptions and checkable artifacts. |
| **OR-Tools** | Constraint-based planning for work with resource limits, dependencies, and scheduling requirements. |
| **angr** | Authorized binary or firmware analysis, with findings reproduced in actual execution. |
| **LAMMPS or an appropriate scientific toolchain** | Reproducible simulation against reference cases before extending to new scientific candidates. |
| **TypeSafe AI / Jev** | A measured evaluation where a specific decision or verification bottleneck remains. |

Pi may support selected execution configurations where its adapters fit the required models and tools. LiteLLM can be evaluated if multiple compatible local or API endpoints justify an inference gateway. Reviewed pstack-derived recipes can support recall, explanation, and verification. These choices remain separate from adopting another orchestration system.

The final durability review asks whether DBOS improves demonstrated recovery or operating needs enough to justify a migration. Keeping the existing execution substrate remains a valid outcome.

[Project roadmap documentation](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/roadmap.md)

## Documentation

| Guide | What it covers |
|---|---|
| [Architecture](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/architecture.md) | Mission structure, system responsibilities, evidence, and execution boundaries. |
| [Operations](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/operations.md) | Requirements, repository setup, commands, Runtime configuration, and remote operation. |
| [Grok Bot](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/grok-bot.md) | Connector setup, endpoint configuration, Mission interaction, and delivery approvals. |
| [HTTPS tunnel](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/tunnel.md) | Making the local endpoint available to the remote connector. |
| [Pixel Agents office](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/pixel-agents-office.md) | Build, source registration, launch, world presets, and the browser interface. |
| [Operator protocol](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/OPERATOR_PROTOCOL.md) | Preparing work, coordinating with Herdr, inspecting results, and delivery. |
| [Roadmap](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/docs/roadmap.md) | Planned capabilities and acceptance criteria. |
| [Contributing](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/CONTRIBUTING.md) | Development setup, testing, and contribution expectations. |
| [Security](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/SECURITY.md) | Security boundaries and reporting guidance. |
| [Changelog](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/CHANGELOG.md) | Release history. |

## Contributing

Contributions are welcome across the orchestration core, integrations, documentation, tests, and visual office. Start with [CONTRIBUTING.md](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/CONTRIBUTING.md), and read the repository’s [operator rules](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/AGENTS.md) before initiating automated work.

For a proposed change, make the problem, scope, expected behavior, and verification clear. The core and office have separate dependency and validation requirements, described in their respective guides.

## License and acknowledgments

Dodging Infinity is licensed under **Apache 2.0**. See [LICENSE](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/LICENSE).

The vendored Pixel Agents office retains its own notices: MIT-licensed code, character art derived from CC0 sources, and a font under the SIL Open Font License 1.1. See the [Pixel Agents provenance and licensing guide](https://github.com/TheMickeyDodger/dodging-infinity/blob/main/integrations/pixel-agents/UPSTREAM.md#licensing).

The linked research and upstream software projects retain their own licenses and attribution requirements.

<img width="2172" height="724" alt="Dodging infinity on a stone platform" src="https://github.com/user-attachments/assets/7dbd4fc6-c9d2-42c7-84ba-9ad979c1e893" />

*The next Mission is yours. It’s hero time!*
