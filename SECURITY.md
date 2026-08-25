# Security Policy

## Supported versions

Security fixes are currently provided for the latest Dodging Infinity release line.

| Version | Supported |
| --- | --- |
| 0.6.x | Yes |
| < 0.6 | No |

## Reporting a vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Use GitHub private vulnerability reporting for this repository when available. Include enough information to reproduce and understand the impact of the issue, including affected components, relevant configuration, and a minimal proof of concept when appropriate.

Please avoid including secrets, credentials, private repository contents, or unrelated user data in a report.

## Security-sensitive areas

Changes involving the following areas deserve additional scrutiny:

- repository and worktree isolation
- Git commit and push authorization guards
- runtime permissions and command execution
- cross-repository child-Herdr orchestration
- policy and rule enforcement
- dependency and completion gating
- prompt delivery and runtime settlement
- handling of local configuration, tokens, and credentials
- the Telegram Remote Operator adapter (remote input surface, bot
  token handling, approval binding)

Security fixes should include regression coverage whenever practical.

## Telegram Remote Operator surface

The Telegram adapter (`telegram_operator/`, `tgop`) is a
security-sensitive remote input surface. Its properties, all covered
by regression tests:

- **Outbound-only transport.** Genuine Bot API long polling; no
  webhook, no listener, no inbound port. The client socket deadline is
  a hard constant strictly greater than the server-side long-poll
  duration, so an idle poll is answered by the server, never aborted
  by the client; a deadline firing is handled as a normal empty poll.
  Failed polls recover with capped exponential backoff. These
  deadlines apply only to the Telegram transport — the Codex Gateway
  subprocess keeps its no-deadline behavior.
- **LaunchAgent KeepAlive limits.** KeepAlive restarts a process that
  exits; it cannot rescue a process that is alive but wedged. The
  restart throttle bounds relaunch frequency.
- **LaunchAgent executable resolution.** launchd does not inherit the
  interactive shell PATH, so the installed job's PATH is composed at
  install time from the directory the `codex` binary resolves to,
  placed FIRST so the validated binary cannot be shadowed by a
  different `codex` in a base directory, followed by a fixed
  hard-coded directory list — the ambient PATH is never passed
  through. Installation fails closed when `codex` is not resolvable,
  and the agent must be reinstalled if the binary moves.
  An explicit `--config` is propagated into the job absolutely;
  installing against a silently different default configuration is
  refused by construction.
- **Bot token handling.** The token sits in the URL path of every Bot
  API request, so every error, diagnostic, and exception string that
  can carry a URL is redacted before leaving the transport module. The
  config file must be mode `600` in a mode `700` directory; a
  group/other-readable config is refused, never repaired.
- **Authentication before content.** Every update is authenticated on
  its identity envelope (exact numeric user id, private chat, chat/user
  consistency) before any content is parsed or persisted. Unknown
  senders get no reply, and nothing they send is parsed or persisted —
  no content, intent, approval, work, or session state. The only
  durable effect of a denied update is the transport update-offset
  advance that stops the poller re-fetching it (intended, so a hostile
  update cannot wedge the poll loop).
- **Approval binding.** Plan approval is one-shot and bound to user,
  chat, repository realpath, gateway request, Codex session, plan
  message, exact plan digest, an adapter-held nonce, and an
  `expires_at` validity bound. No approval control exists before
  proof: the plan is sent with no keyboard, and the Approve/Reject
  buttons are attached to the bound plan message only after the send
  outcome proves the complete plan text was displayed and the message
  binding is durably persisted. A plan too long for the Telegram chunk
  cap is refused with no approval armed; truncated, partial, failed,
  or unverifiable delivery voids the approval with no buttons ever
  offered; and a failed or unverifiable button offer voids the
  approval too — an actionable approval never covers undisplayed
  text. The nonce never reaches the phone;
  callback buttons carry only an opaque id. Typed text containing the
  protocol marker is visibly quoted so a hand-typed decision envelope
  can never be accepted. Replay, mismatch, expiry, revision,
  duplicate-update, and crash-ambiguity paths all fail closed, and
  interrupted dispatches are reported and never replayed.
- **No delivery authority.** No remote message can commit, push, open
  a PR, tag, release, or deploy; the adapter's decision envelope says
  so explicitly. Delivery remains local and human-authorized.
- **Isolation.** Adapter and gateway sources never import, invoke, or
  reference the orchestration layer or its state directory — enforced
  statically (AST walk, token scan) and behaviorally (import probe and
  an audited end-to-end filesystem check).
