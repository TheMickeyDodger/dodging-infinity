/**
 * A PRODUCTION scan row: one Mission projected through the real `scanRowsFromOffice` from a minimal office state, the
 * same path the product panel takes (DiOfficePanel projects the office state's Missions into the shared component).
 * TEST ONLY. `attention` lists the Missions Reception says need the person.
 */
import type { DiOfficeState } from '../../../core/src/messages.ts';
import { type DiScanRow, scanRowsFromOffice } from '../../src/components/diScanModel.ts';

/** The office clock the helper's office state carries (epoch seconds). */
export const OFFICE_CLOCK_S = 1_800_000_000;

/**
 * `office` overrides the office state's own fields (TEST ONLY): `{ reception: undefined }` (no Reception read),
 * `{ reception: { entries: [], entriesTotal: 3 } }` (a list cut short), `{ live: false, notLiveReason: ... }` (stale).
 */
export function productionRow(
  missionRef: string,
  partial: Record<string, unknown>,
  attention: string[] = [],
  office: Record<string, unknown> = {},
): DiScanRow {
  const mission = {
    missionRef,
    nativeId: missionRef,
    sourceId: 'herd',
    sourceKind: 'herdr_task',
    lifecycleModel: 'herdr.task/1',
    lifecycleState: 'IDLE',
    standing: 'inactive',
    bindingState: 'bound',
    bindingReason: 'canonical_chain_complete',
    producerDeclared: 'herdctl',
    live: true,
    notLiveReason: null,
    objective: { state: 'observed', value: 'Ship the panel', reason: null },
    updatedAt: { state: 'not_recorded', value: null, reason: null },
    identitySource: { state: 'not_recorded', value: null, reason: null },
    paused: { state: 'not_applicable', value: null, reason: null },
    cancelRequested: { state: 'not_applicable', value: null, reason: null },
    ambiguity: { state: 'not_applicable', value: null, reason: null },
    members: { state: 'observed', reason: null, members: [] },
    v2: { projected: false },
    ...partial,
  };
  const state = {
    live: true,
    notLiveReason: null,
    envelope: { envelopeProblem: null, retryAfterSeconds: null, fresh: true },
    missions: { state: 'observed', reason: null, missions: [mission] },
    // a Reception the server read IN FULL (one category decided for every Mission, no notice): the default, so a Mission
    // it does not list is KNOWN not to need you; tests override it to model missing, cut-short or stale coverage
    reception: {
      entries: attention.map((ref) => ({ missionRef: ref })),
      notices: [],
      categories: [
        {
          category: 'authorization',
          label: 'Authorization',
          outcome: attention.length > 0 ? 'present' : 'known_empty',
          reason: null,
          counts: {
            present: attention.length,
            knownEmpty: attention.length > 0 ? 0 : 1,
            unsupported: 0,
            unavailable: 0,
          },
          gaps: [],
        },
      ],
    },
    // the office clock (epoch seconds), so a record's update time reads as an age
    times: { evaluatedAt: OFFICE_CLOCK_S },
    ...office,
  } as unknown as DiOfficeState;
  const row = scanRowsFromOffice(state)[0];
  if (!row) throw new Error('no row projected');
  return row;
}
