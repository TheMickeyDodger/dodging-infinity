/**
 * Deliverable 3: the ISOLATED Mission preview. FIXTURE / VISUAL TEST ONLY.
 *
 * The REAL app (src/main.tsx, unmodified: App, its WebSocket transport, the scan face), in the webview's PRODUCTION build, is served by `vite preview`,
 * and its `/ws` is answered by THIS module's fixture transport, never by an office server. Being a production build, the page carries no development
 * client: the ONE socket it opens is that `/ws`. The transport first sends the same asset messages the real office server sends (decoded by the same
 * core loaders), then the scene. Each scene sends one office state the REAL server projected from a REAL producer answer (exported by
 * the server wire tests) with `fixtureMode` set, so the product draws its own FIXTURE label; and it answers only what that scene names, from replies
 * the office's REAL relay emitted (or, where one is declared synthetic below, says so). Everything else the page sends is recorded and never answered.
 *
 * What it does NOT do: it starts no office server (no dist/di-cli.js), so it passes no --state-dir, --mission-panel-discovery,
 * --delivery-adapter-discovery or Herdr flag; it reads no discovery file, opens no connection to a request panel, delivery adapter, Herdr or backend,
 * and reads or writes no Mission store and no ~/.pixel-agents file. It grants no authority: a delete or a delivery step the page sends goes nowhere.
 *
 * The page clock is held at the scene's own evaluation time (the moment the real server projected its answer), so the client's local expiry reads
 * the same moment the server did; without it, an answer produced under a test clock would read stale for no reason of its own.
 *
 * Launch: see test/harness/di-mission-preview.config.ts. Walkthrough and captures: docs/di-mission-preview.md.
 */
import fs from 'node:fs';
import path from 'node:path';
import type { Duplex } from 'node:stream';
import { fileURLToPath } from 'node:url';

import type { Plugin } from 'vite';
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import { buildAssetIndex, buildFurnitureCatalog } from '../../../core/src/assets/build.ts';
import { decodeAllCarpets, decodeAllCharacters, decodeAllFloors, decodeAllFurniture, decodeAllWalls } from '../../../core/src/assets/loader.ts';
import type { DiMissionRequestReply, DiOfficeState } from '../../../core/src/messages.ts';
import type { DiMissionStatusId } from '../../src/components/diMissionStatus.ts';

const WEBVIEW = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
/**
 * THE launch path, as a reader types it from integrations/pixel-agents/webview-ui (docs/di-mission-preview.md quotes these exactly, and
 * test/diMissionPreviewBrowser.test.ts runs exactly these, changing only the port to a free one and the build directory to its own).
 */
export const PREVIEW_PORT = 5179;
export const PREVIEW_BUILD_COMMAND = ['npx', 'vite', 'build', '--config', 'test/harness/di-mission-preview.config.ts'] as const;
export const PREVIEW_SERVE_COMMAND = ['npx', 'vite', 'preview', '--config', 'test/harness/di-mission-preview.config.ts', '--port', String(PREVIEW_PORT), '--strictPort'] as const;
export const PREVIEW_INDEX = '/test/harness/di-mission-preview.html';
export const PREVIEW_SCENE_PAGE = '/test/harness/di-mission-preview-scene.html';
/** Where the scene page's document-relative asset addresses land (see configurePreviewServer). */
const SCENE_ASSETS = '/test/harness/assets/';
const COOKIE = 'di_mission_preview';
const VERSION = (JSON.parse(fs.readFileSync(path.join(WEBVIEW, '..', 'package.json'), 'utf-8')) as { version: string }).version;

type Any = Record<string, unknown>;
const fixture = <T,>(file: string): T => JSON.parse(fs.readFileSync(path.join(WEBVIEW, 'test/fixtures', file), 'utf-8')) as T;

/** Where a scene's office state comes from. */
export type DiPreviewProvenance =
  /** a real producer answer (a Python test's export), projected by the real server (a server test's export) */
  | 'producer-derived';

export interface DiPreviewScene {
  id: string;
  title: string;
  /** The fixture its office state comes from (under webview-ui/test/fixtures) and which state in it. */
  fixture: string;
  pick: string;
  provenance: DiPreviewProvenance;
  /** What in it is SYNTHETIC, said plainly (an input standing for something no producer path here supplies). */
  synthetic: string;
  /** What to look at, and how a person reaches it. */
  look: string;
  /** The statuses its Missions read in the product (asserted by test/diMissionPreviewBrowser.test.ts). */
  statuses: readonly DiMissionStatusId[];
  /** A `deleteLocalData` is answered with this recorded relay reply (di-mission-request-delete-replies.FIXTURE.json); null: never answered (in flight). */
  deleteReply?: string | null;
  /** A delivery `status` read is answered with the DECLARED SYNTHETIC status answer below. */
  deliveryStatus?: boolean;
  /** The preview closes the socket this long after the state, and leaves that page's reconnections unanswered: the office is then disconnected. */
  disconnectAfterMs?: number;
}

const LIFECYCLE = 'di-office-state-mission-lifecycle-v2.FIXTURE.json';
const ROSTER = 'di-office-state-herdr-roster-v2.FIXTURE.json';
const WORKFLOW = 'di-office-state-workflow-phases-v2.FIXTURE.json';
const ABANDONED = 'di-office-state-progress-abandoned-v2.FIXTURE.json';
const ARCHIVED_DELIVERY = 'di-office-state-archived-delivery-v2.FIXTURE.json';
const DELETE_REPLIES = 'di-mission-request-delete-replies.FIXTURE.json';

const LIFECYCLE_SYNTHETIC =
  'Mission Core’s own records, made by the producer’s test harness (its observer and spawner are test stand-ins); the task-observed step’s runtime snapshot is synthetic (it stands for a runtime capture; no process ran). Completion is Mission Core’s verify: the canonical status is Ended, never Verified.';
const ROSTER_SYNTHETIC = 'The runtime snapshot is synthetic (it stands for the runtime capture of live sessions; no process is observed). The Herdr task records are synthetic.';

/** The lifecycle Mission (Mission Core) and the one the startup sweep reclaims, as the lifecycle export names them. */
export const LIFECYCLE_REF = fixture<{ mission_ref: string }>(LIFECYCLE).mission_ref;
export const OVERDUE_REF = fixture<{ overdue_mission_ref: string }>(LIFECYCLE).overdue_mission_ref;
/** The delete scenes' Mission: the archived Mission Core Mission the recorded delete replies were answered for, and a running one beside it. */
export const DELETE_REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
export const RUNNING_REF = 'synthetic_mission_core:mn-00000000000000000000000000000002';

const lifecycleScene = (id: string, step: string, title: string, statuses: readonly DiMissionStatusId[], look: string): DiPreviewScene => ({
  id,
  title,
  fixture: LIFECYCLE,
  pick: `states.${step}`,
  provenance: 'producer-derived',
  synthetic: LIFECYCLE_SYNTHETIC,
  look,
  statuses,
});

const deleteScene = (id: string, reply: string | null, title: string, look: string): DiPreviewScene => ({
  id,
  title,
  fixture: ABANDONED,
  pick: 'state',
  provenance: 'producer-derived',
  synthetic:
    'Mission Core’s own records made by the producer’s test harness. The delete answer is the request panel’s REAL answer over its real HTTP route, relayed by the office’s REAL relay; only its sequence number is re-addressed to the request this page sent.',
  look,
  statuses: ['running', 'stopped', 'not_started'],
  deleteReply: reply,
});

/** Every scene, in walkthrough order. */
export const DI_PREVIEW_SCENES: readonly DiPreviewScene[] = [
  lifecycleScene('lifecycle-of-record', 'of_record', 'A Mission of record, never started', ['not_started', 'ended'], 'Missions: the Mission reads Not started under Mine and All. The overdue Mission beside it is already finished and filed in Archived.'),
  lifecycleScene('lifecycle-approved', 'approved_never_started', 'Approved, and still never started', ['not_started', 'ended'], 'Missions, then its row: approval does not start a run; the details still say Not started.'),
  lifecycleScene('lifecycle-start-requested', 'start_requested', 'Its start requested: the catalog does not see its team yet', ['unknown', 'ended'], 'Missions, then its row: the request was dispatched, but the catalog does not yet observe a team, so the office says Unknown with its reason, never Running.'),
  lifecycleScene('lifecycle-task-observed', 'task_observed', 'Its task observed, no worker confirmed', ['task_observed', 'ended'], 'Missions, its row, Reception: the SAME Mission reads Task observed on the list, its details and its Reception card. No worker is drawn as working.'),
  lifecycleScene('lifecycle-archived', 'completed_ended_archived', 'Finished: filed into Archived at once, read-only', ['ended'], 'Missions: All, Needs you, Stalled, Running and Done no longer show it; Archived does. Its details: every action is disabled with its reason, and the owner’s hold-to-delete is offered.'),
  lifecycleScene('lifecycle-reclaimed', 'reclaimed_at_startup', 'Reclaimed by the startup sweep after 30 days', ['ended'], 'Missions, Archived, its row: its local data was reclaimed (the retained summary and what is no longer kept).'),
  lifecycleScene('lifecycle-deleted', 'deleted_by_owner', 'Deleted by its owner', ['ended'], 'Missions, Archived, its row: its local data was deleted by its owner; the delete is not offered again.'),
  {
    ...lifecycleScene('lifecycle-stale', 'task_observed', 'The same answer, read after it expired', ['stale'], 'Missions: every Mission reads Stale; nothing is current, nothing is drawn working.'),
    pick: 'expired.state',
    synthetic: `${LIFECYCLE_SYNTHETIC} The office read the task-observed answer 600 s after it was produced (the server’s own local expiry; no new answer).`,
  },
  {
    ...lifecycleScene('lifecycle-disconnected', 'task_observed', 'The same answer, then the office disconnects', ['disconnected'], 'Missions: a moment after the page opens the preview closes its socket and leaves every reconnection unanswered; every Mission then reads Disconnected, its last known status not current.'),
    disconnectAfterMs: 2500,
  },
  {
    id: 'herdr-roster',
    title: 'Herdr tasks: what the producer’s roster lets the office say about workers',
    fixture: ROSTER,
    pick: 'state',
    provenance: 'producer-derived',
    synthetic: ROSTER_SYNTHETIC,
    look: 'Missions (All, then Archived): Running is a live agent at work; Unavailable is a task no live session corroborates (no worker is confirmed now, which is not proof it never started); Blocked, Failed, Waiting, Stopped, Ended, and Unrecognised for a status literal the office does not know. The office draws the working agent.',
    statuses: ['running', 'blocked', 'failed', 'stopped', 'waiting', 'ended', 'unrecognised', 'unavailable'],
  },
  {
    id: 'workflow-phases',
    title: 'Workflows: the phases only a workflow declares',
    fixture: WORKFLOW,
    pick: 'state',
    provenance: 'producer-derived',
    synthetic: 'The workflow records are made by the workflow authority’s own test constructor; no engine ran.',
    look: 'Missions: Start requested (a handoff was sent), Verified (its declared verified success) and Waiting (it needs reauthorization).',
    statuses: ['start_requested', 'verified', 'waiting'],
  },
  deleteScene('delete-in-flight', null, 'Owner hold-to-delete: sent, its answer not back', 'Missions, Archived, the Stopped Mission’s row, then hold the trash: it waits, and the hold is withdrawn so it cannot be sent twice. The Running Mission’s trash is disabled: “A running Mission cannot be deleted.”'),
  deleteScene('delete-pending', 'pending_started_not_finished', 'Owner hold-to-delete: accepted, still finishing', 'Hold the trash on the archived Mission: the request panel accepted it and its deletion is still finishing; it is never shown as refused or as done.'),
  deleteScene('delete-refused-busy', 'refused_busy', 'Owner hold-to-delete: refused, busy', 'Hold the trash: the request panel refused it with its own reason; nothing was removed, and the hold is offered again.'),
  deleteScene('delete-refused-stale', 'refused_status_stale', 'Owner hold-to-delete: refused, the page was out of date', 'Hold the trash: refused because the Mission changed since the page showed it; nothing was removed.'),
  deleteScene('delete-completed', 'completed_after_reclamation', 'Owner hold-to-delete: done', 'Hold the trash: deleted, with what it removed and what it kept.'),
  {
    id: 'archived-delivery',
    title: 'An archived task’s delivery: read-only, still readable',
    fixture: ARCHIVED_DELIVERY,
    pick: 'state',
    provenance: 'producer-derived',
    synthetic:
      'The Herdr task records are synthetic. The delivery adapter’s STATUS ANSWER is DECLARED SYNTHETIC (the shape test/diArchivedDeliveryBrowser.test.ts uses): no delivery adapter is connected, and no delivery step can be sent anywhere.',
    look: 'Missions, Archived, the linked task’s row, then Read delivery status: its facts are shown, and the confirmation and the step are disabled with the archived reason. The other COMPLETE task has no delivery record, so nothing can be created from this archived view.',
    statuses: ['ended'],
    deliveryStatus: true,
  },
];

/** The office state a scene sends: the exported projection, labelled as a fixture by the product's own mechanism. */
export function sceneState(scene: DiPreviewScene): DiOfficeState {
  let at: unknown = fixture<Any>(scene.fixture);
  for (const key of scene.pick.split('.')) at = (at as Any)[key];
  return { ...structuredClone(at as DiOfficeState), fixtureMode: true };
}

/** The moment the page clock is held at: the scene state's own evaluation time, in milliseconds. */
export const sceneClockMs = (scene: DiPreviewScene): number => sceneState(scene).times.evaluatedAt! * 1000;

/** DECLARED SYNTHETIC: the delivery adapter's status answer for the archived linked task (COMMIT eligible), as test/diArchivedDeliveryBrowser.test.ts states it. */
export function syntheticDeliveryStatus(deliveryId: string): Any {
  return {
    ok: true,
    status: {
      delivery: {
        delivery_id: deliveryId,
        revision: 4,
        phase: 'base_refreshed',
        authorization: { valid: true, problem: null, human_identity: 'travis', allowed_actions: ['commit', 'push', 'pr_create'], mode: 'pr_create' },
        base_refresh: { state: 'succeeded', receipt_id: 'rcpt-base-1', receipt_state: 'succeeded', attempts: 1, observed: null },
        commit: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, commit_oid: null },
        push: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, remote_oid: null },
        pr_url: null,
        blocker: null,
        next_action: { action: 'run_step', step: 'COMMIT' },
      },
      office: {
        eligible: { step: 'COMMIT', binding: { delivery_id: deliveryId, revision: 4, candidate_identity_digest_sha256: 'c'.repeat(64), step: 'COMMIT' } },
        not_eligible: null,
        action_steps: ['COMMIT', 'PUSH', 'PR_CREATE'],
        local_confirmation_note:
          'This confirmation is a local check. The delivery was authorized once, by the human ceremony, for these steps. This confirmation is not recorded in the delivery record.',
        performs_steps: true,
        performs_steps_note: 'This office performs ONE confirmed step at a time (COMMIT, PUSH or PR_CREATE; never the base refresh, never every step).',
      },
    },
  };
}

/** One message the page sent, and what the preview did with it. */
export interface DiPreviewTraffic {
  scene: string;
  message: Any;
  answered: 'scene_state' | 'recorded_reply' | 'declared_synthetic' | 'never';
}

/** Where the preview serves its record of every message every page sent, in order (read-only; the browser test reads it). */
export const PREVIEW_TRAFFIC = '/__di-mission-preview/traffic';

function sceneOf(cookieHeader: string | undefined): { scene: DiPreviewScene; page: string } | null {
  const raw = (cookieHeader ?? '')
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${COOKIE}=`));
  if (!raw) return null;
  const [id, page] = decodeURIComponent(raw.slice(COOKIE.length + 1)).split('|');
  const scene = DI_PREVIEW_SCENES.find((s) => s.id === id);
  return scene && page ? { scene, page } : null;
}

const escapeHtml = (text: string) => text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

function indexBody(): string {
  const items = DI_PREVIEW_SCENES.map(
    (s) =>
      `<li><a href="${PREVIEW_SCENE_PAGE}?scene=${s.id}">${escapeHtml(s.title)}</a> <code>${s.id}</code><br>` +
      `<small>Reads: ${s.statuses.join(', ')}. ${escapeHtml(s.look)}<br>Source: <code>webview-ui/test/fixtures/${s.fixture}</code> (${s.pick}), ${s.provenance}. Synthetic: ${escapeHtml(s.synthetic)}</small></li>`,
  ).join('\n');
  return `<h1>FIXTURE / VISUAL TEST — DI Mission preview</h1>
<p>Each link opens the REAL app over this preview's own fixture transport. The office states were projected by the real server from real producer answers
(each scene says what in it is synthetic). Nothing here is live: no office server, request panel, delivery adapter, Herdr or backend is connected, no Mission
store is read or written, and nothing a page sends is carried out. Open one scene at a time (the scene a page reads is named in a cookie). Figures 4.4a-c are not in this repository: this preview shows the
status contract's own words and states, not a match to any figure. Walkthrough: docs/di-mission-preview.md.</p>
<ol>
${items}
</ol>`;
}

/** The scene page's boot script (static: built once, it reads its scene from the address): the face, the scene's socket cookie, and the page clock held at that scene's own time. */
function bootScript(): string {
  const clocks = Object.fromEntries(DI_PREVIEW_SCENES.map((s) => [s.id, sceneClockMs(s)]));
  return `<script>
(function () {
  var CLOCKS = ${JSON.stringify(clocks)};
  var scene = new URLSearchParams(location.search).get('scene');
  if (!Object.prototype.hasOwnProperty.call(CLOCKS, scene)) {
    location.replace('${PREVIEW_INDEX.split('/').pop()}');
    return;
  }
  window.__DI_OFFICE_PANEL_FACE = 'scan';
  document.cookie = '${COOKIE}=' + encodeURIComponent(scene + '|' + Math.random().toString(36).slice(2)) + '; path=/; SameSite=Strict';
  var AT = CLOCKS[scene];
  var RealDate = Date;
  function PinnedDate() {
    var args = Array.prototype.slice.call(arguments);
    return args.length === 0 ? new RealDate(AT) : new (Function.prototype.bind.apply(RealDate, [null].concat(args)))();
  }
  PinnedDate.prototype = RealDate.prototype;
  PinnedDate.now = function () { return AT; };
  PinnedDate.parse = RealDate.parse;
  PinnedDate.UTC = RealDate.UTC;
  window.Date = PinnedDate;
})();
</script>`;
}

/** The asset messages the real office server sends a page before anything else (decoded here by the same core loaders the webview's own Vite config uses), read once. */
let assetMessages: Any[] | null = null;
function assets(): Any[] {
  if (assetMessages !== null) return assetMessages;
  const dir = path.join(WEBVIEW, 'public/assets');
  const catalog = buildFurnitureCatalog(dir);
  const index = buildAssetIndex(dir);
  const layout = index.defaultLayout ? (JSON.parse(fs.readFileSync(path.join(dir, index.defaultLayout), 'utf-8')) as unknown) : null;
  assetMessages = [
    { type: 'characterSpritesLoaded', characters: decodeAllCharacters(dir) },
    { type: 'floorTilesLoaded', sprites: decodeAllFloors(dir) },
    { type: 'wallTilesLoaded', sets: decodeAllWalls(dir) },
    { type: 'carpetTilesLoaded', sets: decodeAllCarpets(dir) },
    { type: 'furnitureAssetsLoaded', catalog, sprites: decodeAllFurniture(dir, catalog) },
    { type: 'layoutLoaded', layout },
  ];
  return assetMessages;
}

/** The file that marks a directory as this preview's own build output: the build refuses to empty any other non-empty directory. */
const BUILD_MARKER = 'DI-MISSION-PREVIEW-BUILD.txt';

/**
 * The preview's Vite plugin. At BUILD: the scene index and the scene page's boot script (the app itself is the webview's production build, so no
 * development client and no hot-reload socket exist). At PREVIEW: the fixture transport on `/ws` and the record of what pages sent.
 */
export function diMissionPreviewPlugin(): Plugin {
  const traffic: DiPreviewTraffic[] = [];
  const deleteReplies = fixture<{ replies: Record<string, DiMissionRequestReply> }>(DELETE_REPLIES).replies;
  /** Pages the preview disconnected on purpose: their reconnection is refused. */
  const cut = new Set<string>();
  let outDir = '';
  return {
    name: 'di-mission-preview',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
      if (config.command !== 'build') return;
      // the preview IS the production build: a development build (NODE_ENV set to anything but production, as a test runner sets it) would run the
      // development asset mock instead of the app as it ships, so it is refused rather than built
      if (!config.isProduction)
        throw new Error(`the Mission preview must be a production build; NODE_ENV is ${JSON.stringify(process.env.NODE_ENV)} (unset it)`);
      const entries = fs.existsSync(outDir) ? fs.readdirSync(outDir) : [];
      if (entries.length > 0 && !entries.includes(BUILD_MARKER))
        throw new Error(`refusing to build the Mission preview into ${outDir}: it is not empty and is not a Mission preview build (no ${BUILD_MARKER})`);
    },
    transformIndexHtml(html, ctx) {
      if (ctx.path.endsWith(PREVIEW_INDEX)) return html.replace('<!--di-mission-preview-index-->', indexBody());
      if (ctx.path.endsWith(PREVIEW_SCENE_PAGE)) return html.replace('<!--di-mission-preview-boot-->', bootScript());
      return html;
    },
    closeBundle() {
      fs.writeFileSync(
        path.join(outDir, BUILD_MARKER),
        'The isolated DI Mission preview build (FIXTURE / VISUAL TEST ONLY): webview-ui/test/harness/di-mission-preview.config.ts. Regenerated by every preview build.\n',
      );
    },
    configurePreviewServer(server) {
      // the app's asset base is './', made for a page at the server's root; the scene page lives under /test/harness/, so its document-relative
      // asset addresses (the Swarm busts) are the build's own /assets/
      server.middlewares.use((req, _res, next) => {
        if (req.url?.startsWith(SCENE_ASSETS)) req.url = req.url.slice(SCENE_ASSETS.length - '/assets/'.length);
        next();
      });
      server.middlewares.use(PREVIEW_TRAFFIC, (_req, res) => {
        res.setHeader('Content-Type', 'application/json');
        res.end(JSON.stringify(traffic));
      });
      const wss = new WebSocketServer({ noServer: true });
      const held = new Set<Duplex>();
      server.httpServer.on('close', () => {
        for (const socket of held) socket.destroy();
      });
      server.httpServer.on('upgrade', (req, socket, head) => {
        // the preview's /ws is the only socket the page opens (the production build has no development client)
        if (!req.url?.startsWith('/ws')) {
          socket.destroy();
          return;
        }
        const at = sceneOf(req.headers.cookie);
        if (!at) {
          socket.destroy();
          return;
        }
        if (cut.has(at.page)) {
          // a page the preview disconnected on purpose: its reconnection is left unanswered, as an unreachable office leaves it, so it stays
          // disconnected (a refused handshake would also do that, but the browser logs it as an error); released when the preview stops
          held.add(socket);
          socket.on('close', () => held.delete(socket));
          return;
        }
        wss.handleUpgrade(req, socket, head, (ws: WebSocket) => {
          ws.on('message', (data) => {
            const msg = JSON.parse(String(data)) as Any;
            const note = (answered: DiPreviewTraffic['answered']) => traffic.push({ scene: at.scene.id, message: msg, answered });
            if (msg.type === 'webviewReady') {
              note('scene_state');
              for (const asset of assets()) ws.send(JSON.stringify(asset));
              ws.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
              ws.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
              ws.send(JSON.stringify(sceneState(at.scene)));
              if (at.scene.disconnectAfterMs !== undefined)
                setTimeout(() => {
                  cut.add(at.page);
                  ws.close();
                }, at.scene.disconnectAfterMs);
            } else if (msg.type === 'diMissionRequest' && msg.op === 'deleteLocalData' && typeof at.scene.deleteReply === 'string') {
              note('recorded_reply');
              ws.send(JSON.stringify({ ...deleteReplies[at.scene.deleteReply], seq: msg.seq, op: 'deleteLocalData' }));
            } else if (msg.type === 'diDeliveryRequest' && msg.op === 'status' && at.scene.deliveryStatus === true) {
              note('declared_synthetic');
              ws.send(
                JSON.stringify({ type: 'diDeliveryReply', seq: msg.seq, op: 'status', state: 'ok', problem: null, reason: null, answer: syntheticDeliveryStatus(String(msg.deliveryId)) }),
              );
            } else {
              note('never');
            }
          });
        });
      });
    },
  };
}
