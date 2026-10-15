/**
 * DI BOOTSTRAP RECOVERY + CANONICAL LAYOUT-READY FLUSH — the smallest isolated browser case, on
 * the PRODUCTION BUILD (dist/di-cli.js + dist/webview) against the labelled FIXTURE backend
 * stand-in (FIXTURE evidence, never live). It STARTS at the initial-handshake cut: no tour.
 *
 * The page's /ws runs through a proxy that keeps a LEDGER of every server -> page frame (its
 * received representation, type, present character ids, and what the proxy did). Withholding is
 * ASSERTED from that ledger, never assumed; while any withhold or hold is active, a frame whose
 * type cannot be determined is withheld (fail closed).
 */
import { type ChildProcess, spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';

import { expect, type Page, test, type WebSocketRoute } from '@playwright/test';

import { buildRealisticFixture } from './realisticFixture';

const REPO_ROOT = path.join(__dirname, '../..');
const DI_BUNDLE = path.join(REPO_ROOT, 'dist', 'di-cli.js');
const BACKEND_TOKEN = 'fixture-backend-token-e2e-0123456789';

interface CharacterSnapshot {
  id: number;
  state: string;
  matrixEffect: string | null;
  isGreeter?: boolean;
}
interface HooksWindow {
  __pixelAgentsTestHooks?: {
    getCharacters?: () => CharacterSnapshot[];
    getDiRecorded?: () => Array<[number, string]>;
    /** The last office state the APPLICATION HANDLER received (e2e-only accessor). */
    getDiStored?: () => unknown;
    /** Every message the APPLICATION HANDLER received (e2e-only). */
    messageLog?: Array<{ type: string; id?: number }>;
    editorTileAction?: (c: number, r: number) => void;
  };
  __PIXEL_AGENTS_E2E?: boolean;
}
interface LedgerEntry {
  conn: number;
  type: string | null;
  repr: string;
  action: string;
  ids?: number[];
  agents?: number[];
}

const shotDir = process.env['DI_SHOT_DIR'];

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function shot(page: Page, name: string): Promise<void> {
  if (!shotDir) throw new Error('DI_SHOT_DIR must be set');
  // DI_SHOT_SUFFIX: a later run never reuses an earlier run's artifact names.
  const suffix = process.env['DI_SHOT_SUFFIX'] ?? '';
  await page.screenshot({
    path: path.join(shotDir, name.replace(/\.png$/, `${suffix}.png`)),
    fullPage: true,
  });
}

/** Present character ids of an office-state object (sorted). */
function presentIds(state: unknown): number[] {
  const missions =
    (
      state as {
        missions?: {
          missions?: Array<{ members?: { members?: Array<{ characterId: unknown }> } }>;
        };
      }
    )?.missions?.missions ?? [];
  return missions
    .flatMap((m) => m.members?.members ?? [])
    .map((x) => x.characterId)
    .filter((id): id is number => typeof id === 'number')
    .sort((a, b) => a - b);
}

/** What a frame is, whatever its representation: type (null = undeterminable) + present ids. */
function frameInfo(message: string | Buffer): {
  type: string | null;
  ids?: number[];
  agents?: number[];
} {
  try {
    const text = typeof message === 'string' ? message : Buffer.from(message).toString('utf-8');
    const parsed = JSON.parse(text) as { type?: unknown; agents?: unknown };
    const type = typeof parsed.type === 'string' ? parsed.type : null;
    if (type === 'diOfficeState') return { type, ids: presentIds(parsed) };
    if (type === 'existingAgents' && Array.isArray(parsed.agents))
      return { type, agents: parsed.agents as number[] };
    return { type };
  } catch {
    return { type: null };
  }
}

function frameRepr(message: string | Buffer): string {
  return typeof message === 'string'
    ? 'string'
    : `${typeof message}:${(message as object)?.constructor?.name ?? '?'}`;
}

test('DI bootstrap recovery from the initial-handshake cut + canonical layout-ready flush (isolated)', async ({
  page,
}) => {
  const fixture = buildRealisticFixture();
  const sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-boot-')));
  const fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);

  let roster: 'base' | 'changed' = 'base';
  let backendPort = 0;
  const backend = http.createServer((req, res) => {
    const authorized =
      req.headers.authorization === `Bearer ${BACKEND_TOKEN}` &&
      req.headers.host === `127.0.0.1:${backendPort}`;
    if (!authorized) {
      res.writeHead(401);
      res.end();
      return;
    }
    res.writeHead(200, {
      'content-type': 'application/json; charset=utf-8',
      'x-di-freshness-health': 'fresh',
    });
    res.end(
      JSON.stringify(
        fixture.envelopeAt(Math.floor(Date.now() / 1000), undefined, roster, 'available'),
      ),
    );
  });
  await new Promise<void>((r) => backend.listen(0, '127.0.0.1', () => r()));
  backendPort = (backend.address() as AddressInfo).port;
  const runtimeDir = path.join(sandbox, 'runtime');
  fs.mkdirSync(runtimeDir, { mode: 0o700 });
  const discovery = path.join(runtimeDir, 'backend-discovery.json');
  fs.writeFileSync(
    discovery,
    JSON.stringify({
      url: `http://127.0.0.1:${backendPort}`,
      port: backendPort,
      token: BACKEND_TOKEN,
    }),
    { mode: 0o600 },
  );

  let output = '';
  const child: ChildProcess = spawn(
    process.execPath,
    [DI_BUNDLE, '--state-dir', path.join(sandbox, 'di-state'), '--backend-discovery', discovery],
    {
      env: { ...process.env, HOME: fakeHome, USERPROFILE: fakeHome, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stdout?.on('data', (c: Buffer) => (output += c.toString()));
  child.stderr?.on('data', (c: Buffer) => (output += c.toString()));

  try {
    let accessFile = '';
    await expect
      .poll(
        () =>
          (accessFile = /Open the URL stored in (\S+)/.exec(output)?.[1] ?? '') &&
          fs.existsSync(accessFile),
        { timeout: 20_000 },
      )
      .toBe(true);
    const access = JSON.parse(fs.readFileSync(accessFile, 'utf-8')) as {
      url: string;
      token: string;
    };
    // The office must have the backend's state before the page connects (a LIVE first snapshot).
    await expect.poll(() => /office: LIVE/.test(output), { timeout: 20_000 }).toBe(true);

    // The e2e flag ONLY (enables the application's own test hooks). No WebSocket spy: observations
    // come from the APPLICATION HANDLER (messageLog, getDiStored, getDiRecorded) and the ledger.
    await page.addInitScript(() => {
      (window as unknown as HooksWindow).__PIXEL_AGENTS_E2E = true;
    });

    // ── the proxy ──
    let refuseConnections = false;
    let forwardToPage = true;
    let cutAfterType: string | null = null;
    /** Pass exactly ONE office message: the first on a connection numbered above this. */
    let passOneOfficeAbove: number | null = null;
    const withheldTypes = new Set<string>();
    const heldClientTypes = new Set<string>();
    const heldClient: Array<{ server: WebSocketRoute; message: string | Buffer }> = [];
    const sockets: Array<{ page: WebSocketRoute; server: WebSocketRoute }> = [];
    let connectionCount = 0;
    const clientSent: Array<{ conn: number; type: string | null; repr: string; action: string }> =
      [];
    const ledger: LedgerEntry[] = [];
    await page.routeWebSocket(/\/ws\?token=/, (ws) => {
      if (refuseConnections) {
        void ws.close({ code: 1001, reason: 'FIXTURE: server unreachable' });
        return;
      }
      const server = ws.connectToServer();
      const conn = ++connectionCount;
      ws.onMessage((message) => {
        const { type } = frameInfo(message);
        const repr = frameRepr(message);
        if (heldClientTypes.size > 0 && (type === null || heldClientTypes.has(type))) {
          clientSent.push({ conn, type, repr, action: 'held' });
          heldClient.push({ server, message });
          return;
        }
        clientSent.push({ conn, type, repr, action: 'forwarded' });
        server.send(message);
      });
      server.onMessage((message) => {
        const { type, ids, agents } = frameInfo(message);
        const repr = frameRepr(message);
        const record = (action: string): void => {
          ledger.push({
            conn,
            type,
            repr,
            action,
            ...(ids ? { ids } : {}),
            ...(agents ? { agents } : {}),
          });
        };
        if (!forwardToPage) return record('dropped');
        if (withheldTypes.size > 0 && (type === null || withheldTypes.has(type))) {
          const passOne =
            type === 'diOfficeState' && passOneOfficeAbove !== null && conn > passOneOfficeAbove;
          if (!passOne) return record(type === null ? 'withheld-undeterminable' : 'withheld');
          passOneOfficeAbove = null;
          record('forwarded-pass-one');
          ws.send(message);
          return;
        }
        record('forwarded');
        ws.send(message);
        if (cutAfterType !== null && type === cutAfterType) {
          cutAfterType = null;
          forwardToPage = false;
          refuseConnections = true;
          void ws.close({ code: 1001, reason: `FIXTURE: lost right after ${type}` });
          void server.close();
        }
      });
      sockets.push({ page: ws, server });
    });
    const closeSockets = async (reason: string): Promise<void> => {
      for (const socket of sockets.splice(0)) {
        try {
          await socket.page.close({ code: 1001, reason });
          await socket.server.close();
        } catch {
          // already closed
        }
      }
    };
    /** Messages of this type the APPLICATION HANDLER received. */
    const pageCount = (type: string): Promise<number> =>
      page.evaluate(
        (t) =>
          ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.messageLog ?? []).filter(
            (m) => m.type === t,
          ).length,
        type,
      );
    /** The office state the APPLICATION HANDLER stored last. */
    const storedState = (): Promise<unknown> =>
      page.evaluate(
        () => (window as unknown as HooksWindow).__pixelAgentsTestHooks?.getDiStored?.() ?? null,
      );
    const drawnIds = async (): Promise<number[]> =>
      (
        await page.evaluate(() =>
          (
            (window as unknown as HooksWindow).__pixelAgentsTestHooks?.getCharacters?.() ?? []
          ).filter((c) => !c.isGreeter),
        )
      )
        .map((c) => c.id)
        .sort((a, b) => a - b);
    const typingCount = async (): Promise<number> =>
      (
        await page.evaluate(() =>
          (
            (window as unknown as HooksWindow).__pixelAgentsTestHooks?.getCharacters?.() ?? []
          ).filter((c) => !c.isGreeter),
        )
      ).filter((c) => c.state === 'type' && !c.matrixEffect).length;
    const ledgerDump = (from = 0): string => JSON.stringify(ledger.slice(from));
    const unready = page.getByTestId('di-canvas-unready');
    const panel = page.getByTestId('di-office-panel');

    // ── 1. INITIAL-HANDSHAKE CUT: the FIRST connection is cut after webviewReady was sent and
    // existingAgents arrived, before layoutLoaded.
    cutAfterType = 'existingAgents';
    try {
      await page.goto(access.url);
    } catch (err) {
      throw new Error(`navigation failed: ${String(err).split(access.token).join('<REDACTED>')}`);
    }
    await expect.poll(() => cutAfterType, { timeout: 20_000 }).toBeNull();
    expect(connectionCount).toBe(1);
    expect(clientSent.some((m) => m.conn === 1 && m.type === 'webviewReady')).toBe(true);
    expect(ledger.find((e) => e.conn === 1)?.type, 'first frame on the first connection').toBe(
      'diOfficeState',
    );
    // PRESENCE IS NOT DELIVERY: a post-cut layoutLoaded is RECORDED (dropped). Assert on the
    // ACTION — neither forwarding action ('forwarded', 'forwarded-pass-one') — and on the PAGE.
    const conn1Layout = ledger.filter((e) => e.conn === 1 && e.type === 'layoutLoaded');
    console.log(`[conn-1 layoutLoaded records] ${JSON.stringify(conn1Layout)}`);
    expect(
      conn1Layout.filter((e) => e.action.startsWith('forwarded')),
      `no layoutLoaded FORWARDED on the cut connection ${JSON.stringify(conn1Layout)}`,
    ).toEqual([]);
    expect(await pageCount('layoutLoaded'), 'the page received no layoutLoaded').toBe(0);
    await expect(unready).toContainText('office canvas NOT READY, awaiting layout', {
      timeout: 10_000,
    });
    await expect(unready).toContainText('NOT LIVE', { timeout: 15_000 });
    await expect(panel).toHaveCount(0);
    await shot(page, 'R1-initial-handshake-cut-not-live-unready-FIXTURE.png');

    // ── 2. Reconnect with layoutLoaded WITHHELD: fresh data, canvas unready; the bootstrap is
    // requested on the new connection because the layout is unready.
    withheldTypes.add('layoutLoaded');
    refuseConnections = false;
    forwardToPage = true;
    await expect
      .poll(() => clientSent.some((m) => m.conn > 1 && m.type === 'webviewReady'), {
        timeout: 20_000,
      })
      .toBe(true);
    const c2 = connectionCount;
    expect(clientSent.filter((m) => m.conn === c2 && m.type === 'webviewReady')).toHaveLength(1);
    await expect(unready).toContainText('DI office data received (LIVE)', { timeout: 15_000 });
    await expect(unready).toContainText('office canvas NOT READY, awaiting layout');
    await expect(panel).toHaveCount(0);
    await shot(page, 'R2-fresh-data-canvas-unready-FIXTURE.png');

    // ── 3. HOLD: from the next connection on, webviewReady is HELD and EVERY office message is
    // WITHHELD except exactly ONE (that connection's first: it becomes the STORED state).
    withheldTypes.delete('layoutLoaded');
    withheldTypes.add('diOfficeState');
    passOneOfficeAbove = c2;
    heldClientTypes.add('webviewReady');
    await closeSockets('FIXTURE: lost before the layout');
    await expect
      .poll(
        () =>
          heldClient.length === 1 &&
          ledger.some((e) => e.conn > c2 && e.action === 'forwarded-pass-one'),
        { timeout: 20_000, message: `bootstrap held + one snapshot passed ${ledgerDump()}` },
      )
      .toBe(true);
    const c3 = connectionCount;
    const passed = ledger.find((e) => e.action === 'forwarded-pass-one')!;
    expect(passed.conn).toBe(c3);
    // The page holds EXACTLY that snapshot as its latest office state (observed, not assumed).
    await expect
      .poll(async () => presentIds(await storedState()), {
        timeout: 5_000,
        message: `stored == passed snapshot ${ledgerDump()}`,
      })
      .toEqual(passed.ids);
    // The roster the page HOLDS, read from the page: the BASE roster (the member the change will
    // remove is present; the member it will add is not).
    const { removedRef, addedRef } = fixture.changedRoster;
    const heldRoster = (await page.evaluate(() => {
      const last = (window as unknown as HooksWindow).__pixelAgentsTestHooks?.getDiStored?.() as
        | {
            missions: {
              missions: Array<{
                members: { members: Array<{ memberRef: string; characterId: number | null }> };
              }>;
            };
          }
        | undefined;
      return (last?.missions.missions ?? []).flatMap((m) =>
        m.members.members.map((x) => [x.memberRef, x.characterId]),
      );
    })) as Array<[string, number | null]>;
    const heldRef = (ref: string) => heldRoster.find(([r]) => r === ref)?.[1] ?? null;
    expect(
      heldRef(removedRef),
      `stored roster is BASE: ${removedRef} present ${ledgerDump()}`,
    ).not.toBeNull();
    expect(
      heldRef(addedRef),
      `stored roster is BASE: ${addedRef} absent ${ledgerDump()}`,
    ).toBeNull();
    // The PRECISE office-message count the page holds == the office frames the proxy forwarded.
    const officeAtT1 = await pageCount('diOfficeState');
    expect(officeAtT1, `page office count == forwarded office frames ${ledgerDump()}`).toBe(
      ledger.filter((e) => e.type === 'diOfficeState' && e.action.startsWith('forwarded')).length,
    );
    // FROZEN from here (the one pass is spent): no office message is delivered.
    expect(passOneOfficeAbove).toBeNull();
    const T1 = ledger.length;
    const storedIds = passed.ids!;
    const createdAtT1 = await pageCount('agentCreated');
    roster = 'changed';
    await expect
      .poll(() => pageCount('agentCreated'), {
        timeout: 30_000,
        message: `agentCreated reached the page before any layout ${ledgerDump(T1)}`,
      })
      .toBeGreaterThan(createdAtT1);
    await delay(500);
    const unconfirmedIds = (await page.evaluate(
      (n) =>
        ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.messageLog ?? [])
          .filter((m) => m.type === 'agentCreated')
          .slice(n)
          .map((m) => m.id),
      createdAtT1,
    )) as number[];
    const drawnBefore = await drawnIds();
    const pre = JSON.stringify({
      storedIds,
      unconfirmedIds,
      drawnBefore,
      ledger: ledger.slice(T1),
    });
    console.log(`[precondition] ${pre}`);
    expect(await pageCount('diOfficeState'), `no office message reached the page ${pre}`).toBe(
      officeAtT1,
    );
    expect(unconfirmedIds.length).toBeGreaterThan(0);
    for (const id of unconfirmedIds) {
      expect(storedIds, `agentCreated(${id}) absent from the stored state ${pre}`).not.toContain(
        id,
      );
      // SNAPSHOT-ONLY canvas: an announcement no snapshot confirmed is NEVER drawn.
      expect(drawnBefore, `agentCreated(${id}) NOT drawn ${pre}`).not.toContain(id);
    }
    await expect(unready).toContainText('office canvas NOT READY, awaiting layout');

    // ── 4. RELEASE the bootstrap: existingAgents + layoutLoaded reach the page; office messages
    // stay WITHHELD — only a reconcile ON LAYOUT ARRIVAL can fix the canvas.
    const existingBefore = await pageCount('existingAgents');
    heldClientTypes.delete('webviewReady');
    for (const held of heldClient.splice(0)) held.server.send(held.message);
    await expect(panel).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(() => pageCount('existingAgents'), { timeout: 5_000 })
      .toBeGreaterThan(existingBefore);
    // existingAgents' payload as the proxy FORWARDED it (the handler count above shows it arrived).
    const releasedExisting =
      [...ledger]
        .reverse()
        .find((e) => e.type === 'existingAgents' && e.action.startsWith('forwarded'))?.agents ?? [];
    // The server's store lists them (existingAgents is delivered) — DI never draws from it.
    for (const id of unconfirmedIds) expect(releasedExisting).toContain(id);
    await expect
      .poll(drawnIds, {
        timeout: 5_000,
        message: `drawn == stored canonical roster ${ledgerDump(T1)}`,
      })
      .toEqual(storedIds);
    const identity = await page.evaluate(() => {
      const w = window as unknown as HooksWindow;
      const last = w.__pixelAgentsTestHooks?.getDiStored?.() as
        | {
            missions: {
              missions: Array<{
                sourceId: string;
                missionRef: string;
                members: {
                  members: Array<{
                    characterId: number | null;
                    coverage: string;
                    memberRef: string;
                    taskId: { state: string; value?: string | null };
                  }>;
                };
              }>;
            };
          }
        | undefined;
      const want: Array<[number, string]> = [];
      for (const mission of last?.missions.missions ?? [])
        for (const member of mission.members.members)
          if (member.characterId !== null)
            want.push([
              member.characterId,
              JSON.stringify([
                member.coverage,
                mission.sourceId,
                mission.missionRef,
                member.taskId.state === 'observed' ? (member.taskId.value ?? null) : null,
                member.memberRef,
              ]),
            ]);
      const byId = (a: [number, string], b: [number, string]) => a[0] - b[0];
      return {
        recorded: [...(w.__pixelAgentsTestHooks?.getDiRecorded?.() ?? [])].sort(byId),
        want: want.sort(byId),
      };
    });
    expect(identity.recorded.length).toBeGreaterThan(0);
    expect(identity.recorded, 'identity map CONFIRMED from the stored state').toEqual(
      identity.want,
    );
    await expect
      .poll(typingCount, { timeout: 5_000, message: 'animation reflects the stored state at once' })
      .toBe(storedIds.length);
    // The WITHHOLDING ITSELF, T1..T2, from the ledger:
    const window12 = ledger.slice(T1);
    const officeHeld = window12.filter(
      (e) => e.type === 'diOfficeState' && e.action === 'withheld',
    );
    const officeDelivered = window12.filter(
      (e) => e.type === 'diOfficeState' && e.action.startsWith('forwarded'),
    );
    const summary = JSON.stringify({
      officeHeldT1T2: officeHeld.length,
      officeDeliveredT1T2: officeDelivered.length,
      pageOfficeCountT1: officeAtT1,
      pageOfficeCountT2: await pageCount('diOfficeState'),
      reprs: [...new Set(ledger.map((e) => e.repr))],
      undeterminable: ledger.filter((e) => e.type === null).length,
    });
    console.log(`[withhold T1..T2] ${summary}`);
    expect(officeHeld.length, `held ${summary}`).toBeGreaterThan(0);
    expect(officeDelivered, `delivered ${summary}`).toEqual([]);
    expect(await pageCount('diOfficeState'), `page office count ${summary}`).toBe(officeAtT1);
    expect(
      ledger.filter((e) => e.type === null),
      'no undeterminable frame',
    ).toEqual([]);
    // Unconfirmed announcements are REPORTED in the inspector, never drawn.
    for (const id of unconfirmedIds) {
      await expect(page.getByTestId('di-unconfirmed')).toContainText(`character #${id}`);
      expect(await drawnIds()).not.toContain(id);
    }
    await shot(page, 'R3-layout-ready-flush-office-withheld-FIXTURE.png');

    // ── 5. Office messages flow again, still on the CHANGED roster: the snapshot CONFIRMS the
    // announced character — drawn now, and only now; the inspector report is settled.
    withheldTypes.clear();
    await expect
      .poll(drawnIds, { timeout: 20_000, message: 'drawn when a snapshot confirms it' })
      .toEqual(expect.arrayContaining(unconfirmedIds));
    await expect(page.getByTestId('di-unconfirmed')).toHaveCount(0);
    roster = 'base';
    await expect(panel).toContainText('DI Mission office: LIVE', { timeout: 15_000 });
    await expect
      .poll(
        async () =>
          JSON.stringify(await drawnIds()) === JSON.stringify(presentIds(await storedState())),
        { timeout: 20_000 },
      )
      .toBe(true);

    // ── 6. A DIRTY LAYOUT EDIT survives a reconnect: layout ready -> nothing requested.
    await page.getByTitle('Edit office layout').click();
    await page.getByTitle('Paint floor tiles').click();
    for (let i = 1; i <= 8 && !(await page.getByTitle('Undo (Ctrl+Z)').isVisible()); i++) {
      await page.evaluate(
        (n) => (window as unknown as HooksWindow).__pixelAgentsTestHooks?.editorTileAction?.(n, n),
        i,
      );
    }
    await expect(page.getByTitle('Undo (Ctrl+Z)')).toBeVisible();
    const layoutBefore = await pageCount('layoutLoaded');
    const connBeforeEdit = connectionCount;
    await closeSockets('FIXTURE: lost during an edit');
    await expect.poll(() => connectionCount, { timeout: 20_000 }).toBeGreaterThan(connBeforeEdit);
    await expect(panel).toContainText('DI Mission office: LIVE', { timeout: 20_000 });
    expect(clientSent.some((m) => m.conn > connBeforeEdit && m.type === 'webviewReady')).toBe(
      false,
    );
    expect(await pageCount('layoutLoaded')).toBe(layoutBefore);
    await expect(page.getByTitle('Undo (Ctrl+Z)')).toBeVisible();
    await shot(page, 'R4-dirty-edit-survives-reconnect-FIXTURE.png');
    console.log(
      `[ledger office frames] ${JSON.stringify(ledger.filter((e) => e.type === 'diOfficeState'))}`,
    );
    expect(output).not.toContain(access.token);
    expect(output).not.toContain(BACKEND_TOKEN);
  } finally {
    child.kill('SIGTERM');
    await delay(300);
    await new Promise<void>((r) => backend.close(() => r()));
    // the sandbox is left in place (no deletion)
  }
});
