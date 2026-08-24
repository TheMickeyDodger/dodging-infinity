# Changelog

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
