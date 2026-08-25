# Changelog

## Unreleased

- Added the Telegram Remote Operator MVP (`telegram_operator/` package plus the `tgop` entry script): a trusted local Mac adapter that receives allowlisted private-chat Telegram intent and routes it exclusively through the Codex Gateway into the Codex Operator. It authenticates every update on its identity envelope before parsing any content (exact numeric user-id allowlist, private chats only), returns bounded plans through a versioned remote protocol (`plan`/`status`/`result`/`error` envelopes; free-form model text is never reinterpreted as a plan or result), offers one-shot Approve/Reject inline buttons bound to user + chat + repository realpath + gateway request + Codex session + plan message + exact plan digest + adapter-held nonce + `expires_at`, resumes the bound Codex session on approval, reports durable-state-first `/status` with a separately constrained read-only Operator turn, and reports interrupted work honestly after restart without ever replaying authority. Remote delivery authority (commit/push/PR/tag/release/deploy) is deferred and has no remote path.
- Telegram transport uses genuine outbound Bot API long polling (positive server-side long-poll duration; client socket deadline strictly greater by a hard constant margin; deadline on an idle poll handled as a normal empty poll) with capped retry/backoff, 4096-character message chunking with labelled truncation, bot-token redaction on every diagnostic surface, atomic JSON state outside the repository, a single-instance lock, and an optional per-user LaunchAgent (`tgop install-agent` / `tgop uninstall-agent`). The adapter never imports or invokes Herdr/herdctl and holds no orchestration-state path strings — enforced by the extended three-part isolation check in the static suite plus a behavioral audited filesystem test. `scripts/install.sh` now also installs a `tgop` wrapper; CI compiles the gateway and Telegram sources.

- Added Codex Gateway v0.1: a local, transport-neutral terminal boundary (`codex_gateway/` package plus the `codexgw` entry script) that routes human intent only into the existing Codex Operator workflow via the locally installed `codex` CLI. It validates the target repository (git worktree with `AGENTS.md` and `OPERATOR_PROTOCOL.md`) before any invocation, carries intent on stdin, supports stateless session continuation via `--resume SESSION_ID`, and maps outcomes to deterministic exit codes (0 completed, 2 invalid request, 3 codex unavailable, 4 codex failed, 5 malformed output) with a versioned `--json` result contract that always includes an `unrecognized_event_lines` count of output lines matching no declared event shape (disclosed on stderr in text mode when above zero). Both byte boundaries are guarded: intent (stdin/argv) must be valid UTF-8 and is refused as `invalid_request` otherwise, and Codex output is captured as bytes and decoded strictly, mapping an undecodable stream to `malformed_output` with a distinct `codex_output_not_utf8` error code. On the byte boundaries the gateway itself controls — intent in, Codex streams out — it never prints a traceback and always exits within {0,2,3,4,5}; like any Python CLI in this repository, rendering a valid non-ASCII message can still fail if the interpreter's stdout encoding is overridden to one that cannot represent it (e.g. `PYTHONIOENCODING=ascii`).
- The gateway recognizes Codex JSONL events only through a declared compatibility surface (named constants in `codex_gateway/codex_adapter.py`) and fails closed with `malformed_output` when no recognized terminal message is found; session handles are reported honestly as null when absent. Sandbox-weakening and check-bypassing Codex flags are banned by an in-code argv guard. The gateway never imports or invokes Herdr/herdctl — enforced by a three-part architectural isolation check (AST walk, token scan, behavioral import probe) in the static suite. `scripts/install.sh` now also installs a `codexgw` wrapper.

- Added `herdctl observe [--repo NAME] [--json]`: a schema-versioned (v1), strictly read-only, point-in-time projection of one repository's herd — repository/Git identity, config/workforce, mission, current task, runtime topology, bounded allowlisted live-agent state, recorded child dependencies, review metadata, artifact presence/freshness, bounded recent task summaries, and explicit per-source diagnostics. Human mode prints a concise summary; `--json` prints the canonical projection and nothing else. Exit is 0 whenever an observation is produced (even a fully PARTIAL one); only an unresolvable `--repo` reference exits 2. All logic is package-owned in `herdr/observe.py`.
- `herdctl observe` is hard-bounded by module constants never derived from repository input: 1 MiB state-file read limit, 64 live agent probes per run (config-expected roles first), 32 listed agents, 10 recent tasks, 40 review files, 32 children, 16 artifacts, 200-character truncation of every projected string, and a 2000-entry cap on directory scans. Missing, malformed, unreadable, or oversized sources degrade to explicit diagnostics — never a traceback, never a repair, never a mutation; Git queries run under `--no-optional-locks` so even `.git/index` stays byte-identical. The legacy `events.jsonl` journal is reported only under `legacy` and never presented as current activity.

- Added `herdctl health [--repo NAME]`: a repository-scoped, strictly read-only operational readiness probe. It checks herd configuration, Herdr server reachability, runtime state, that every expected agent resolves to a live Herdr agent, and task-state readability. Agent workflow states (idle/working/done/blocked) are reported as information, not failures; missing, unreachable, malformed, or unknown infrastructure fails with an actionable remedy and a nonzero exit. Complements `doctor` (environment/tooling probe) and `status` (task + agent state display).
- `herdctl health` bounds live-agent probing to a hard cap of 512 runtime entries per run (expected roles probed first), so a corrupt runtime map can never fan out unbounded `herdr agent get` subprocesses. A map exceeding the cap fails health with a count of the unprobed entries instead of reporting READY on unverified agents.
- `herdctl health` remedies no longer advertise `herdctl bootstrap --force` for the runtime states bootstrap cannot read past — invalid JSON, a non-object payload, a non-object `agents` value, or a corrupt recorded supervisor name — since bootstrap re-reads `.herd/state/runtime.json` before its force check and tracebacks on those states with or without `--force`. Those remedies now say to move the file aside or restore a valid JSON object first, then run plain `herdctl bootstrap`; `--force` is still advertised where it genuinely works, such as a corrupt non-supervisor agent name.

## v0.6.0

- Replaced the abandoned Mission Control architecture with a simpler Codex-as-operator model: human intent flows through Codex into Herdr's existing Supervisor, Lead, Executor, and Reviewer hierarchy.
- Added a durable mission contract under `.herd/state/mission.json`.
- Added `herdctl mission create` and `herdctl mission show`.
- Added `herdctl task --mission` to dispatch Codex-prepared missions through the existing Herdr Control Plane.
- Mission dispatch now carries the full objective, constraints, rules, acceptance criteria, and verification plan into the Supervisor task payload.
- Mission rules continue to flow through Herdr task policy for enforcement.
- Strengthened bootstrap boundaries so agents do not infer engineering work from repository state, verification commands, or shared memory before receiving an explicit task or delegation.
- Fixed one-shot push approval lifecycle so `git push --dry-run` does not consume authorization; approval is consumed only after the approved commit is observed on the approved remote-tracking ref.
- Added regression coverage for mission dispatch, push dry-runs, real pushes, fetch behavior, approval invalidation, and the updated operator workflow.
- Removed the abandoned Mission Control implementation while preserving Herdr's canonical CLI, orchestration engine, repository isolation, and human commit/push gates.

## v0.3.0

- Renamed the project to **Dodging Infinity**: an orchestration system for bounding unbounded objectives into finite, isolated, verifiable work.
- Added package-owned `HerdrControlPlane` as the primary programmatic orchestration interface.
- Added autonomous Supervisor-to-child-Herdr delegation through a structured orchestration bridge.
- Added durable parent/child task dependency tracking and deterministic parent completion gating.
- Added repository-scoped `herdctl rules`, `rules add`, and `rules remove` commands.
- Added repeatable task-scoped `herdctl task --rule` constraints that do not persist into repository configuration.
- Added hierarchical repository isolation: cross-repository implementation is delegated to separately scoped child Herdrs rather than performed directly by the parent.
- Extracted package-owned initialization, lifecycle, heartbeat, task dispatch, Git guards, registry, policy, runtime, orchestration, and dependency services under `herdr/`.
- Replaced reliance on Herdr's short initial prompt-settlement gate with harness-owned prompt observation and settlement.
- Added one safe retry for an unobserved bootstrap delivery while preserving single-delivery semantics for normal task prompts.
- Formalized model/runtime agnosticity: Supervisor, Lead, Executor, and Reviewer are logical roles that may be backed by any Herdr-supported model or runtime.
- Expanded regression coverage across policy, Control Plane operations, runtime prompting, lifecycle, initialization, heartbeat, guards, orchestration, dependencies, completion gating, and rules UX.

## v0.2.3

- Added built-in `max-quality`, `all-claude`, and `conservative` workforce presets.
- Added `herdctl init --preset ... --test-command ...`, `herdctl presets`, `herdctl preset`, and `herdctl set-test`.
- Added strict deterministic Reviewer protocol validation through `herdctl review-decision`.
- Reviewer transcripts are now persisted by the harness under `.herd/state/reviews/`, allowing Reviewer runtimes to remain read-only.
- Added one-shot human `approve-push` tokens bound to repo/branch/HEAD/remote/target.
- Added Git `pre-push` guard while preserving any pre-existing hook input.
- Expanded Claude PreToolUse protection to pushes, including `--no-verify` and destructive/force push forms.
- `safety-install` now also configures conservative Claude Auto Mode `$defaults` at user scope while preserving existing entries.
- Context reset is runtime-aware: Claude `/clear`, Codex `/new`; unknown runtime reset commands must be configured explicitly.
- Doctor now checks Codex when configured and validates the repository push guard.
- Strengthened repo-boundary and Git-boundary role contracts.

## v0.2.2

- Added deterministic ACTIVE/COMPLETE/ABORTED/ERROR task state.
- Added idle-aware Supervisor heartbeat.
- Added context checkpointing and bounded task history.
- Added `clear-contexts`, task completion/abort/status, and rejection-loop drill.
- Added safer reads for working/blocked alternate-screen agents.

## v0.2.1

- Fixed Herdr fresh-pane startup race by waiting/retrying for interactive shells.
- Added cleanup of partial failed workspaces.

## v0.2

- Added multi-repo registry and repo isolation.
- Added human one-shot commit authorization bound to exact repo/worktree/branch/HEAD/staged diff.
- Added Git reference-transaction history guard and Claude PreToolUse commit guard.
