# OperatorSession

Back to [Home](Home.md). Labels are defined on the Home page.

## The Operator role

**[IMPLEMENTED / PROVEN]** The Operator is the reasoning role for one mission. It understands the mission, examines evidence, decides what bounded work is needed, invokes approved capabilities, and coordinates engineering when necessary. It is a role, not a model brand. Dodging Infinity separates responsibilities from models: the outer Operator and the Herdr roles are logical responsibilities rather than permanent model assignments, and any model or runtime Herdr supports can be assigned to an execution layer.

```text
Supervisor  -> Model A
Lead        -> Model B
Executor    -> Model C
Reviewer    -> Model D
```

or the same model for all four. The orchestration contract belongs to the role. The model is a replaceable execution engine. Presets such as `max-quality`, `all-claude`, and `conservative` are convenience configurations, not architectural requirements; they assign runtimes, models, and permissions and do not alter the orchestration hierarchy.

**[REFERENCE / FALLBACK]** Codex is the current implementation of the Operator role, reached through the Codex Gateway. It stays the reference implementation and the fallback until another path is proven. It is never written as permanent architecture. The Gateway is on the [Codex Gateway](../reference/codex-gateway.md) page.

## The seam that exists today

**[IN PROGRESS]** This checkout carries the first provider-neutral Operator seam in the `operator_session/` package. It is on `main` and is now one of two initial Phase 1 seams, alongside `HumanInteractionAdapter`. PR #16 introduced the OperatorSession seam; PR #17 later added the interaction seam on top of it. The status stays IN PROGRESS because this is only the initial two-step `prepare()` / `execute()` abstraction and not the target lifecycle described below. Pinned by `tests/test_operator_session.py` (41 tests).

- `OperatorSession` is an abstract two-step boundary between a caller (a transport adapter, a CLI) and whatever operator provider carries the turn. `prepare(text, repository, session_id=None, source="terminal")` builds the provider request through a subclass hook, validates that the request carries a non-blank string request id, captures that id as a frozen field, records the turn in a per-session weak set, and returns a `PreparedTurn`. It makes no provider call and writes no state. `execute(prepared)` validates first (it must be a `PreparedTurn`, produced by this session's own `prepare`, whose request still reports the same request id) and only then makes exactly one provider call with the original request object, returning the provider's result unchanged. It retries nothing, swallows nothing, invents no status, and does not consume the turn.
- `PreparedTurn` binds the request, by reference, to the session that built it. Equality is identity: a turn is the turn `prepare` minted, never a value-equal look-alike. An ordinary construction fails provenance.
- `PreparedTurnError` means the turn cannot be executed and no provider call was made.
- `FunctionOperatorSession` adapts two injected callables, forwarding exactly `build_request_fn(text, repository, session_id=..., source=...)` and `submit_fn(request)`.
- `CodexOperatorSession` is the package's only provider import. It resolves `build_request` and `submit` as attributes of the `codex_gateway.gateway` module at call time, never at import time, so the gateway module remains the single point of substitution.
- The Telegram adapter constructs the seam: an explicit `operator_session` wins, an injected callable pair becomes a `FunctionOperatorSession`, and the default is `CodexOperatorSession`. Every serialized Gateway turn the adapter dispatches goes through `prepare` and then `execute`, with the in-flight marker persisted between them.

## What the seam owns and does not own

**[IN PROGRESS]** The seam owns request construction, provenance, and request-identity stability across the prepare-to-execute gap. It owns nothing else:

- no authority of any kind: no Mission Authorization, no Git operation, no delivery, push, tag, release, or deploy;
- no orchestration and no workflow lifecycle;
- no retry policy;
- no interpretation of the provider `session_id`, which flows through `prepare` as opaque continuation state that the seam passes to the provider and never stores or derives anything from.

Preserving v0.7.0 behavior was the constraint. The adapter's Gateway turn is unchanged in what it sends and what it does with the result; the seam only names the boundary that was already there.

## The target lifecycle

**[PLANNED / TARGET]** The target `OperatorSession` interface:

```text
OperatorSession
  create()
  prompt()
  steer()
  follow_up()
  abort()
  status()
  events()
  restore()
  close()
```

`create` opens a session bound to one mission and its manifest. `prompt` and `follow_up` deliver bounded work. `steer` adjusts an active session within its authorized scope. `abort` ends it with evidence preserved. `status` and `events` are read-only views the Observation Service can consume without interrupting the session. `restore` rebuilds a session from durable state after an Operator process disappears, which is what lets the Reconciler recover a mission without a human. `close` settles it. None of these exist in the tree. `prepare` and `execute` are not two of them; they are the narrower seam that the target lifecycle will sit behind.

Implementations in the target design are a Pi adapter and a Codex adapter. Success for the provider-neutral Operator means provider replacement does not change mission authority or mission semantics. That is Phase 4 on the [Roadmap](Roadmap.md).

## Provider roles: Pi, Codex, and others

| Provider | Role | Status |
|---|---|---|
| Codex | Current Operator implementation and fallback until another path is proven | REFERENCE / FALLBACK |
| Pi | Preferred candidate provider-neutral operator runtime; an RPC boundary is the preferred first integration | CANDIDATE |
| GPT | Potential model provider behind the OperatorSession | PLANNED / TARGET |
| Claude | Potential model provider behind the OperatorSession | PLANNED / TARGET |
| Grok models | Potential model provider behind the OperatorSession; separate from Grok Bot's role as an interaction plane | PLANNED / TARGET |
| Future supported providers | As above | PLANNED / TARGET |

A provider-neutral session makes it possible to compare models while holding the mission rules constant. No provider is a permanent assignment to any role.

## Domain Operator Profiles and Skill Packs

**[PLANNED / TARGET]** A Domain Operator Profile gives a mission reusable specialization: a Skill Pack, context sources, proof requirements, worker requirements, an escalation policy, and a version. A Skill Pack encodes how to approach a class of mission: for an engineering bug fix, investigate, plan, review, security, QA; for research, source discovery, source evaluation, comparison, synthesis, adversarial review, artifact generation; for automation assessment, process reconstruction, pain-point analysis, system-of-record mapping, opportunity scoring, ROI, pilot design, adversarial review; for browser QA, snapshot, interaction, visual proof, console and network inspection, regression evidence; for release preparation, release-doc reconciliation, diff inspection, verification, artifact packaging, delivery preparation.

Profiles improve reasoning. Skill Packs guide reasoning. Neither inherits or grants authority. The Ops Steward may later propose Profile and Skill Pack improvements through a governed path; it cannot change the rules that govern its own authority.

Related: [Architecture](Architecture.md) for where the session sits in the stack, and [Herdr](Herdr.md) for the engineering organization the Operator hands off to.
