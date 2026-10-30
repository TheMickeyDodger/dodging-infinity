/**
 * THE ATTACH PROOF'S COMPOSITION (owner item (d), R2): ONE run of the approved launch route (herdrLaunch.ts) under the approved send recorder (sendRecorder.ts), for the ONE disposable target the
 * owner permitted, keeping the WHOLE record of what this process sent across the person's typing window.
 *
 * It is a DISPOSABLE PROOF SCAFFOLD, not a product capability. Only a person starts it (attachProofEntry.ts); the office entry never reaches it (the launch closure pin), and the office's Herdr
 * controls stay disabled. Every effect is injected, so no test starts anything.
 *
 * ONE RUN, in order: the recorder is installed; mark `proof-start`; the launch (the binding read at use, Herdr's read-only agent list, the launcher written, Terminal asked to open it); the
 * person is told what to do; they press Enter in THIS entry's own terminal; mark `typing-start`; they type in the attached window; they press Enter here again; mark `typing-end`; the recorder is
 * uninstalled; the window is assessed over the whole record; the evidence is written.
 *
 * WHY IT IS SOUND, point by point:
 * - The launch's Node functions are looked up on the recorded module objects when CALLED (`launchApisFrom`), so no function captured before the recorder was installed can go around it.
 * - Nothing is said between the two marks: this entry's own output is a socket write the recorder counts, so a word printed there would be a send inside the window.
 * - No office server and no browser run here. Their socket traffic would fall inside the window, and hiding it would need a filter, which the evidence rules forbid.
 * - A refused launch asks the person for nothing: there is no window to type in.
 *
 * WHAT IT DOES NOT ESTABLISH: anything about another actor; any path around the wrapped Node APIs (the recorder's own stated reach); where an attach lands; or how a REAL Mission's server,
 * workspace and agent are found (the disposable target's are known by construction; production discovery stays owed).
 */
import type {
  HerdrAttachTarget,
  HerdrLaunchResult,
  HerdrRoleBinding,
  NodeLaunchApis,
} from './herdrLaunch.js';
import { createHerdrLauncher, nodeHerdrLaunchEffects } from './herdrLaunch.js';
import type { SendRecord, SendRecorderTargets, SendWindowAssessment } from './sendRecorder.js';
import { assessSendWindow, installSendRecorder } from './sendRecorder.js';

/** The owner-permitted disposable root (the single-target exception) and this repository's evidence directory for the proof. */
const ROOT = '/Users/travissellers/Documents/gb-tmp/herdr-attach-proof-20261028';
const EVIDENCE_DIR =
  '/Users/travissellers/tools/di-mission-worktrees/mn-491c0b35b901d74ca373a8f2f9c076fe/.herd/state/evidence/20261026-141936-4b6847/attach-proof';

/**
 * The one target, and where the proof reads and writes. Herdr runs with HOME at the root and its API socket at the path the owner's Q4 answer established for that HOME; launchers stay
 * under the root; the binding and the evidence stay in this repository.
 */
export const ATTACH_PROOF = {
  root: ROOT,
  target: {
    herdrBin: '/Users/travissellers/.local/bin/herdr',
    home: ROOT,
    socketPath: `${ROOT}/.config/herdr/herdr.sock`,
    launcherDir: `${ROOT}/launchers`,
  } satisfies HerdrAttachTarget,
  bindingFile: `${EVIDENCE_DIR}/binding.json`,
  evidenceDir: EVIDENCE_DIR,
} as const;

export const PROOF_START = 'proof-start';
export const TYPING_START = 'typing-start';
export const TYPING_END = 'typing-end';

/** The limits the evidence carries with every record, so no reader takes the record for more than it is. */
export const ATTACH_PROOF_LIMITS = [
  'The record is what THIS process sent through the Node APIs the recorder wraps, after it was installed; it does not see a native path around them, another isolate or anything kernel-wide.',
  'Nothing is claimed about any other actor: whether anything else sent input to the attached terminal is not established here.',
  "The window between the two marks is bounded by this program's receipt of two lines, not by an observed period of typing. That the person typed in the attached terminal during it is THEIR OWN REPORT, not this program's measurement. This entry never generates keystrokes.",
  'Each typing mark is set by a line this program RECEIVED after the wait for it began. A line typed earlier may have been buffered and received later, so receipt order says nothing about when any key was pressed. Lines received while no wait was pending are ignored, never kept, and counted in linesIgnored.',
  "The Herdr server and the agent session are known because the disposable target was created with them; how the office finds a real Mission's server, workspace and agent stays owed.",
] as const;

interface PathStat {
  isDirectory(): boolean;
  isFile(): boolean;
  isSymbolicLink(): boolean;
}

/** Why the proof must not start, or null. Each path must exist as itself (never through a symbolic link): the root and its launcher directory, the binding file and the evidence directory. */
export function attachProofPreflight(
  paths: { root: string; launcherDir: string; bindingFile: string; evidenceDir: string },
  lstat: (path: string) => PathStat | null,
): string | null {
  const directory = (path: string): string | null => {
    const stat = lstat(path);
    if (stat === null) return `${path} does not exist`;
    if (stat.isSymbolicLink()) return `${path} is a symbolic link`;
    return stat.isDirectory() ? null : `${path} is not a directory`;
  };
  if (!paths.launcherDir.startsWith(`${paths.root}/`))
    return `the launcher directory ${paths.launcherDir} is not inside ${paths.root}`;
  const problem =
    directory(paths.root) ?? directory(paths.launcherDir) ?? directory(paths.evidenceDir);
  if (problem !== null) return problem;
  const binding = lstat(paths.bindingFile);
  if (binding === null) return `${paths.bindingFile} does not exist`;
  if (binding.isSymbolicLink()) return `${paths.bindingFile} is a symbolic link`;
  return binding.isFile() ? null : `${paths.bindingFile} is not a regular file`;
}

const isText = (value: unknown): value is string => typeof value === 'string';
const isTextOrNull = (value: unknown): value is string | null =>
  value === null || typeof value === 'string';

/** The disposable target's ONE role, from the binding file's text. Anything not marked `disposable: true`, malformed or incomplete is no binding, so no production catalog row can stand in. */
export function parseProofBinding(text: string): HerdrRoleBinding | null {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  if (
    v.disposable !== true ||
    !isText(v.missionRef) ||
    !isText(v.agent) ||
    !isTextOrNull(v.session) ||
    !isTextOrNull(v.paneId)
  )
    return null;
  const identity = v.identity as Record<string, unknown> | null | undefined;
  if (typeof identity !== 'object' || identity === null || !isText(identity.reason)) return null;
  const state = identity.state as Record<string, unknown> | null | undefined;
  if (typeof state !== 'object' || state === null || typeof state.known !== 'boolean') return null;
  if (state.known && !isText(state.value)) return null;
  return {
    missionRef: v.missionRef,
    agent: v.agent,
    session: v.session,
    paneId: v.paneId,
    identity: { state: state as HerdrRoleBinding['identity']['state'], reason: identity.reason },
  };
}

type AnyFunction = (...args: unknown[]) => unknown;

/** The launch route's Node APIs over the RECORDED module objects, each looked up when it is called. */
export function launchApisFrom(
  tables: Pick<SendRecorderTargets, 'childProcess' | 'fileSystemPromises'>,
  randomBytes: (size: number) => { toString(encoding: 'hex'): string },
  env: Readonly<Record<string, string | undefined>>,
): NodeLaunchApis {
  const call = (table: Record<string, unknown>, name: string, args: unknown[]): unknown =>
    (table[name] as AnyFunction).apply(table, args);
  return {
    execFile: (...args) => call(tables.childProcess, 'execFile', args),
    spawn: (...args) =>
      call(tables.childProcess, 'spawn', args) as ReturnType<NodeLaunchApis['spawn']>,
    writeFile: (...args) => call(tables.fileSystemPromises, 'writeFile', args) as Promise<void>,
    randomBytes,
    env,
  };
}

/** The lines the person enters in THIS entry's own terminal. Reading writes nothing. */
export interface HumanLines {
  /** The next line RECEIVED AFTER this call. */
  next(): Promise<string>;
  /** How many lines were received while no wait was pending, and so ignored. */
  ignored(): number;
}

/**
 * The person's lines from a stream of text chunks (`subscribe` registers the one listener). There is NO QUEUE (round 65): a wait is
 * filled only by a line RECEIVED after it began. That orders receipts, never keypresses: a line typed earlier may have been buffered
 * (in the terminal, the system or the pipe) and received later. A line received while no wait is pending (before the first wait,
 * or between two waits), or the second of two lines arriving in one chunk, is ignored and counted, never kept for a later wait.
 */
export function humanLines(subscribe: (listener: (chunk: string) => void) => void): HumanLines {
  let waiter: ((line: string) => void) | null = null;
  let ignored = 0;
  let buffered = '';
  subscribe((chunk) => {
    buffered += chunk;
    for (let at = buffered.indexOf('\n'); at >= 0; at = buffered.indexOf('\n')) {
      const line = buffered.slice(0, at);
      buffered = buffered.slice(at + 1);
      const filling = waiter;
      waiter = null;
      if (filling) filling(line);
      else ignored += 1;
    }
  });
  return {
    next: () =>
      new Promise<string>((resolve) => {
        waiter = resolve;
      }),
    ignored: () => ignored,
  };
}

export interface AttachProofDeps {
  target: HerdrAttachTarget;
  /** The objects the recorder wraps: the real modules and prototypes in the entry; stand-ins in tests. */
  recorderTargets: SendRecorderTargets;
  randomBytes: (size: number) => { toString(encoding: 'hex'): string };
  env: Readonly<Record<string, string | undefined>>;
  /** A FRESH read of the disposable binding, at use. */
  readBinding(): Promise<HerdrRoleBinding | null>;
  /** The person's lines in THIS entry's own terminal (humanLines in the entry). */
  lines: HumanLines;
  /** Print to THIS entry's own terminal. Never called between the two typing marks. */
  say(text: string): void;
  clock(): number;
  /** Write the evidence document. Called once, after the recorder is uninstalled. */
  writeEvidence(text: string): Promise<void>;
}

export interface AttachProofOutcome {
  launch: HerdrLaunchResult;
  records: SendRecord[];
  assessment: SendWindowAssessment | null;
}

/** ONE proof run. Resolves once the evidence is written. */
export async function runAttachProof(deps: AttachProofDeps): Promise<AttachProofOutcome> {
  const records: SendRecord[] = [];
  const recorder = installSendRecorder(
    deps.recorderTargets,
    (record) => records.push(record),
    deps.clock,
  );
  let launch: HerdrLaunchResult;
  let assessment: SendWindowAssessment | null = null;
  try {
    recorder.mark(PROOF_START);
    const effects = nodeHerdrLaunchEffects(
      launchApisFrom(deps.recorderTargets, deps.randomBytes, deps.env),
      deps.readBinding,
    );
    launch = await createHerdrLauncher(deps.target, effects).launch();
    if (launch.launched) {
      deps.say(
        [
          'A Terminal window attached to the disposable Herdr server should now be open.',
          'When you are ready to type into THAT window, press Enter HERE first. From then on this window prints nothing.',
          'Then type in the attached window. When you have finished, come back and press Enter here again.',
        ].join('\n'),
      );
      await deps.lines.next();
      recorder.mark(TYPING_START);
      await deps.lines.next();
      recorder.mark(TYPING_END);
    }
  } finally {
    recorder.uninstall();
  }
  if (launch.launched)
    assessment = assessSendWindow(
      records,
      { herdrBin: deps.target.herdrBin, launcherDir: deps.target.launcherDir },
      TYPING_START,
      TYPING_END,
    );
  const evidence = {
    proof:
      'the Herdr OS-terminal attach proof, owner item (d): one disposable target; the human types; this process sends nothing',
    target: deps.target,
    launch,
    records,
    assessment,
    linesIgnored: deps.lines.ignored(),
    limits: ATTACH_PROOF_LIMITS,
  };
  await deps.writeEvidence(JSON.stringify(evidence, null, 2));
  deps.say(
    !launch.launched
      ? `Nothing was opened, and nothing was asked of you: ${launch.detail}`
      : assessment?.supported
        ? 'Measured by this program: nothing was sent by this process through the recorded APIs between the two Enters it received. That window is set by when your Enters ARRIVED here, not by when you typed. Your part is your own report: whether you typed in the attached window during it. Tell Engineering. The whole record is in the evidence file.'
        : `Measured by this program: the record does NOT support a no-input statement for the window between the two Enters it received: ${(assessment?.reasons ?? []).join('; ')}`,
  );
  return { launch, records, assessment };
}
