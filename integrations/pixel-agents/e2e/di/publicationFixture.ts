/**
 * feature13 — the OWNED PUBLICATION FIXTURE for the two README images. SYNTHETIC, never live evidence.
 *
 * It is the realistic fixture (realisticFixture.ts, hash-pinned, unchanged) with ONE difference: each
 * Mission's objective is a short, plausible, obviously illustrative goal ("... the sample ...") instead of
 * the long stress text, so a screenshot can show the objective -> status -> team hierarchy without one
 * 600-character paragraph hiding it. Nothing else is touched: same Missions, same teams, same states, same
 * contract (v1). The long-text stress fixture and every assertion that uses it stay exactly as they are;
 * this variant is ADDITIONAL and is used only by publicationCaptures.ts. No real project, person or
 * repository is named. The product itself still labels the data ("LIVE - FIXTURE DATA") when it is
 * served with --fixture-preview.
 */
import { buildRealisticFixture } from './realisticFixture';

type Raw = Record<string, unknown>;

/** One short goal per Mission, in the realistic fixture's order (eleven Missions). */
export const SAMPLE_GOALS = [
  'Add contract tests for the sample API',
  'Cut start-up time in the sample ingestion service',
  "Move the sample app's settings to one place",
  'Write a rollback guide for the sample release',
  "Tidy the sample project's error messages",
  'Add a health check to the sample worker',
  'Document the sample import flow',
  'Speed up the sample test suite',
  'Split the sample report into smaller parts',
  'Review the sample backup schedule',
  'Check the sample cache expiry rules',
] as const;

export interface PublicationFixture {
  envelopeAt(now: number): Raw;
  missionRefs: string[];
}

export function buildPublicationFixture(): PublicationFixture {
  const base = buildRealisticFixture();
  if (base.missionRefs.length !== SAMPLE_GOALS.length)
    throw new Error(
      `publication fixture expects ${SAMPLE_GOALS.length} Missions, the realistic fixture has ${base.missionRefs.length}`,
    );
  return {
    missionRefs: base.missionRefs,
    envelopeAt: (now) => {
      const envelope = base.envelopeAt(now);
      const missions = (envelope.response as Raw).missions as Raw[];
      missions.forEach((mission, index) => {
        mission.objective = SAMPLE_GOALS[index];
      });
      return envelope;
    },
  };
}
