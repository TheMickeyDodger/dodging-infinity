# Codex Gateway

Back to [Wiki home](../wiki/Home.md) · [Reference index](README.md)

## What the Gateway is

**[REFERENCE / FALLBACK]** Codex Gateway v0.1 (released in v0.6.2) is the
local, transport-neutral interface boundary in front of the current
reference Operator, which is Codex. In this checkout the Gateway is reached
through the provider-neutral `OperatorSession` seam described on the wiki
[OperatorSession](../wiki/OperatorSession.md) page; `CodexOperatorSession`
resolves the Gateway's `build_request` and `submit` at call time. The
target design replaces the Operator behind that seam. Codex remains the
reference implementation and the fallback until another path is proven.

Codex Gateway v0.1 adds a local, transport-neutral interface boundary in front of the existing Codex Operator workflow.

Its job is intentionally narrow:

```text
Human intent
    |
    v
codexgw
    |
    v
Codex Gateway
    |
    v
Local Codex CLI
    |
    v
AGENTS.md + OPERATOR_PROTOCOL.md
    |
    v
Existing Codex Operator workflow
    |
    v
Herdr only when Codex decides to dispatch engineering work
```

Gateway v0.1 provides:

- **Versioned request/response contracts**
- **Local terminal entry point**
- **New Codex Operator sessions**
- **Resumed Codex Operator sessions**
- **Repository/operator-contract validation**
- **Subprocess argument isolation**
- **Fail-closed structured output**
- **Strict UTF-8 boundaries**
- **Bounded errors**
- **Hermetic regression coverage**
- **Static enforcement of the gateway/Herdr architectural boundary**

The gateway invokes the installed Codex CLI.

It never becomes an engineering runtime.

### Isolation

Gateway source must not:

- import `herdr`
- call `HerdrControlPlane`
- invoke `herdctl`
- manipulate `.herd`
- construct mission envelopes
- prompt Supervisor, Lead, Executor, or Reviewer
- grant execution approval
- perform Git delivery

Engineering remains downstream of Codex.

### Non-goals

Gateway v0.1 intentionally does not provide:

- remote networking
- Telegram
- authentication
- a daemon
- HTTP
- sockets
- queues
- mission construction
- Herdr dispatch
- Herdr lifecycle management
- commit/push/tag/release authority

Those responsibilities remain outside the Gateway itself. The Telegram adapter supplies Telegram transport, allowlist authentication, and the optional long-running LaunchAgent process as a client of the Gateway; it does not add those capabilities to the Gateway. Mission construction, Herdr dispatch and lifecycle management, and delivery authority remain downstream or separately deferred.

### Live compatibility validation

Gateway tests remain hermetic and mock Codex rather than consuming a real Codex engineering turn.

Before the Telegram adapter was enabled, the installed Codex CLI was separately checked against the declared compatibility boundary. That validation covered:

- new Codex sessions work
- resumed Codex sessions work
- `AGENTS.md` is loaded from the target repository
- `OPERATOR_PROTOCOL.md` remains authoritative
- clarification responses round-trip correctly
- approval requests round-trip correctly
- real Codex structured events match the gateway parser
- malformed/unexpected events fail closed
- no direct Herdr path exists through the gateway

## Command surface

The Codex Gateway is a separate outer interface.

Installed CLI:

```text
codexgw
```

Inspect installed options:

```bash
codexgw --help
```

The gateway intentionally remains separate from `herdctl`.

That separation is architectural, not cosmetic.
