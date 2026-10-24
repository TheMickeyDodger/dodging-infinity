/**
 * AC5: the sample Mission states live in exactly ONE labelled fixture file and are given out only in an explicit
 * fixture preview (`fixtureMode`); their copy is short and plain, with no em or en dash and no number of any kind.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { missionStateSamples, SAMPLE_FIXTURE_ID, SAMPLE_PHASES } from '../src/components/diSampleStates.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const STATES = ['Proposal ready', 'Building', 'Question for you', 'Stalled', 'Ready to deliver', 'Merged'];

describe('sample Mission states', () => {
  it('a live office gets none; a fixture preview gets exactly the six states, in order', () => {
    expect(missionStateSamples(false)).toEqual([]);
    expect(missionStateSamples(true).map((s) => s.state)).toEqual(STATES);
    expect(SAMPLE_FIXTURE_ID).toBe('mission-state-samples-v1');
    expect(SAMPLE_PHASES).toEqual(['Plan', 'Build', 'Review', 'Delivery']);
  });
  it('exactly one labelled fixture file in the UI source holds them', () => {
    const fixtures: string[] = [];
    const walk = (dir: string) => {
      for (const name of fs.readdirSync(dir)) {
        const p = path.join(dir, name);
        if (fs.statSync(p).isDirectory()) walk(p);
        else if (name.endsWith('.FIXTURE.json')) fixtures.push(path.relative(root, p));
      }
    };
    walk(path.join(root, 'src'));
    expect(fixtures).toEqual(['src/components/mission-state-samples.FIXTURE.json']);
    const raw = JSON.parse(fs.readFileSync(path.join(root, fixtures[0]), 'utf-8')) as { fixture: boolean; label: string };
    expect(raw.fixture).toBe(true);
    expect(raw.label).toMatch(/^FIXTURE \/ VISUAL TEST/);
  });
  it('the sample copy is short and plain: no em or en dash, and no invented number', () => {
    for (const s of missionStateSamples(true)) {
      for (const text of [s.state, s.objective, s.step, s.phase]) {
        expect(text, text).not.toMatch(/[–—]/);
        expect(text, text).not.toMatch(/\d/);
        expect(text.length, text).toBeLessThanOrEqual(48);
      }
    }
  });
  it('each sample needing the owner says so; the others do not', () => {
    const needs = Object.fromEntries(missionStateSamples(true).map((s) => [s.state, s.needsYou]));
    expect(needs).toEqual({
      'Proposal ready': true,
      Building: false,
      'Question for you': true,
      Stalled: true,
      'Ready to deliver': true,
      Merged: false,
    });
  });
});
