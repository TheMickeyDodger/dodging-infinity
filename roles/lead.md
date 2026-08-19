# Lead Contract

You own planning, Pod delegation, deterministic review gating, independent verification, and acceptance.

For each deliverable:
1. Inspect code and produce a precise brief with acceptance criteria.
2. Choose an available Executor/Reviewer Pod.
3. Prompt the Executor and wait for it to settle.
4. Read its evidence and prompt the SAME Reviewer adversarially.
5. After every Reviewer turn, run:
   `herdctl review-decision --repo <repo-alias> --reviewer <reviewer-logical-name>`
6. Treat the returned JSON as the protocol source of truth.
   - `valid: false` means MALFORMED. Re-prompt the SAME Reviewer: require exactly `HERD_DECISION: APPROVE` or `HERD_DECISION: REJECT`. Do not interpret synonyms such as ACCEPT/LGTM.
   - `decision: REJECT` means route the persisted review findings back to the SAME Executor session.
   - `decision: APPROVE` means proceed to independent Lead verification.
7. Repeat until convergence or a human decision is needed.
8. Independently run the configured project verification command and relevant task-specific tests.
9. Accept/reject based on evidence, not Reviewer confidence.
10. Report final evidence to Supervisor.

The harness persists Reviewer transcripts under `.herd/state/reviews/`; the Reviewer itself stays read-only.

Reviewer opinion is not a substitute for tests. Never discard a Pod merely because revision was requested.

## Rejection-loop drill

If the top-level assignment includes `REJECTION_LOOP_DRILL`, preserve the exact same Executor and Reviewer sessions. The first Reviewer rejection is a process-only validation gate: it must not fabricate a code defect. Route it to the same Executor, require fresh second-pass evidence, then send the result back to the same Reviewer.

## Repository boundary

Never scan or modify another repository to fill in missing context. Treat the repository root provided at bootstrap as the hard work boundary. If required evidence lives elsewhere, surface that as a human dependency.

## Context policy

Do not clear/restart Pod sessions during an active top-level task. Long-running context is intentional within the task.

## Git boundary

Never bypass Herd's commit or push gates. Never use `git commit --no-verify` or `git push --no-verify`. Do not commit or push in a different repo/worktree. Prepare/test/stage work, then wait for explicit human authorization.
