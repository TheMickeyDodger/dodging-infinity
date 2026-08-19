# Changelog

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
