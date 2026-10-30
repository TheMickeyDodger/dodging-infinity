/**
 * THE ATTACH PROOF'S ENTRY (owner item (d), R2): started ONLY by a person, by hand, in their own terminal, with no argument. It is a DISPOSABLE PROOF SCAFFOLD, not a product capability, and
 * nothing imports it: not the office entry (the launch closure pin keeps diCli.ts away from the launch route), and not any test (all of attachProof.ts is tested with injected effects instead).
 *
 * It wires the REAL Node module objects into the composition: `process.getBuiltinModule` returns the same objects `require` does, so the recorder wraps the functions every caller reaches through
 * them, and no bundler copy stands in. Its only writes are its own terminal output and the evidence file; it starts the two programs the launch route starts (Herdr's read-only agent list and
 * the opener), and nothing else.
 *
 * Before anything is installed or launched it refuses unless the disposable root, its launcher directory, the binding file and the evidence directory all exist as themselves.
 */
import {
  ATTACH_PROOF,
  attachProofPreflight,
  humanLines,
  parseProofBinding,
  runAttachProof,
} from './attachProof.js';

const childProcess = process.getBuiltinModule('node:child_process');
const crypto = process.getBuiltinModule('node:crypto');
const dgram = process.getBuiltinModule('node:dgram');
const fs = process.getBuiltinModule('node:fs');
const net = process.getBuiltinModule('node:net');

const table = (value: object): Record<string, unknown> => value as Record<string, unknown>;

function lstatOrNull(
  path: string,
): { isDirectory(): boolean; isFile(): boolean; isSymbolicLink(): boolean } | null {
  try {
    return fs.lstatSync(path);
  } catch {
    return null;
  }
}

async function main(): Promise<number> {
  if (process.argv.length !== 2) {
    process.stdout.write('REFUSED: the attach proof takes no argument; nothing was started.\n');
    return 2;
  }
  const problem = attachProofPreflight(
    {
      root: ATTACH_PROOF.root,
      launcherDir: ATTACH_PROOF.target.launcherDir,
      bindingFile: ATTACH_PROOF.bindingFile,
      evidenceDir: ATTACH_PROOF.evidenceDir,
    },
    lstatOrNull,
  );
  if (problem !== null) {
    process.stdout.write(`REFUSED: ${problem}; nothing was started.\n`);
    return 2;
  }
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d+Z$/, 'Z');
  const evidenceFile = `${ATTACH_PROOF.evidenceDir}/record-${stamp}.json`;
  const outcome = await runAttachProof({
    target: ATTACH_PROOF.target,
    recorderTargets: {
      childProcess: table(childProcess),
      socketPrototype: table(net.Socket.prototype),
      datagramPrototype: table(dgram.Socket.prototype),
      fileSystem: table(fs),
      fileSystemPromises: table(fs.promises),
      global: table(globalThis),
    },
    randomBytes: (size) => crypto.randomBytes(size),
    env: process.env,
    readBinding: async () =>
      parseProofBinding(await fs.promises.readFile(ATTACH_PROOF.bindingFile, 'utf8')),
    lines: humanLines((listener) => {
      process.stdin.setEncoding('utf8');
      process.stdin.on('data', listener);
    }),
    say: (text) => {
      process.stdout.write(`${text}\n`);
    },
    clock: () => Date.now(),
    writeEvidence: (text) =>
      fs.promises.writeFile(evidenceFile, text, { flag: 'wx', mode: 0o600, encoding: 'utf8' }),
  });
  process.stdout.write(`The evidence is in ${evidenceFile}\n`);
  return outcome.launch.launched && outcome.assessment?.supported === true ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (err: unknown) => {
    process.stdout.write(`The attach proof stopped: ${(err as Error).message}\n`);
    process.exit(3);
  },
);
