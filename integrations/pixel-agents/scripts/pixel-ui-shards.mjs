// Splits the webview UI test files (webview-ui/test/*.test.ts) into N balanced CI shards.
//
// Vitest's own --shard splits by path hash, which put 24 of 55 minutes on one of four runners. This
// balances by measured seconds per file (scripts/pixel-ui-test-durations.json, longest first onto the
// lightest shard), so every file lands on exactly one shard and new files are never dropped.
//
//   node scripts/pixel-ui-shards.mjs --shard 2 --of 4   prints shard 2's files, one per line
//   node scripts/pixel-ui-shards.mjs --check --of 4     proves the partition and prints the plan
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const TEST_DIR = path.join(ROOT, 'webview-ui', 'test');

export function testFiles(dir = TEST_DIR) {
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.test.ts'))
    .sort();
}

export function planShards(files, durations, count) {
  const seconds = (f) => durations.seconds[f] ?? durations.defaultSeconds;
  const shards = Array.from({ length: count }, () => ({ files: [], seconds: 0 }));
  const ordered = [...files].sort((a, b) => seconds(b) - seconds(a) || a.localeCompare(b));
  for (const file of ordered) {
    const lightest = shards.reduce((min, s) => (s.seconds < min.seconds ? s : min));
    lightest.files.push(file);
    lightest.seconds += seconds(file);
  }
  for (const s of shards) s.files.sort();
  return shards;
}

function arg(name) {
  const i = process.argv.indexOf(name);
  return i === -1 ? null : process.argv[i + 1];
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const count = Number(arg('--of') ?? 4);
  const durations = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'scripts', 'pixel-ui-test-durations.json'), 'utf8'),
  );
  const files = testFiles();
  const shards = planShards(files, durations, count);
  if (process.argv.includes('--check')) {
    const assigned = shards.flatMap((s) => s.files).sort();
    if (
      JSON.stringify(assigned) !== JSON.stringify(files) ||
      shards.some((s) => s.files.length === 0)
    ) {
      console.error('pixel-ui-shards: the shards do not cover every UI test file exactly once');
      process.exit(1);
    }
    shards.forEach((s, i) =>
      console.log(
        `shard ${i + 1}/${count}: ${s.files.length} files, ~${(s.seconds / 60).toFixed(1)} min`,
      ),
    );
  } else {
    const index = Number(arg('--shard'));
    if (!Number.isInteger(index) || index < 1 || index > count) {
      console.error('usage: pixel-ui-shards.mjs --shard <1..N> --of <N> | --check --of <N>');
      process.exit(2);
    }
    for (const f of shards[index - 1].files) console.log(`test/${f}`);
  }
}
