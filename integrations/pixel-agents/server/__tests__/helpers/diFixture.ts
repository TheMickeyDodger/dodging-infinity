/**
 * FIXTURE loader for the DI backend's serializer-generated canonical fixture
 * (CONTRACT-v3, sha256 55ee7e32…afb9, names the fixture hash pinned here). SYNTHETIC
 * fixture evidence only: never live acceptance. A swapped or edited fixture throws.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

export const FIXTURE_PATH = path.join(
  __dirname,
  '../fixtures/di-backend/v3-canonical-fixture.FIXTURE.json',
);
export const FIXTURE_SHA256 = 'c7dcd7284ee5566f0dee9d20084074e8aa96aeb274b2bc93d159cbe7d815b823';
export const FIXTURE_BYTES = 6861;

export function loadFixture(): Record<string, unknown> {
  const bytes = fs.readFileSync(FIXTURE_PATH);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== FIXTURE_SHA256) {
    throw new Error(`FIXTURE hash mismatch: ${digest} (pinned ${FIXTURE_SHA256})`);
  }
  return JSON.parse(bytes.toString('utf-8')) as Record<string, unknown>;
}

export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
