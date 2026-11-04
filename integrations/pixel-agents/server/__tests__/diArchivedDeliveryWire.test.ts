/**
 * Deliverable 2, round 10 (lead ruling: archived is read-only, not information-erasing), office side: the REAL producer answer exported by
 * tests/test_mission_retention_delivery_answer.py AD2 (a COMPLETE Herdr task whose delivery the catalog links by its own task and worktree, and a COMPLETE task
 * with no record) set as the response of the packaged contract-v2 envelope, projected by the REAL server (parseEnvelope, buildOfficeView,
 * buildDiOfficeStateMessage) and exported for the webview's archived-delivery tests. Nothing in the answer is edited here; the envelope's request time is the
 * answer's own `now`.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import { parseEnvelope } from '../src/di/catalogProjection.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const ROOT = path.join(__dirname, '..', '..');
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const ENVELOPE_REL = 'server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json';
const ANSWER_REL = 'server/__tests__/fixtures/di-backend/archived-delivery-herdr-complete.FIXTURE.json';
const TARGET = 'webview-ui/test/fixtures/di-office-state-archived-delivery-v2.FIXTURE.json';
const DELIVERY = 'prd-0a0a0a0a0a0a0a0a0a0a0a0a';

const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel));

describe('a COMPLETE Herdr task with a linked delivery, as the real producer answers it, reaches the office intact', () => {
  it('projects the real answer: both tasks terminal, one linked by task and worktree, the other unlinked with its handoff; exported for the webview', () => {
    const answerBytes = read(ANSWER_REL);
    const answer = JSON.parse(answerBytes.toString('utf-8')) as { now: number; response: Raw };
    const envelopeBytes = read(ENVELOPE_REL);
    const raw = clone(JSON.parse(envelopeBytes.toString('utf-8')) as Raw);
    raw.response = answer.response;
    raw.requested_at = answer.now;
    const parsed = parseEnvelope(raw);
    if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
    const state = buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, answer.now, 2), () => 41);
    const herdr = state.missions.missions.filter((m) => m.lifecycleModel === 'herdr.task/1');
    expect(herdr).toHaveLength(2);
    const linked = herdr.find((m) => m.v2.deliveryLink?.state === 'linked');
    const unlinked = herdr.find((m) => m.v2.deliveryLink?.state === 'unlinked_no_record');
    expect(linked?.v2.deliveryLink).toEqual({ state: 'linked', deliveryId: DELIVERY, basis: 'record_task_worktree_binding' });
    expect(unlinked?.v2.deliveryHandoff?.state).toBe('observed');
    for (const mission of herdr) expect(mission.standing, mission.missionRef).toBe('terminal');

    const doc = {
      fixture: true,
      label:
        'FIXTURE — the real producer answer of tests/test_mission_retention_delivery_answer.py AD2 (a COMPLETE Herdr task linked by its own task and worktree, ' +
        'and a COMPLETE task with no record), projected by the real server (parseEnvelope, buildOfficeView, buildDiOfficeStateMessage); evidences nothing about any live Mission',
      derived_from: {
        answer: { path: ANSWER_REL, sha256: crypto.createHash('sha256').update(answerBytes).digest('hex') },
        envelope: { path: ENVELOPE_REL, sha256: crypto.createHash('sha256').update(envelopeBytes).digest('hex') },
        edit: 'the envelope response set to the answer, its requested_at to the answer’s own now; nothing in the answer edited',
      },
      generator: 'server/__tests__/diArchivedDeliveryWire.test.ts',
      state,
    };
    const text = JSON.stringify(doc, null, 2) + '\n';
    const at = path.join(ROOT, TARGET);
    if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(at, text);
    expect(fs.readFileSync(at, 'utf-8')).toBe(text);
  });
});
