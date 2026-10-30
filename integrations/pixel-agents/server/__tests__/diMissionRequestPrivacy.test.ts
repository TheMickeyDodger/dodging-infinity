/**
 * The Mission request panel's RESPONSE BOUNDARY, for the refusals that name locations on this machine.
 *
 * The defect this pins: when the MissionBridge refuses to prepare a workspace (the workspace guard among them), the adapter's `reason` for that refusal NAMES ABSOLUTE PATHS (the workspaces root, the
 * repository, a worktree, a git error). The office relayed that reason verbatim, so a private filesystem path crossed to the client and was drawn in its refusal notice. (The real-adapter walk in
 * diMissionRequests.test.ts asserts that no reply or log line carries the harness's temp path; it failed because the path WAS returned.)
 *
 * The fix does not remove a path from prose afterwards (that needs a parser for arbitrary text, and a path with a space defeats a pattern). It keeps the producer's prose OUT: for the producer's own
 * closed list of workspace-preparation refusals (target_runtime/mission_bridge.py DURABLE_WORKSPACE_PROBLEMS; the Python test tests/test_mission_panel_refusal_parity.py pins this table to that list),
 * the reply is stated from the refusal's CODE. Preserved and asserted below: the code, the state, the request reference and the recorded flag. Not covered, and said so: the prose of any refusal
 * OUTSIDE that list is relayed as before, and nothing here examines it for paths.
 *
 * Everything below is SYNTHETIC and IN-PIN: made-up absolute paths (several containing SPACES), no real runtime, no filesystem, no outside-repository fixture.
 */
import { describe, expect, it } from 'vitest';

import type { PanelResult } from '../src/di/missionPanelClient.js';
import {
  DiMissionRequests,
  parseMissionRequest,
  replyFromPanel,
  WORKSPACE_REFUSAL_SUFFIX,
  WORKSPACE_REFUSAL_WORDS,
} from '../src/di/missionRequests.js';

/** Paths WITH SPACES, as ordinary on this platform as "My Drive" or "Application Support". */
const REPOSITORY = '/Users/some one/My Documents/dodging infinity';
const WORKSPACES = `${REPOSITORY}/.herd/state/evidence/x/tmp dir/di-e2e-AbC123/w/workspaces`;
const FRAGMENTS = ['/Users', 'some one', 'My Documents', 'dodging infinity', 'tmp dir', 'di-e2e-AbC123', 'Documents', 'workspaces/'];
const REF = `lr-${'a'.repeat(32)}`;

/** The guard's own wording for this refusal (target_runtime/mission_workspace.py), with the synthetic paths. */
const GUARD_REASON =
  `the workspaces root ${WORKSPACES} is inside the repository at ${REPOSITORY}; ` +
  'Mission worktrees live outside every repository, and nothing was bound or created';
const CODE = 'mission_bridge_workspace_root_unavailable';
const CODES = Object.keys(WORKSPACE_REFUSAL_WORDS);

const run = parseMissionRequest({ type: 'diMissionRequest', seq: 16, op: 'run', requestRef: REF, command: 'dispatch' });
if (!run.ok) throw new Error('fixture');
const http200 = (body: unknown): PanelResult => ({ kind: 'http', status: 200, body, bodyProblem: null });
const refusal = (problem: string, reason: string, details: Record<string, unknown> = { refusal_recorded: true, request_ref: REF }) =>
  http200({ ok: false, problem, reason, details });
const leaks = (text: string): string[] => FRAGMENTS.filter((fragment) => text.includes(fragment));

describe('the table of code-based wording', () => {
  it('has a sentence for each of the twelve workspace-preparation refusals, none of which names a location', () => {
    expect(CODES).toHaveLength(12);
    for (const [code, words] of Object.entries(WORKSPACE_REFUSAL_WORDS)) {
      expect(code, code).toMatch(/^mission_bridge_[a-z_]+$/);
      expect(words.length, code).toBeGreaterThan(40);
      expect(words, code).not.toMatch(/(^|[\s"'(=<])\/[^\s/]+\/|[A-Za-z]:\\|~\/|file:\/\//);
      expect(words, code).not.toContain('\n');
    }
    expect(WORKSPACE_REFUSAL_SUFFIX).toContain('not shown here');
  });
});

describe('replyFromPanel: a workspace-preparation refusal is stated from its code, never from the producer\'s prose', () => {
  it('the workspace guard\'s refusal, with spaces in both paths: the code and state are kept; nothing of either path survives', () => {
    const reply = replyFromPanel(run, refusal(CODE, GUARD_REASON));
    expect(reply).toMatchObject({ type: 'diMissionRequestReply', seq: 16, op: 'run', state: 'refused', problem: CODE });
    expect(reply.reason).toBe(`${WORKSPACE_REFUSAL_WORDS[CODE]} ${WORKSPACE_REFUSAL_SUFFIX}`);
    expect(leaks(JSON.stringify(reply))).toEqual([]);
  });

  it('every one of the twelve codes, each given prose full of spaced paths, a git error and a path in its details: none of it crosses', () => {
    for (const code of CODES) {
      const prose = `the worktree at ${WORKSPACES}/m-1 belongs to the repository at ${REPOSITORY}, not the one configured (fatal: not a git repository: '${REPOSITORY}/.git')`;
      const reply = replyFromPanel(
        run,
        refusal(code, prose, { refusal_recorded: false, request_ref: REF, workspace: WORKSPACES, nested: { path: REPOSITORY }, note: prose }),
      );
      expect(reply, code).toMatchObject({ state: 'refused', problem: code, reason: `${WORKSPACE_REFUSAL_WORDS[code]} ${WORKSPACE_REFUSAL_SUFFIX}` });
      expect(leaks(JSON.stringify(reply)), code).toEqual([]);
      // what the person and the page may use of the details is kept: whether the refusal was recorded, and which request it is
      expect(reply.answer, code).toEqual({ details: { refusal_recorded: false, request_ref: REF } });
    }
  });

  it('a refusal with no usable details still states the same words and carries an empty details object', () => {
    const reply = replyFromPanel(run, http200({ ok: false, problem: CODE, reason: GUARD_REASON }));
    expect(reply.reason).toBe(`${WORKSPACE_REFUSAL_WORDS[CODE]} ${WORKSPACE_REFUSAL_SUFFIX}`);
    expect(reply.answer).toEqual({ details: {} });
    const odd = replyFromPanel(run, http200({ ok: false, problem: CODE, reason: GUARD_REASON, details: { refusal_recorded: 'yes', request_ref: WORKSPACES } }));
    expect(odd.answer).toEqual({ details: {} });
  });

  it('a refusal OUTSIDE that list is relayed exactly as before (its prose and its details); nothing here claims it holds no path', () => {
    const reply = replyFromPanel(run, refusal('mission_panel_display_stale', 'not the one Mission Core holds', { binding: 'stale_revision' }));
    expect(reply).toMatchObject({
      state: 'refused',
      problem: 'mission_panel_display_stale',
      reason: 'not the one Mission Core holds',
      answer: { details: { binding: 'stale_revision' } },
    });
    expect(replyFromPanel(run, http200({ ok: true, request: { stage: 'x' } }))).toMatchObject({ state: 'ok', reason: null, answer: { ok: true, request: { stage: 'x' } } });
  });

  it('a code that merely LOOKS like a built-in property name is not looked up as one', () => {
    for (const problem of ['constructor', 'toString', 'hasownproperty', 'proto']) {
      const reply = replyFromPanel(run, refusal(problem, 'its own words'));
      expect(reply, problem).toMatchObject({ state: 'refused', reason: 'its own words' });
    }
  });
});

describe('through the real handler (the path a reply really takes): nothing a client is sent, and nothing logged, carries the path', () => {
  async function ask(problem: string, reason: string, details?: Record<string, unknown>) {
    const logs: string[] = [];
    const sent: Record<string, unknown>[] = [];
    const panel = new DiMissionRequests('/discovery.json', {
      readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 'secret-bearer-token-do-not-leak' } }),
      request: async () => refusal(problem, reason, details),
      log: (line) => logs.push(line),
    });
    await panel.handle({ type: 'diMissionRequest', seq: 16, op: 'run', requestRef: REF, command: 'dispatch' }, (m) => sent.push(m));
    return { sent, logs };
  }

  it('a refused start whose paths contain spaces: the reply keeps its code and states the refusal; neither the reply nor any log line holds any part of either path', async () => {
    const { sent, logs } = await ask(CODE, GUARD_REASON);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ state: 'refused', problem: CODE, op: 'run', reason: `${WORKSPACE_REFUSAL_WORDS[CODE]} ${WORKSPACE_REFUSAL_SUFFIX}` });
    const everything = JSON.stringify(sent) + logs.join('\n');
    expect(leaks(everything)).toEqual([]);
    expect(everything).not.toContain('secret-bearer-token-do-not-leak');
    expect(logs.join('\n')).toContain(CODE); // the log still names the refusal, by code
  });

  it('the same for every code of the list, so the table and the handler cannot disagree', async () => {
    for (const code of CODES) {
      const { sent, logs } = await ask(code, `at ${WORKSPACES} and ${REPOSITORY}`, { refusal_recorded: true, request_ref: REF, where: WORKSPACES });
      expect(sent[0], code).toMatchObject({ state: 'refused', problem: code });
      expect(leaks(JSON.stringify(sent) + logs.join('\n')), code).toEqual([]);
    }
  });
});
