/**
 * STALE, NOT MERELY DORMANT. MOVED VERBATIM out of diMissionRequests.test.ts by the request-panel repair (2026-10-29), so that file runs
 * whole with no socket and no spawn.
 *
 * THE REPAIR DID NOT RUN THIS FILE. It holds:
 *  - "requestPanel: the client (in-process HTTP stand-in)", which opens a loopback server;
 *  - "REAL request panel adapter, through the real handler", which spawns python3 on tests/_panel_e2e_server.py and drives ACTUAL turn,
 *    submit, decide and run operations against a scripted bridge.
 * The repair CHANGES the handler (server/src/di/missionRequests.ts) that the second describe exercises. So THIS FILE'S VERDICT IS
 * UNESTABLISHED as of that change: an unrun test whose subject has just changed is stale, not merely dormant. The first describe's subject,
 * server/src/di/missionPanelClient.ts, is one the repair does not edit.
 *
 * A READING, NOT A VERDICT: its assertions target only states the repair does not move. Those are refused, for
 * local_request_reply_not_affirmative, mission_panel_display_stale, mission_panel_action_not_offered and
 * mission_panel_withdrawal_key_unavailable; ok; and unavailable, for panel_discovery_unavailable. Its last test's NAME says a stopped panel
 * is "not running", a liveness wording the repair removes from the reply text; the assertion does not test that wording.
 *
 * WHOEVER RUNS IT NEXT: a failure here may be caused by the repair, not pre-existing. Everything from the first describe to the end is
 * byte-identical to its original text. The imports and the one constant above it are the original lines these tests use. The original's
 * other imports and fixtures, used only by the tests left behind, are not copied, so the type check passes. The digests are in the
 * repair's record.
 */
import { type ChildProcessWithoutNullStreams, spawn } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import type { AddressInfo } from 'net';
import * as os from 'os';
import * as path from 'path';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.js';
import {
  MAX_PANEL_REQUEST_BODY_BYTES,
  MAX_PANEL_RESPONSE_BYTES,
  requestPanel,
} from '../src/di/missionPanelClient.js';
import { DiMissionRequests } from '../src/di/missionRequests.js';

const TOKEN = 'secret-bearer-token-do-not-leak';

describe('requestPanel: the client (in-process HTTP stand-in)', () => {
  let server: http.Server;
  let port = 0;
  let seen: Array<{ req: http.IncomingMessage; body: string }> = [];
  let respond: (req: http.IncomingMessage, res: http.ServerResponse) => void;
  beforeEach(async () => {
    seen = [];
    respond = (_req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{"ok":true}');
    };
    server = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c: Buffer) => chunks.push(c));
      req.on('end', () => {
        seen.push({ req, body: Buffer.concat(chunks).toString('utf-8') });
        respond(req, res);
      });
    });
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
    port = (server.address() as AddressInfo).port;
  });
  afterEach(async () => {
    await new Promise<void>((r) => server.close(() => r()));
  });

  it('a GET carries the bearer, a Host naming the same listener, no Origin, no cookie, no body', async () => {
    const result = await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' });
    expect(result).toMatchObject({ kind: 'http', status: 200, body: { ok: true } });
    const { req, body } = seen[0];
    expect(req.method).toBe('GET');
    expect(req.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(req.headers.host).toBe(`127.0.0.1:${port}`);
    expect(req.headers.origin).toBeUndefined();
    expect(req.headers.cookie).toBeUndefined();
    expect(req.headers['content-length']).toBeUndefined();
    expect(body).toBe('');
  });

  it('a POST sends exactly its JSON with an exact Content-Length', async () => {
    await requestPanel(
      { port, token: TOKEN },
      {
        method: 'POST',
        path: '/conversations/turn',
        body: { conversation_id: null, text: 'héllo 😀' },
      },
    );
    const { req, body } = seen[0];
    expect(req.headers['content-type']).toBe('application/json');
    expect(Number(req.headers['content-length'])).toBe(Buffer.byteLength(body));
    expect(JSON.parse(body)).toEqual({ conversation_id: null, text: 'héllo 😀' });
  });

  it('refuses locally, before any socket, what the adapter would refuse', async () => {
    const before = seen.length;
    const refusals = await Promise.all([
      requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/catalog' }),
      requestPanel({ port, token: TOKEN }, { method: 'POST', path: '/overview', body: {} }),
      requestPanel({ port, token: TOKEN }, { method: 'POST', path: '/conversations/turn' }),
      requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview', body: { a: 1 } }),
      requestPanel(
        { port, token: TOKEN },
        {
          method: 'POST',
          path: '/conversations/turn',
          body: { text: 'x'.repeat(MAX_PANEL_REQUEST_BODY_BYTES) },
        },
      ),
    ]);
    for (const r of refusals)
      expect(r).toEqual({ kind: 'transport_error', problem: 'panel_request_invalid' });
    expect(seen.length).toBe(before);
  });

  it('bounds the response it will read', async () => {
    respond = (_req, res) => {
      res.writeHead(200);
      res.end('x'.repeat(MAX_PANEL_RESPONSE_BYTES + 10));
    };
    expect(
      await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' }),
    ).toEqual({ kind: 'transport_error', problem: 'panel_response_too_large' });
  });

  it('a silent adapter is a timeout (outcome unknown); a closed port is unreachable (nothing sent)', async () => {
    respond = () => undefined;
    expect(
      await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' }, 150),
    ).toEqual({ kind: 'transport_error', problem: 'panel_timeout' });
    const closed = http.createServer();
    await new Promise<void>((r) => closed.listen(0, '127.0.0.1', () => r()));
    const dead = (closed.address() as AddressInfo).port;
    await new Promise<void>((r) => closed.close(() => r()));
    expect(
      await requestPanel({ port: dead, token: TOKEN }, { method: 'GET', path: '/overview' }),
    ).toEqual({ kind: 'transport_error', problem: 'panel_unreachable' });
  });

  it('a non-JSON body is reported as such, not parsed into something else', async () => {
    respond = (_req, res) => {
      res.writeHead(200);
      res.end('<html>');
    };
    expect(
      await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' }),
    ).toMatchObject({ kind: 'http', status: 200, bodyProblem: 'body_not_json' });
  });
});

// ── the REAL adapter ────────────────────────────────────────────────────────────────────────────

const REPO_ROOT = path.resolve(__dirname, '../../../..');
const HARNESS = path.join(REPO_ROOT, 'tests', '_panel_e2e_server.py');

class Harness {
  private child: ChildProcessWithoutNullStreams | null = null;
  private lines: string[] = [];
  private waiters: Array<(line: string) => void> = [];
  private buffer = '';
  readonly tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'di-e2e-'));
  readonly discovery = path.join(this.tmp, 'disc', 'panel.json');
  stderr = '';

  async start(): Promise<void> {
    this.child = spawn(
      'python3',
      [HARNESS, '--work-dir', path.join(this.tmp, 'w'), '--discovery-file', this.discovery],
      {
        env: { ...process.env, PYTHONPATH: REPO_ROOT, PYTHONDONTWRITEBYTECODE: '1' },
        stdio: ['pipe', 'pipe', 'pipe'],
      },
    );
    this.child.stdout.on('data', (d: Buffer) => {
      this.buffer += d.toString('utf-8');
      let i: number;
      while ((i = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, i);
        this.buffer = this.buffer.slice(i + 1);
        const waiter = this.waiters.shift();
        if (waiter) waiter(line);
        else this.lines.push(line);
      }
    });
    this.child.stderr.on('data', (d: Buffer) => {
      this.stderr += d.toString('utf-8');
    });
    const hello = JSON.parse(await this.line(30_000)) as { endpoint: string };
    if (hello.endpoint !== 'mission_panel_e2e')
      throw new Error(`unexpected startup: ${JSON.stringify(hello)}`);
  }

  private line(timeoutMs: number): Promise<string> {
    const queued = this.lines.shift();
    if (queued !== undefined) return Promise.resolve(queued);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () =>
          reject(
            new Error(`harness silent for ${timeoutMs} ms; stderr: ${this.stderr.slice(0, 1500)}`),
          ),
        timeoutMs,
      );
      this.waiters.push((l) => {
        clearTimeout(timer);
        resolve(l);
      });
    });
  }

  async control(command: Record<string, unknown>): Promise<Record<string, unknown>> {
    this.child?.stdin.write(`${JSON.stringify(command)}\n`);
    return JSON.parse(await this.line(30_000)) as Record<string, unknown>;
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
    if (child.exitCode === null) {
      try {
        child.stdin.write('{"op":"stop"}\n');
      } catch {
        /* already gone */
      }
      const timer = setTimeout(() => child.kill('SIGTERM'), 10_000);
      await exited;
      clearTimeout(timer);
    }
    this.child = null;
  }

  token(): string {
    return (JSON.parse(fs.readFileSync(this.discovery, 'utf-8')) as { token: string }).token;
  }
}

describe('REAL request panel adapter, through the real handler', { timeout: 90_000 }, () => {
  let harness: Harness;
  let panel: DiMissionRequests;
  let seq = 0;
  const logs: string[] = [];
  const replies: DiMissionRequestReply[] = [];

  const ask = async (fields: Record<string, unknown>): Promise<DiMissionRequestReply> => {
    let got: DiMissionRequestReply | null = null;
    await panel.handle({ type: 'diMissionRequest', seq: ++seq, ...fields }, (m) => {
      got = m as unknown as DiMissionRequestReply;
    });
    if (got === null) throw new Error('no reply');
    const reply: DiMissionRequestReply = got;
    expect(reply.seq).toBe(seq);
    replies.push(reply);
    return reply;
  };
  type Json = Record<string, any>;
  const answer = (r: DiMissionRequestReply): Json => (r.answer ?? {}) as Json;

  const settled = async (conversationId: string): Promise<Json> => {
    for (let i = 0; i < 120; i++) {
      const view = answer(await ask({ op: 'conversation', conversationId }));
      if (view.turns.at(-1).state !== 'RESERVED') return view.turns.at(-1);
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error('the conversation turn never settled');
  };

  const PROPOSAL = {
    objective: 'Make the readiness probe stable under load',
    target_context: 'the readiness subsystem',
    requested_scope: 'the probe and its tests; no schema change',
    requested_action_scope: ['repository_read', 'engineering_change'],
  };
  const reply = (text: string): string =>
    `${text}\n\n\`\`\`mission-proposal\n${JSON.stringify(PROPOSAL)}\n\`\`\`\n`;

  beforeAll(async () => {
    harness = new Harness();
    await harness.start();
    panel = new DiMissionRequests(harness.discovery, { log: (l) => logs.push(l) });
  }, 60_000);
  afterAll(async () => {
    await harness.stop();
    // Only the directory this harness made, by its exact path, directly under the temp root.
    if (path.dirname(harness.tmp) === os.tmpdir() && /^di-e2e-[A-Za-z0-9]{6}$/.test(path.basename(harness.tmp))) {
      fs.rmSync(harness.tmp, { recursive: true, force: true });
    }
  });

  it('walks request -> clarification -> exact proposal -> decision -> dispatch -> verified result', async () => {
    expect(answer(await ask({ op: 'refresh' })).requests).toEqual([]);
    await harness.control({ op: 'provider', reply: 'Which probe do you mean?' });
    const first = answer(await ask({ op: 'turn', text: 'make the probe stable' })).turn as Json;
    const clarifying = await settled(first.conversation_id);
    expect(clarifying).toMatchObject({
      state: 'COMPLETED',
      draft: null,
      provider_text: 'untrusted',
    });
    await harness.control({ op: 'provider', reply: reply('The readiness probe.') });
    const second = answer(
      await ask({
        op: 'turn',
        conversationId: first.conversation_id,
        text: 'the readiness probe, no schema change',
      }),
    ).turn as Json;
    const drafted = await settled(first.conversation_id);
    expect(second.sequence).toBe(2);
    expect(drafted.draft.proposal.requested_action_scope).toEqual([
      'engineering_change',
      'repository_read',
    ]);
    expect((await harness.control({ op: 'state' })).requests).toBe(0); // a draft is not of record

    const submitted = answer(
      await ask({
        op: 'submit',
        turnId: drafted.turn_id,
        digest: drafted.draft.proposal_digest_sha256,
      }),
    );
    const view = submitted.request as Json;
    expect(view.lifecycle.stage).toBe('of_record');
    expect(view.proposal_digest_sha256).toBe(drafted.draft.proposal_digest_sha256);
    const ref = view.request_ref as string;

    const offer = answer(await ask({ op: 'offer', requestRef: ref }));
    expect(offer).toMatchObject({ reply: 'approve', revision: 1, mission_id: view.mission_id });
    // A second window is shown the SAME frozen offer, with the same absolute expiry.
    const sameOffer = answer(await ask({ op: 'offer', requestRef: ref }));
    expect(sameOffer.offer_digest_sha256).toBe(offer.offer_digest_sha256);
    expect(sameOffer.expires_at).toBe(offer.expires_at);
    const bind = {
      requestRef: ref,
      missionId: offer.mission_id,
      revision: offer.revision,
      digest: offer.proposal_digest_sha256,
      offerDigest: offer.offer_digest_sha256,
      expiresAt: offer.expires_at,
      scope: offer.approved_action_scope,
      deliveryTargets: offer.approved_delivery_targets,
      repositoryUrl: offer.repository_url,
    };

    const notYet = await ask({ op: 'decide', ...bind, reply: 'yes, go ahead' });
    expect(notYet).toMatchObject({
      state: 'refused',
      problem: 'local_request_reply_not_affirmative',
    });
    expect((await harness.control({ op: 'state' })).authorizations).toBe(0);
    const stale = await ask({ op: 'decide', ...bind, digest: '0'.repeat(64), reply: 'approve' });
    expect(stale).toMatchObject({ state: 'refused', problem: 'mission_panel_display_stale' });
    expect(answer(stale).details).toMatchObject({
      display_invalid: true,
      binding: 'offer_tampered',
    });
    // The same offer with a different expiry is tampering too: the retained offer is the truth.
    const moved = await ask({
      op: 'decide',
      ...bind,
      expiresAt: (offer.expires_at as number) + 600,
      reply: 'approve',
    });
    expect(answer(moved).details).toMatchObject({ binding: 'offer_tampered' });

    const approved = answer(await ask({ op: 'decide', ...bind, reply: 'approve' }));
    expect(approved.request.lifecycle).toMatchObject({
      stage: 'approved',
      approval_expires_at: offer.expires_at,
    });
    expect(approved.request.lifecycle.provenance.proof).toBe(
      'operator_attested_not_independently_verified',
    );
    const again = answer(await ask({ op: 'decide', ...bind, reply: 'approve' }));
    expect(again.decision.idempotent).toBe(true);
    expect(await harness.control({ op: 'state' })).toMatchObject({
      authorizations: 1,
      spawn_calls: 0,
    });

    const started = answer(await ask({ op: 'run', requestRef: ref, command: 'dispatch' }));
    expect(started.request.lifecycle.stage).toBe('dispatched');
    expect(started.request.lifecycle.detail).toMatch(/exactly once/);
    const dup = await ask({ op: 'run', requestRef: ref, command: 'dispatch' });
    expect(dup).toMatchObject({ state: 'refused', problem: 'mission_panel_action_not_offered' });
    await harness.control({ op: 'team', status: 'RUNNING' });
    expect(
      answer(await ask({ op: 'run', requestRef: ref, command: 'observe' })).request.lifecycle.stage,
    ).toBe('running');
    const early = await ask({ op: 'run', requestRef: ref, command: 'verify' });
    expect(early).toMatchObject({ state: 'refused', problem: 'mission_panel_action_not_offered' });
    await harness.control({ op: 'artifacts', request_ref: ref });
    await harness.control({ op: 'team', status: 'COMPLETE' });
    const done = answer(await ask({ op: 'run', requestRef: ref, command: 'verify' }));
    expect(done.request.lifecycle.stage).toBe('verified');
    const result = answer(await ask({ op: 'result', requestRef: ref }));
    expect(result).toMatchObject({
      recoverable: true,
      delivered: false,
      delivery_authority: 'none',
    });
    expect(result.result_text).toContain('readiness probe fix landed');
    expect(await harness.control({ op: 'state' })).toMatchObject({
      spawn_calls: 1,
      authorizations: 1,
      requests: 1,
    });
  });

  it('a withdrawal is a withdrawal, and a restart loses the key honestly', async () => {
    await harness.control({ op: 'provider', reply: reply('Proposal A.') });
    const t = answer(await ask({ op: 'turn', text: 'another change' })).turn as Json;
    const d = await settled(t.conversation_id);
    const v = answer(
      await ask({ op: 'submit', turnId: d.turn_id, digest: d.draft.proposal_digest_sha256 }),
    ).request as Json;
    const bind = {
      requestRef: v.request_ref,
      missionId: v.mission_id,
      revision: v.revision,
      digest: v.proposal_digest_sha256,
    };
    await harness.control({ op: 'restart' });
    const noKey = await ask({ op: 'withdraw', ...bind });
    expect(noKey).toMatchObject({
      state: 'refused',
      problem: 'mission_panel_withdrawal_key_unavailable',
    });
    expect(answer(await ask({ op: 'request', requestRef: v.request_ref })).lifecycle.stage).toBe(
      'of_record',
    );
  });

  it('a withdrawn proposal is closed and is called withdrawn, never denied', async () => {
    await harness.control({ op: 'provider', reply: reply('Proposal B.') });
    const t = answer(await ask({ op: 'turn', text: 'a third change' })).turn as Json;
    const d = await settled(t.conversation_id);
    const v = answer(
      await ask({ op: 'submit', turnId: d.turn_id, digest: d.draft.proposal_digest_sha256 }),
    ).request as Json;
    const out = answer(
      await ask({
        op: 'withdraw',
        requestRef: v.request_ref,
        missionId: v.mission_id,
        revision: v.revision,
        digest: v.proposal_digest_sha256,
      }),
    );
    expect(out.request.lifecycle.stage).toBe('withdrawn');
    expect(JSON.stringify(out.request.lifecycle)).not.toMatch(/den(y|ied|ial)/i);
    const stalePost = await ask({
      op: 'decide',
      requestRef: v.request_ref,
      missionId: v.mission_id,
      revision: v.revision,
      digest: v.proposal_digest_sha256,
      reply: 'approve',
      offerDigest: '8'.repeat(64),
      expiresAt: 4_000_000_000,
      scope: ['engineering_change', 'repository_read'],
      deliveryTargets: [],
      repositoryUrl: 'https://github.com/Example/Repo',
    });
    // No offer was ever issued for this digest: refused as a stale display, before any authority.
    expect(stalePost).toMatchObject({ state: 'refused', problem: 'mission_panel_display_stale' });
    expect(answer(stalePost).details).toMatchObject({ binding: 'offer_unknown' });
  });

  it('an unreadable provider turn is shown as uncertain and is never retried', async () => {
    const before = (await harness.control({ op: 'state' })).codex_calls as number;
    await harness.control({ op: 'provider', fail: 'garbage' });
    const t = answer(await ask({ op: 'turn', text: 'this will be unreadable' })).turn as Json;
    const settledTurn = await settled(t.conversation_id);
    expect(settledTurn.state).toBe('UNCERTAIN');
    expect(settledTurn.assistant_text).toBeNull();
    expect((await harness.control({ op: 'state' })).codex_calls).toBe(before + 1);
  });

  it('the worst conversation the panel accepts crosses the relay inside the bound, and says what it left out', async () => {
    // Round 27 F2. Fifty turns, each with a user text and an assistant text at the accepted maximum
    // (8000 characters), seeded through the REAL journal's own validation. Astral characters are four bytes
    // raw and twelve ASCII-escaped; control characters are six once JSON-escaped. A character count cannot
    // tell these apart: only the serialized BYTES that cross the relay can.
    const endpoint = JSON.parse(fs.readFileSync(harness.discovery, 'utf-8')) as { port: number };
    const rawBytes = (conversationId: string): Promise<{ declared: number; actual: number }> =>
      new Promise((resolve, reject) => {
        http
          .get(
            {
              host: '127.0.0.1',
              port: endpoint.port,
              path: `/conversations/${conversationId}`,
              headers: {
                Authorization: `Bearer ${harness.token()}`,
                Host: `127.0.0.1:${endpoint.port}`,
              },
            },
            (res) => {
              const chunks: Buffer[] = [];
              res.on('data', (c: Buffer) => chunks.push(c));
              res.on('end', () =>
                resolve({
                  declared: Number(res.headers['content-length']),
                  actual: Buffer.concat(chunks).length,
                }),
              );
            },
          )
          .on('error', reject);
      });
    for (const character of ['astral', 'control'] as const) {
      const seeded = await harness.control({ op: 'seed_turns', turns: 50, character });
      const conversationId = seeded.conversation_id as string;
      // 1. The raw bytes the adapter really sends.
      const raw = await rawBytes(conversationId);
      expect(raw.declared, character).toBe(raw.actual);
      expect(raw.actual, character).toBeLessThanOrEqual(MAX_PANEL_RESPONSE_BYTES);
      expect(raw.actual, `${character}: a worst case, not a toy`).toBeGreaterThan(1024 * 1024);
      // 2. Through the real relay: readable, typed, and honest about the cut.
      const reply = await ask({ op: 'conversation', conversationId });
      expect(reply.state, character).toBe('ok');
      const view = answer(reply);
      expect(view.turns_total).toBe(50);
      expect(view.turns_shown + view.turns_omitted).toBe(50);
      expect(view.turns_omitted, `${character}: 50 maximal turns cannot all fit`).toBeGreaterThan(0);
      expect(view.turns_truncated).toBe(true);
      // WHICH bound cut, with real counts: here every omitted turn was left out by the byte budget.
      expect(view.turns_omitted_by_byte_limit).toBe(view.turns_omitted);
      expect(view.turns_omitted_by_turn_limit).toBe(0);
      console.info(
        `MEASURED ${character} x50 through the relay: raw_response_bytes=${raw.actual} ` +
          `content_length=${raw.declared} bound=${MAX_PANEL_RESPONSE_BYTES} shown=${view.turns_shown} ` +
          `omitted=${view.turns_omitted} (by bytes=${view.turns_omitted_by_byte_limit}, by turn bound=${view.turns_omitted_by_turn_limit})`,
      );
      expect(view.turns).toHaveLength(view.turns_shown);
      // The newest turn is always shown, whole: nothing inside a turn is cut.
      const newest = view.turns.at(-1);
      expect(newest.sequence).toBe(50);
      expect([...newest.user_text]).toHaveLength(8000);
      expect([...newest.assistant_text]).toHaveLength(8000);
    }
  });

  it('never exposes the bearer token to a client, in any reply or any log line', async () => {
    const token = harness.token();
    const everything = JSON.stringify(replies) + logs.join('\n');
    expect(everything).not.toContain(token);
    expect(everything).not.toContain('Bearer');
    expect(everything).not.toMatch(/lc-[0-9a-f]{64}/); // a withdrawal key
    expect(everything).not.toContain(harness.tmp); // no private path
  });

  it('after the panel stops it is "not running", with nothing sent', async () => {
    await harness.stop();
    expect(fs.existsSync(harness.discovery)).toBe(false);
    const gone = await ask({ op: 'refresh' });
    expect(gone).toMatchObject({ state: 'unavailable', problem: 'panel_discovery_unavailable' });
  });
});
