/**
 * Deliverable 3, part 1 (integration across the REAL seam), the webview's end: an ARCHIVED Mission's restart and execution are refused SERVER-SIDE, and the
 * request model says so in the panel's own words.
 *
 * The replies are what the office's REAL relay (server/src/di/missionRequests.ts DiMissionRequests.handle) emitted for the request panel's REAL HTTP answers
 * (tests/test_mission_archived_refusal_http.py, over mission_panel.http_adapter.PanelServer): `run dispatch` and `run observe` on a verified (archived)
 * Mission, `run dispatch` after its 30-day reclamation, and, as the countercase, `run dispatch` on an approved Mission that is not archived. They are fed
 * through the request model's own reducer (the path useDiMissionRequest takes). Nothing is live.
 *
 * The office itself never offers these actions on an archived Mission (its details draw them disabled, with the archived reason); this pins that the
 * PANEL refuses them too, and that a refusal reaching a page that sent one anyway is shown as a refusal, never as a start.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.ts';
import { INITIAL_STATE, type MrState, reduce } from '../src/components/diMissionRequestModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FIXTURE = path.join(root, 'test/fixtures/di-mission-request-archived-refusal-replies.FIXTURE.json');
const doc = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as {
  derived_from: { answers: { path: string; sha256: string } };
  replies: Record<string, DiMissionRequestReply>;
};

/** The model after the page sent `reply.op` and received `reply` for it (useDiMissionRequest's own two events). */
const answered = (reply: DiMissionRequestReply): MrState => {
  const sent = reduce(INITIAL_STATE, { type: 'sent', seq: reply.seq, op: reply.op, requestRef: null, turnId: null });
  return reduce(sent, { type: 'reply', nowMs: 1000, reply });
};

describe('the relayed replies are the real ones', () => {
  it('derive from the panel’s exported answers, byte for byte as recorded', () => {
    const bytes = fs.readFileSync(path.join(root, '..', doc.derived_from.answers.path));
    expect(crypto.createHash('sha256').update(bytes).digest('hex')).toBe(doc.derived_from.answers.sha256);
    expect(Object.keys(doc.replies).sort()).toEqual(['active_run_dispatch', 'archived_run_dispatch', 'archived_run_observe', 'reclaimed_run_dispatch']);
  });
});

describe('an archived Mission’s restart and execution, refused by the panel, read as refusals in its own words', () => {
  for (const name of ['archived_run_dispatch', 'archived_run_observe', 'reclaimed_run_dispatch']) {
    it(`${name}: refused as not offered for a verified record, and the model says exactly the panel’s reason`, () => {
      const reply = doc.replies[name]!;
      expect([reply.state, reply.problem]).toEqual(['refused', 'mission_panel_action_not_offered']);
      expect((reply.answer as { details: { stage: string } }).details.stage).toBe('verified');
      const state = answered(reply);
      expect(state.pending).toEqual([]);
      expect(state.notices).toHaveLength(1);
      expect(state.notices[0]).toMatchObject({ kind: 'refusal', op: 'run', text: reply.reason });
      expect(reply.reason).toMatch(/is not offered for a proposal that is verified$/);
      // nothing about a run starting is taken from it
      expect(JSON.stringify(state.views)).not.toMatch(/dispatched|running/);
    });
  }
  it('COUNTERCASE: the same route on a Mission that is not archived is accepted, and no refusal is said', () => {
    const reply = doc.replies.active_run_dispatch!;
    expect([reply.state, reply.problem]).toEqual(['ok', null]);
    const state = answered(reply);
    expect(state.notices.filter((n) => n.kind === 'refusal')).toEqual([]);
  });
});
