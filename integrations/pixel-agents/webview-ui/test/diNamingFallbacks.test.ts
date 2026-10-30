/**
 * The naming remainder: the two operational primary-name fallbacks, the inspector's heading and the Mission picker, under the
 * already-authorized plain-English naming rule (owner ruling, 2026-10-28). A Mission whose source reported NO goal is named in plain
 * words, exactly "No goal reported", because the available facts support no other name; nothing is invented for the missing goal.
 * The technical id stays SECONDARY: the inspector draws the short source-qualified ref on its own line under the heading, and the
 * picker puts it in parentheses after the name, the shape it already has for every Mission with a goal (so lookalikes stay apart).
 * A Mission with a goal is unchanged on both. Static markup and pure functions only: no browser, no network, no process.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import * as panelModule from '../src/components/DiOfficePanel.tsx';
import * as summaryModule from '../src/components/diPlainSummary.ts';
import { missionTitle, shortMissionRefs } from '../src/components/diPlainSummary.ts';
import type { OfficeState } from '../src/office/engine/officeState.ts';

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Fn = (...args: any[]) => any; // eslint-disable-line @typescript-eslint/no-explicit-any

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = (
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as {
    state: DiOfficeState;
  }
).state;
const missions = fixture.missions.missions;
const noGoal = missions.filter((m) => m.objective.value === null);
const withGoal = missions.filter((m) => m.objective.value !== null && missionTitle(m).kind === 'sentence');
const refs = shortMissionRefs(missions);
const NO_GOAL = 'No goal reported';

const officeStub = {
  characters: new Map<number, unknown>(),
  selectedAgentId: null,
  cameraFollowId: null,
  areaMappings: {},
  getLayout: () => ({ areas: [] }),
} as unknown as OfficeState;

/** A named export the correction adds to diPlainSummary.ts (the inspector calls it): its absence FAILS (an assertion), never errors. */
const need = (name: string): Fn => {
  expect(typeof (summaryModule as unknown as Any)[name], `${name} is exported`).toBe('function');
  return (summaryModule as unknown as Any)[name] as Fn;
};

const escape = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
const unescape = (s: string): string =>
  s.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&');

/** The picker, drawn once from the fixture, and one option's visible text by its Mission ref. */
const html = renderToStaticMarkup(
  createElement(panelModule.DiOfficePanel, {
    state: structuredClone(fixture),
    officeState: officeStub,
    collapsed: false,
    onCollapsedChange: () => {},
  }),
);
const optionText = (ref: string): string | null => {
  const at = html.indexOf(`<option value="${escape(ref)}">`);
  if (at < 0) return null;
  const start = at + `<option value="${escape(ref)}">`.length;
  return unescape(html.slice(start, html.indexOf('</option>', start)));
};

describe('the fixture holds both kinds of Mission (non-empty guards)', () => {
  it('Missions with no goal reported, and Missions with a goal sentence', () => {
    expect(noGoal.length).toBeGreaterThan(0);
    expect(withGoal.length).toBeGreaterThan(0);
  });
});

describe('the Mission picker', () => {
  it('names a Mission with no goal reported in plain words, its short ref in parentheses', () => {
    expect(html).toContain('data-testid="di-mission-select"');
    for (const m of noGoal) {
      expect(optionText(m.missionRef), m.missionRef).toBe(`${NO_GOAL} (${refs.get(m.missionRef)})`);
    }
  });

  it('keeps a Mission with a goal as its title, then its short ref in parentheses (unchanged)', () => {
    expect(html).toContain('data-testid="di-mission-select"');
    for (const m of withGoal) {
      expect(optionText(m.missionRef), m.missionRef).toBe(`${missionTitle(m).text} (${refs.get(m.missionRef)})`);
    }
  });
});

describe("the inspector's heading for a Mission", () => {
  it('is exactly "No goal reported" when the source reported none: no id in the heading', () => {
    const inspectorHeading = need('inspectorHeading');
    for (const m of noGoal) {
      const heading = inspectorHeading(fixture, null, m);
      expect(heading, m.missionRef).toBe(NO_GOAL);
      expect(m.nativeId.length, m.missionRef).toBeGreaterThan(0);
      expect(heading, m.missionRef).not.toContain(m.nativeId);
    }
  });

  it('is the Mission title when there is a goal (unchanged)', () => {
    const inspectorHeading = need('inspectorHeading');
    for (const m of withGoal) {
      expect(inspectorHeading(fixture, null, m), m.missionRef).toBe(missionTitle(m).text);
    }
  });
});
