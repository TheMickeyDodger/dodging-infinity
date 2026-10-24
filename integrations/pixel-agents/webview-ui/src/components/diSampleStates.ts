/**
 * AC5: the sample Mission states of the ONE labelled fixture file `mission-state-samples.FIXTURE.json`, given out
 * only in an explicit fixture preview (`fixtureMode`). Design samples, not Mission records: no status is read from
 * them and none is wired.
 */
import samples from './mission-state-samples.FIXTURE.json';

export type SampleTone = 'attention' | 'running' | 'failed' | 'good';

export interface MissionStateSample {
  id: string;
  state: string;
  glyph: string;
  tone: SampleTone;
  needsYou: boolean;
  objective: string;
  step: string;
  phase: string;
}

/** The fixture's own id and phase names. */
export const SAMPLE_FIXTURE_ID: string = samples.fixture_id;
export const SAMPLE_PHASES: readonly string[] = samples.phases;

/** The samples to draw: all of them in a fixture preview, none otherwise. */
export function missionStateSamples(fixtureMode: boolean): readonly MissionStateSample[] {
  return fixtureMode ? (samples.samples as MissionStateSample[]) : [];
}
