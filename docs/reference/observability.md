# Observability

Back to [Wiki home](../wiki/Home.md) · [Reference index](README.md)

**[IMPLEMENTED / PROVEN]** The read-only inspection commands as they ship
in v0.7.0. The `herdctl observe` schema v3 contract stays in the
[README](../../README.md#herdctl-observe), where `tests/test_docs_i8.py`
pins it against the production projection. What observation can and
cannot tell you, and how the target Observation Service differs from it,
is on the wiki [Observation and Recovery](../wiki/Observation-and-Recovery.md)
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
completeness, and the observation bounds it inherits are the ones the
README lists.
