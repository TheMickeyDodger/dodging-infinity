# Observability

Back to [Wiki home](../wiki/Home.md) · [Reference index](README.md)

**[IMPLEMENTED / PROVEN]** The read-only inspection commands as they ship
in v0.7.0, and the `herdctl observe` schema v3 contract, which
`tests/test_docs_i8.py` pins against the production projection. What
observation can and cannot tell you, and how the target Observation Service
differs from it, is on the wiki [Observation and Recovery](../wiki/Observation-and-Recovery.md)
page.

## Inspecting a herd

Four commands answer four different questions:

- `doctor`: are the environment, binaries, runtime kinds, and Git guards installed?
- `status`: what is the Herdr currently doing?
- `health`: is this repository's Herdr operational and usable right now?
- `observe`: what does all bounded persisted and live-queryable state say right now?

## `herdctl health`

```text
herdctl health [--repo NAME]
```

`health` is strictly read-only.

It checks:

- Herdr configuration
- server reachability
- runtime state
- expected/live agents
- task-state readability

Valid workflow states such as:

```text
idle
working
done
blocked
```

are informational rather than infrastructure failures.

Missing, unreachable, malformed, or unknown required infrastructure fails with actionable diagnostics and a nonzero exit.

Healthy state returns:

```text
Health: READY
```

with exit `0`.

Agent probing remains bounded.

## Reading a health report

`health` answers one question: is this repository's Herdr operational and
usable right now? It reads configuration and runtime state, probes the
expected live agents within a fixed bound, and checks that task state is
readable. A workflow state of `blocked` or `idle` is information about the
work, not a failure of the infrastructure, and does not change the exit
code. A missing binary, an unreachable server, or malformed required state
does. The command repairs nothing; every finding is a diagnostic with an
actionable message.

## Relationship to `herdctl observe`

`health` is a readiness probe with a pass or fail exit. `observe` is a
point-in-time projection of everything bounded that can be read about the
herd, with a schema version and a `completeness` field that describes
visibility only. Both are strictly read-only: neither mutates, repairs,
prompts an agent, changes workflow, or controls execution. Observation is a
reporting surface, not a gate. The DI-REMOTE-2 Runtime consumes the same
read-only projection to observe a target herd, under source-scoped
completeness, and the observation bounds it inherits are the ones listed
below.

## What observation does not tell you

Read this before the architecture, not after it. These are limits of the
EVIDENCE, not gaps in the implementation, and each is pinned by a named test —
see "Claim-to-pin map" below for which one.

- **The model a RUNNING agent uses is not observable through the agent
  interface (F1).** `herdctl observe` reports `configured_model` — the model a
  role's CONFIGURATION asks for — and states that limit in its own diagnostics.
  The projection carries no `running_model` field, no `model_observable` flag
  and no verdict about a running model, because such a field would imply a
  distinction the evidence cannot support.
- **A verdict cannot distinguish a model substitution from a restart (F2).**
  Where a substitution preserves the agent's session, the two situations are
  not representably different in what this system can see, and the surface says
  so rather than guessing.
- **A turn record written by a different build of the observer is a claim made
  by different logic.** Skew is reported, naming both the build that wrote the
  record and the build on disk, rather than reconciled silently.

## `herdctl observe` schema v3

The projection is schema version 3. Top-level keys, in the order the projection
emits them:

```text
schema_version
generated_at
completeness
repository
config
vintage
checkpoint
roles
turns
mission
task
runtime
agents
children
reviews
artifacts
recent_tasks
legacy
diagnostics
```

`vintage`, `checkpoint`, `roles` and `turns` arrived after schema v1: they carry
the task a state file belongs to, the artifacts that disagree about it, the
role-to-agent bindings, and the turn records for this task.

Every source section uses a closed source-state vocabulary:

```text
available
missing
malformed
unreadable
unavailable
empty
```

`completeness` describes visibility only.

It does not affect execution.

## What observation says about models

A role's model appears in the projection under the key `configured_model`, and
in the human render as `model-CONFIGURED=`. Both name the CONFIGURATION, and
the unqualified `model` key that preceded them is gone rather than merely
renamed alongside.

The projection also carries the limit itself as a diagnostic, so a consumer
that reads only the JSON meets it without reading this document:

```text
NO running-model value exists in this document and there is no unqualified
`model` key: the model a RUNNING agent uses is not observable through the
agent interface. `configured_model` states intent.
```

A role whose configuration names no model renders `(unset)` — stated as unset
rather than guessed, and not reported as unknown.

## Hard observation bounds

Observation limits are constants rather than repository-controlled values.

Current bounds include:

- 1 MiB state-file limit
- 64 live agent probes
- 32 listed agents
- 10 recent tasks
- 40 review files
- 32 listed children
- 16 artifacts
- 200-character projected strings
- 2000-entry directory scan budget
- 2000-line dirty-file count cap

Bounds are disclosed rather than silently presenting partial information as complete.

## Observation non-goals

`observe` is not:

- a stream
- a daemon
- a control surface
- a repair command
- a TUI
- a replacement Mission Control

It is an instrument panel.
