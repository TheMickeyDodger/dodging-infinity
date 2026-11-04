/**
 * A POPULATED sample Mission for the details disclosure tests (Free UI streamline, A8.4). LABELLED SAMPLE: built on the scan specimen (DI_SCAN_LOCK, drawn with its own
 * "Sample data (fixture)" label), never presented as a real Mission.
 *
 * It carries everything Mission details must fold and give back exactly: a LONG description (several sentences, one with a line break), a multi-paragraph handoff, a nonempty
 * Swarm with one member whose identity is UNRESOLVED (the product's own words, from `identityOfMember`), one whose model is "not recorded" and one "last seen, not live", and one
 * runtime record not linked to a role. The harness (test/harness/diScanLock.tsx, `di-scan-case=populated`) draws it; the browser test reads these same strings to compare.
 */
import type { DiMember } from '../../core/src/messages.ts';
import { DI_SCAN_LOCK, type DiScanRow, type DiScanUnresolvedAgent, identityOfMember } from '../src/components/diScanModel.ts';

export const POPULATED_ID = 'sample-populated';
export const POPULATED_DESCRIPTION =
  'SAMPLE (fixture): make the readiness probe stable under load. The probe flakes when the queue is warm, so the Mission adds a bounded retry with a deadline, ' +
  'a regression test that reproduces the warm-queue flake, and a note in the operations guide.\nIt changes no schema, adds no dependency, and keeps the existing probe ' +
  'contract for every caller; the team reports the test run and the review as evidence.';
export const POPULATED_HANDOFF =
  'Supervisor has the context. Reviewer queue is clear.\nNo blockers.\nNext: Lead handoff to Executor, with the warm-queue reproduction attached.';
/** The member whose identity is unresolved: the product's own words for a session replaced by another. */
export const UNRESOLVED_ROLE = 'Executor';
export const UNRESOLVED_IDENTITY = identityOfMember({ classification: 'replaced', reason: 'session_replaced', live: false } as unknown as DiMember);
export const STRAY_AGENT: DiScanUnresolvedAgent = {
  agentRef: 'sample-agent-stray',
  name: 'w-sample-stray',
  state: 'unverified',
  label: 'Identity not verified',
  words: 'SAMPLE: a runtime record found under this Mission’s workspace. Its identity is not verified, and its role and task membership are not established.',
  reason: 'the runtime record named no verified identity',
  reasonCode: 'runtime_identity_unverified',
};

export function populatedRow(): DiScanRow {
  const base = DI_SCAN_LOCK[0]!;
  return {
    ...base,
    id: POPULATED_ID,
    title: 'Sample populated Mission (fixture)',
    description: POPULATED_DESCRIPTION,
    handoff: POPULATED_HANDOFF,
    swarm: base.swarm.map((member) => (member.role === UNRESOLVED_ROLE ? { ...member, identity: UNRESOLVED_IDENTITY } : member)),
    unresolvedAgents: [STRAY_AGENT],
  };
}
