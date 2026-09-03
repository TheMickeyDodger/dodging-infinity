# Telegram Remote Operator

Back to [Wiki home](../wiki/Home.md) · [Reference index](README.md)

## What Telegram is here

**[REFERENCE / FALLBACK]** Telegram is the current reference transport for
remote intent, plan approval, status, and verified results. It ships as an
MVP adapter (`telegram_operator/` package, `tgop` entry script). Current
`main` routes Telegram transport operations through the initial
provider-neutral `HumanInteractionAdapter` seam using
`TelegramHumanInteractionAdapter`. The broader target puts every human
surface behind this boundary and makes Grok Bot the preferred conversational
plane; Telegram remains the reference and the fallback until that is proven.
It is an adapter, not an execution system, and it has no direct path to Herdr
or `herdctl`. The isolation is enforced by the static suite.

The authority model the adapter implements is on the wiki
[Authority and Safety](../wiki/Authority-and-Safety.md) page. The
DI-REMOTE-2 remote-target flow it fronts is in the
[README](../../README.md#remote-target-repository-routing-di-remote-2-v070),
and the lifecycle it participates in is on
[Missions and Lifecycle](../wiki/Missions-and-Lifecycle.md).

## Telegram remote operator experience

This is the released v0.6.3 (v0.1 adapter) experience for a mission
against the configured local repository; a remote target mission adds
the one-shot Approve Mission flow described in the README's
[Remote Target Repository Routing](../../README.md#remote-target-repository-routing-di-remote-2-v070)
section.

The implemented v0.1 interaction is deliberately simpler than the machinery underneath it.

You are away from the Mac.

You open Telegram on your phone.

You send:

```text
Fix issue #702 in the external target repository.

Investigate the actual cause, preserve the repository contribution rules,
add the necessary verification, and prepare the result for delivery.
```

Telegram forwards the intent to the trusted Mac.

Codex investigates.

Your phone receives:

```text
PLAN READY

Repository:
external-target

Objective:
Resolve issue #702

Constraints:
- preserve compatibility
- follow contribution rules
- add regression coverage
- no delivery without separate approval

Proceed?

[Approve]
[Reject]
```

You approve once. That one-shot decision authorizes only the exact plan presented for that Telegram user, private chat, repository, Gateway request, Codex session, Telegram message, and plan digest. Ordinary Telegram text grants no authority.

Then you can put the phone away.

Codex:

- starts/restores required local execution components
- creates the Herdr handoff
- dispatches Herdr
- monitors progress
- handles routine recovery
- creates bounded follow-up missions when necessary
- independently verifies the final result

Herdr:

- decomposes
- implements
- tests
- reviews
- rejects when necessary
- corrects
- re-reviews
- produces evidence

While the mission runs, your phone receives no unsolicited progress
delivery: v0.1 has no proactive progress streaming. (The one
unsolicited message class is a restart/recovery notice about work you
sent that was interrupted, if the adapter restarts mid-mission.) If
you want to know how things are going, you send:

```text
/status
```

The adapter acknowledges immediately, and the answer (durable
lifecycle state plus a read-only Operator status snapshot) arrives
when it reaches the front of the queue behind the active engineering
work.

When the engineering turn you approved completes, its reply is the
verified result:

```text
MISSION COMPLETE

Reviewer:
APPROVE

Tests:
Passed

Changed files:
4

Delivery:
Awaiting a separate human-controlled local Git authorization
```

Telegram v0.1 does not authorize or perform a commit, push, PR, tag, release, deployment, or merge. Those actions remain behind the existing explicit human gates outside the Telegram adapter.

Your Mac did the engineering.

Your phone was the intent, plan, status, and result control surface.

## Setup detail

**[IMPLEMENTED / PROVEN]** Pinned by the `telegram_operator` suites.


Configuration lives OUTSIDE any repository, in
`~/Library/Application Support/DodgingInfinity/telegram/config.json`
(directory mode `700`, file mode `600`; the adapter refuses to load a
group/other-readable config because it holds the bot token):

```json
{
  "bot_token": "<token from @BotFather>",
  "allowed_user_ids": [123456789],
  "repository": "/path/to/one/repository"
}
```

- `allowed_user_ids` is an exact NUMERIC Telegram user-id allowlist.
- One repository per adapter instance.
- Durable adapter state (`state.json`) sits next to the config, also
  outside the repository, written atomically.

Run in the foreground with `tgop run`, or install the optional
per-user LaunchAgent with `tgop install-agent` (absolute paths,
RunAtLoad, KeepAlive with a restart throttle; logs in the protected
state directory, which the installer creates at mode `700`). An
explicit `--config PATH` given to `install-agent` is propagated into
the installed job as an absolute path, so the agent runs exactly the
named configuration, and the job's logs, state, and lock all live in
that config's directory (the default protected state directory
otherwise), never split across two locations. Because launchd does
not inherit your shell PATH, the installer resolves the `codex`
binary at INSTALL time and bakes its directory into the job's PATH
FIRST, ahead of a fixed constant list (never your ambient PATH), so
the exact validated binary always wins; installation refuses
with an actionable message when `codex` cannot be resolved, and the
agent must be reinstalled if the `codex` binary later moves. Disable
with `tgop uninstall-agent`; it unloads and removes exactly the
per-user plist the installer created. A single-instance lock refuses
a second concurrent adapter.

## Transport

The adapter uses genuine outbound Telegram Bot API long polling:
`getUpdates` with a positive server-side long-poll duration, and a
client socket deadline that is strictly LONGER than the long poll by a
hard constant margin (so the server always answers an idle poll before
the client gives up; a deadline firing on an idle poll is treated as a
normal empty poll, never an error and never a reason to disturb the
update offset). Failed polls recover with capped exponential backoff.
There is no webhook, no public listener, and no inbound port. These
deadlines exist ONLY in the Telegram transport; the Codex Gateway's
subprocess keeps its no-deadline behavior unchanged.

## Interaction

- Send natural-language intent (or `/mission <intent>`). The adapter
  authenticates the sender BEFORE parsing any content, then routes the
  intent through the Codex Gateway into a new or resumed Codex
  Operator session.
- The Operator answers through a versioned remote protocol envelope
  (plan / status / result / error). A free-form model message is never
  reinterpreted as an approved plan or a verified result.
- A `plan` reply is displayed first, with no controls; its one-shot
  **Approve / Reject** inline buttons are attached to the plan message
  only after complete delivery is proven and the exact message binding
  has been durably persisted.
- `/status` reports durable adapter lifecycle state first (the last
  gateway turn, queued items besides the status request itself,
  in-flight dispatch, approvable plans awaiting decision (counted
  across all chats; expired, consumed, and superseded approvals are
  excluded), and session-map evictions since first run, each an exact
  labelled count), then fetches engineering status through a
  separately constrained READ-ONLY Operator turn.
- `/status` is acknowledged immediately ("Gathering status…") but is
  ANSWERED through the same single worker that serializes every
  Gateway turn: a status request queues behind any active or
  already-queued Gateway work and is answered only when it reaches
  the front. Visibility is polled and lifecycle-based; there is no
  proactive progress streaming; send `/status` again for a fresh
  snapshot.
- `/help` (or `/start`) describes the commands.

## Approval binding

Plan approval is ONE-SHOT and bound to ALL of: the exact Telegram user
id, the private chat, the configured repository realpath, the Gateway
request, the Codex session, the Telegram plan message, the sha256
digest of the exact plan text, a random adapter-held nonce, and an
`expires_at` validity bound. No approval CONTROL exists before proof:
the plan text is sent with no keyboard, and the Approve / Reject
buttons are offered, on exactly the bound plan message, only after
the send outcome proves the complete plan text was displayed and the
message binding has been durably persisted. A plan too long to display
within the Telegram chunk cap is refused with no approval armed; a
truncated, partial, failed, or unverifiable plan delivery voids the
approval with an explanation and offers no buttons; and a failed or
unverifiable button offer voids the approval too; an actionable
approval binds exactly what was displayed, never undisplayed text. A
revised plan invalidates every prior
approval in the thread, and any intervening engineering turn in the
chat invalidates a still-pending approval at dispatch time (checking
/status does not). Replays, mismatches, expiry, and duplicates
all fail closed. The nonce never leaves the Mac: inline buttons carry
only an opaque approval id, and typed chat text can never forge the
adapter's decision envelope (marker-bearing user lines are visibly
quoted before forwarding).

## Recovery

Authority-bearing state is persisted before any external action, and
the Telegram offset advances only after accepted state is durably
stored. After a crash or restart the adapter reports
queued-but-undispatched work as dropped (re-send it) and
dispatched-but-unconfirmed work as AMBIGUOUS; it is never replayed
automatically.

## Delivery authority: local today, Telegram-native planned

**[IMPLEMENTED / PROVEN]** Remote delivery authority is not implemented today. No Telegram
message, plain text or approval callback, can commit, push, open a
PR, tag, release, or deploy. The adapter's decision envelope states
explicitly that it grants no delivery authority. Commit, push, PR,
tag, and release remain separate, human-authorized, local actions.

**[PLANNED / TARGET]** Telegram-native delivery authorization is now a Phase-I product requirement.
It will require exact mission/result/diff/ref binding, expiring one-shot human
approval, durable receipts, replay protection, and separate authorization at
each commit, push, PR, tag, release, deploy, or merge boundary. The design is
tracked in the [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md).

## Telegram security requirements

The adapter enforces, with static and behavioral regression tests:

- allowlist trusted NUMERIC Telegram user IDs, private chats only
- authenticate every update BEFORE parsing or persisting content
- reject unknown senders with no reply; nothing they send is parsed
  or persisted: no content, intent, approval, work, or session
  state. The only durable effect of a denied update is the transport
  update-offset advance (intended poll-loop bookkeeping, so a denied
  update is not re-fetched and cannot wedge the poller)
- never expose arbitrary shell execution
- never forward raw shell commands directly
- never invoke Herdr, never read orchestration state, not even a
  path string (isolation is enforced by the static suite)
- never bypass Codex
- never silently interpret chat text as Git authorization
- bind approval actions to a known Codex request/session and the
  exact bounded plan
- redact the bot token from every error and diagnostic surface
