/**
 * AC5: the sample Mission states, from the ONE labelled fixture file `mission-state-samples.FIXTURE.json`, drawn in
 * the office panel ONLY in an explicit fixture preview (`fixtureMode`, the existing labelling mechanism) and
 * labelled FIXTURE on the section and on every card.
 *
 * These are design samples, not Mission records: nothing reads them as status, nothing here can be acted on (no
 * controls), and no real Mission status is wired from them. A live office never shows them.
 */
import { missionStateSamples, SAMPLE_FIXTURE_ID, SAMPLE_PHASES } from './diSampleStates.js';

export function DiMissionStateSamples({ fixtureMode }: { fixtureMode: boolean }) {
  const shown = missionStateSamples(fixtureMode);
  if (shown.length === 0) return null;
  const phases = SAMPLE_PHASES;
  return (
    <details
      open
      className="di-op-samples"
      data-testid="di-mission-samples"
      data-fixture-id={SAMPLE_FIXTURE_ID}
    >
      <summary className="di-op-section-title" data-testid="di-mission-samples-title">
        Sample Mission states · FIXTURE DATA
      </summary>
      <p className="di-op-muted" data-testid="di-mission-samples-note">
        Design samples from a labelled fixture file. They are not Missions, and nothing here can be
        acted on.
      </p>
      <ul className="di-op-sample-list" aria-label="Sample Mission states, fixture data">
        {shown.map((s) => {
          const at = phases.indexOf(s.phase);
          return (
            <li
              key={s.id}
              className="di-op-sample"
              data-testid="di-mission-sample"
              data-tone={s.tone}
            >
              <div className="di-op-sample-head">
                <span className="di-op-sample-tile" aria-hidden="true">
                  {s.glyph}
                </span>
                <span className="di-op-sample-title" data-testid="di-mission-sample-title">
                  FIXTURE · {s.objective}
                </span>
              </div>
              <div className="di-op-sample-meta">
                <span className="di-op-sample-pill" data-testid="di-mission-sample-state">
                  {s.state}
                </span>
                {s.needsYou && (
                  <span className="di-op-sample-needs" data-testid="di-mission-sample-needs-you">
                    <span aria-hidden="true">▲ </span>Needs you
                  </span>
                )}
              </div>
              <div className="di-op-muted">{s.step}</div>
              <div
                className="di-op-sample-phases"
                role="img"
                aria-label={`Phase: ${s.phase}, step ${at + 1} of ${phases.length}`}
              >
                {phases.map((p, i) => (
                  <span
                    key={p}
                    className="di-op-sample-phase"
                    data-reached={i <= at ? 'true' : 'false'}
                  >
                    <span className="di-op-sample-seg" aria-hidden="true" />
                    <span className="di-op-sample-phase-word" aria-hidden="true">
                      {p}
                    </span>
                  </span>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
