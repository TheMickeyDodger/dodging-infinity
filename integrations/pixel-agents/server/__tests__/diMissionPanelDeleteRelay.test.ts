/**
 * Deliverable 2, round 9 finding 1: the owner's "Delete local data" through the office's REAL relay (`DiMissionRequests.handle`), fed the exact answers the panel's
 * real HTTP route sends (fixtures/di-backend/mission-panel-delete-answers.FIXTURE.json, generated and drift-checked by tests/test_mission_retention_http.py). What is
 * asserted is the CLASSIFICATION the relay emits (`state`), never a problem prefix: a deletion that started and did not finish is ACCEPTED (`ok`, its answer saying
 * `deletion: pending`), never `refused`; a refusal stays `refused`.
 *
 * The relayed replies are exported for the webview (DI_EXPORT_OFFICE_STATE=1 writes webview-ui/test/fixtures/di-mission-request-delete-replies.FIXTURE.json;
 * otherwise it is checked for drift), so the words a person reads are tested on the reply this relay really emits.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.js';
import type { PanelCall, PanelResult } from '../src/di/missionPanelClient.js';
import { DiMissionRequests } from '../src/di/missionRequests.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const ANSWERS_REL = 'server/__tests__/fixtures/di-backend/mission-panel-delete-answers.FIXTURE.json';
const REPLIES_REL = 'webview-ui/test/fixtures/di-mission-request-delete-replies.FIXTURE.json';
const MISSION = `mn-${'0'.repeat(32)}`;
const REF = `mc:${MISSION}`;
const STORE = 'a'.repeat(64);

type Answer = { status: number; body: Record<string, unknown> };
const answers = (
  JSON.parse(fs.readFileSync(path.join(ROOT, ANSWERS_REL), 'utf-8')) as { answers: Record<string, Answer> }
).answers;

async function relay(answer: Answer | PanelResult, displayedState: string): Promise<{ reply: DiMissionRequestReply; calls: PanelCall[] }> {
  const sent: DiMissionRequestReply[] = [];
  const calls: PanelCall[] = [];
  const relayer = new DiMissionRequests('/disc.json', {
    readDiscovery: () => ({ ok: true as const, endpoint: { port: 4321, token: 'relay-test-token' } }),
    request: async (_endpoint, call): Promise<PanelResult> => {
      calls.push(call);
      return 'kind' in answer ? answer : { kind: 'http', status: answer.status, body: answer.body, bodyProblem: null };
    },
    log: () => undefined,
  });
  await relayer.handle(
    { type: 'diMissionRequest', seq: 7, op: 'deleteLocalData', missionId: MISSION, displayedState, missionRef: REF, storeIdentity: STORE },
    (m) => sent.push(m as unknown as DiMissionRequestReply),
  );
  expect(sent).toHaveLength(1);
  return { reply: sent[0]!, calls };
}

describe('the real relay classifies the panel’s real delete answers honestly', () => {
  const replies: Record<string, DiMissionRequestReply> = {};

  it('sends the delete to the one route, with the state the window displayed', async () => {
    const { calls } = await relay(answers.completed_after_reclamation!, 'COMPLETED');
    expect(calls).toEqual([
      { method: 'POST', path: `/missions/${MISSION}/delete-local-data`, body: { displayed_state: 'COMPLETED', mission_ref: REF, store_identity: STORE } },
    ]);
  });
  it('a completed deletion of a reclaimed Mission is ok, and its answer says what was removed', async () => {
    const { reply } = await relay(answers.completed_after_reclamation!, 'COMPLETED');
    expect(reply.state).toBe('ok');
    expect(reply.answer).toMatchObject({ ok: true, deletion: 'completed', deleted: { phase: 'deleted', previously: 'reclaimed' } });
    replies.completed_after_reclamation = reply;
  });
  it('a deletion that started and did not finish is ACCEPTED and pending: the relay never emits "refused" for it', async () => {
    const { reply } = await relay(answers.pending_started_not_finished!, 'REJECTED');
    expect(reply.state).toBe('ok');
    expect(reply.state).not.toBe('refused');
    expect(reply.problem).toBeNull();
    expect(reply.answer).toMatchObject({ ok: true, deletion: 'pending', deleted: { phase: 'deleting' } });
    replies.pending_started_not_finished = reply;
  });
  it('a refusal stays a refusal, with the panel’s own row', async () => {
    for (const [name, row] of [
      ['refused_already_deleted', 'mission_panel_delete_refused_already_deleted'],
      ['refused_busy', 'mission_panel_delete_refused_run_busy'],
      ['refused_status_stale', 'mission_panel_delete_refused_status_stale'],
    ] as const) {
      const { reply } = await relay(answers[name]!, 'COMPLETED');
      expect(reply.state, name).toBe('refused');
      expect(reply.problem, name).toBe(row);
      replies[name] = reply;
    }
  });
  it('target identity: a bare native id, a ref naming another Mission, or a malformed store is refused HERE and never sent', async () => {
    for (const [what, extra] of [
      ['no missionRef', { missionRef: undefined }],
      ['a ref naming another Mission', { missionRef: `mc:mn-${'1'.repeat(32)}` }],
      ['a malformed store identity', { storeIdentity: 'not-a-digest' }],
      ['no storeIdentity key', { storeIdentity: undefined }],
    ] as const) {
      const sent: DiMissionRequestReply[] = [];
      let called = 0;
      const relayer = new DiMissionRequests('/disc.json', {
        readDiscovery: () => ({ ok: true as const, endpoint: { port: 4321, token: 'relay-test-token' } }),
        request: async (): Promise<PanelResult> => {
          called += 1;
          return { kind: 'http', status: 200, body: { ok: true }, bodyProblem: null };
        },
        log: () => undefined,
      });
      const base: Record<string, unknown> = { type: 'diMissionRequest', seq: 9, op: 'deleteLocalData', missionId: MISSION, displayedState: 'COMPLETED', missionRef: REF, storeIdentity: STORE };
      const message: Record<string, unknown> = Object.assign(base, extra);
      for (const key of Object.keys(message)) if (message[key] === undefined) delete message[key];
      await relayer.handle(message, (m) => sent.push(m as unknown as DiMissionRequestReply));
      expect(sent[0]?.state, what).toBe('refused');
      expect(called, what).toBe(0);
    }
    // a null store (the catalog reported none) IS sent: the panel refuses it with its own reason
    const { reply } = await relay(answers.refused_source_mismatch!, 'COMPLETED');
    expect(reply.state).toBe('refused');
    expect(reply.problem).toBe('mission_panel_delete_refused_source_mismatch');
    replies.refused_source_mismatch = reply;
  });
  it('a write whose answer never came back (the panel timed out after it was sent) is UNKNOWN: never refused, never done', async () => {
    const { reply } = await relay({ kind: 'transport_error', problem: 'panel_timeout' }, 'COMPLETED');
    expect(reply.state).not.toBe('refused');
    expect(reply.state).not.toBe('ok');
    replies.unknown_after_send = reply;
  });
  it('exports the relayed replies for the webview (drift-checked)', () => {
    expect(Object.keys(replies).sort()).toEqual([...Object.keys(answers), 'unknown_after_send'].sort());
    const doc = {
      fixture: true,
      label:
        'FIXTURE (generated, not live): the replies the office relay (server/src/di/missionRequests.ts DiMissionRequests.handle) emits for the panel’s real ' +
        'delete answers (' + ANSWERS_REL + ')',
      generator: 'server/__tests__/diMissionPanelDeleteRelay.test.ts',
      replies: Object.fromEntries(Object.entries(replies).sort(([a], [b]) => a.localeCompare(b))),
    };
    const text = JSON.stringify(doc, null, 2) + '\n';
    const at = path.join(ROOT, REPLIES_REL);
    if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(at, text);
    expect(fs.readFileSync(at, 'utf-8')).toBe(text);
  });
});
