/**
 * b3 — the office delivery panel, opened from its entry in the office panel, beside the Mission request entry.
 *
 * It renders only what the model (diDeliveryModel.ts) decides; the hook (useDiDelivery.ts) sends only what the model builds.
 * - One delivery id at a time, entered by the person, with no list to choose from: no earlier delivery can be picked here. A notice
 *   names the delivery it is about, so an earlier id can stay visible in a notice (round 55: the panel says so). That is scope and
 *   minimization, not authentication: the delivery's own record and ceremony are the authority.
 * - An eligible step is shown under a visible LOCAL CHECK label, with the adapter's two notes VERBATIM, the exact binding the request
 *   carries, a confirmation box and ONE button, disabled until the box is ticked. No step offered: the reason and its detail, each
 *   naming who established it (the adapter's own verdict, or this window's reading of the answer), and no step control. There is never
 *   a BASE_REFRESH control, and nothing here starts a delivery.
 * - A waiting request is said with ITS delivery: the one shown, or a different one whose request blocks reading this one (round 58).
 * - A refusal, an unknown outcome and a busy refusal are shown with the model's headings; the adapter's and the relay's words are shown
 *   as they came, as text. No control resends an advance; after any advance reply, the status is read again.
 */
import { Fragment } from 'react';

import type { DiDeliveryControl } from '../hooks/useDiDelivery.js';
import { enteredId, pendingLine } from './diDeliveryModel.js';
import { DI_FOCUS_RING } from './diNavigation.js';

export const DL_NAME = 'Delivery';

export function DiDeliveryPanel({ control }: { control: DiDeliveryControl }) {
  const { state } = control;
  const blocked = state.pending !== null || state.unvouched !== null;
  const button = `di-op-button ${DI_FOCUS_RING}`;
  return (
    <div className="di-op-detail-body" data-testid="di-dl-panel">
      <p className="di-op-entry-note">
        Delivery ids are entered one at a time, as each delivery&apos;s ceremony printed it. There is no list to choose from: no earlier
        delivery can be picked in this window. A notice names the delivery it is about, so an earlier id can stay visible in a notice.
      </p>
      <label>
        Delivery id{' '}
        <input
          type="text"
          data-testid="di-dl-id"
          value={state.entered}
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => control.setEntered(event.currentTarget.value)}
        />
      </label>
      <div className="di-op-actions">
        <button
          type="button"
          className={button}
          data-testid="di-dl-read"
          disabled={enteredId(state) === null || blocked}
          onClick={control.readStatus}
        >
          Read the status
        </button>
      </div>
      <DiDeliveryStatus control={control} />
    </div>
  );
}

/**
 * The session's status content, shared by the standalone panel and Mission details (M8), so both draw the same words and controls:
 * the waiting line, the unvouched line, the notices, and the adapter's answer with its LOCAL CHECK block. `onlyAbout` (Mission details):
 * draw only the notices about THAT delivery, and those that name none; a notice about another delivery belongs to another Mission.
 * `buttonClass` is the CALLER'S face's control class: the operational face's by default, where it is styled; Mission details passes
 * the scan face's, its read control's own, because the operational class draws no edge there (measured: diMissionDeliveryComposition.test.ts).
 */
export function DiDeliveryStatus({
  control,
  onlyAbout,
  buttonClass = 'di-op-button',
}: {
  control: DiDeliveryControl;
  onlyAbout?: string;
  buttonClass?: string;
}) {
  const { state } = control;
  const pending = state.pending !== null;
  const blocked = pending || state.unvouched !== null;
  const view = state.view;
  const button = `${buttonClass} ${DI_FOCUS_RING}`;
  const notices =
    onlyAbout === undefined ? state.notices : state.notices.filter((n) => n.deliveryId === null || n.deliveryId === onlyAbout);
  const waiting = pendingLine(state);
  return (
    <>
      {waiting !== null && (
        // it names ITS delivery: the one shown, or a different one whose request blocks this one (round 58)
        <p className="di-op-entry-note" aria-live="polite" data-testid="di-dl-waiting">
          {waiting}
        </p>
      )}
      {state.unvouched !== null && (
        <p className="di-op-entry-note" data-testid="di-dl-unvouched">
          This window cannot vouch for the office right now ({state.unvouched}), so nothing is shown as current and nothing is sent.
        </p>
      )}
      {notices.map((notice) => (
        <div key={notice.id} className="di-op-entry" data-testid="di-dl-notice" data-kind={notice.kind}>
          <p>
            <strong>{notice.heading}</strong>
          </p>
          {/* a kept answer names the delivery it is about, which may not be the id in the field now */}
          {notice.deliveryId !== null && <p data-testid="di-dl-notice-subject">About delivery {notice.deliveryId}</p>}
          {notice.text !== '' && <p>{notice.text}</p>}
          <button type="button" className={button} onClick={() => control.dismiss(notice.id)}>
            Dismiss
          </button>
        </div>
      ))}
      {view !== null && (
        <section data-testid="di-dl-view" aria-label="The delivery status, as the adapter answered it">
          <dl>
            {view.facts.map((fact, index) => (
              <div key={`${index}-${fact.label}`}>
                <dt>{fact.label}</dt>
                <dd>{fact.text}</dd>
              </div>
            ))}
          </dl>
          {view.eligible !== null ? (
            <div data-testid="di-dl-eligible">
              <p>
                <strong>LOCAL CHECK.</strong> Eligible now, as the record states it: {view.eligible.step}.
              </p>
              <p data-testid="di-dl-local-note">{view.localConfirmationNote}</p>
              {view.performsStepsNote !== null && <p data-testid="di-dl-performs-note">{view.performsStepsNote}</p>}
              <dl data-testid="di-dl-binding">
                {(
                  [
                    ['Delivery', view.eligible.delivery_id],
                    ['Revision', String(view.eligible.revision)],
                    ['Candidate digest', view.eligible.candidate_identity_digest_sha256],
                    ['Step', view.eligible.step],
                  ] as const
                ).map(([label, text]) => (
                  <Fragment key={label}>
                    <dt>{label}</dt>
                    <dd>{text}</dd>
                  </Fragment>
                ))}
              </dl>
              <label>
                <input
                  type="checkbox"
                  data-testid="di-dl-confirm"
                  checked={state.confirmed}
                  disabled={blocked}
                  onChange={(event) => control.setConfirmed(event.currentTarget.checked)}
                />{' '}
                I confirm this ONE step, {view.eligible.step}, for this displayed delivery (a local check, not new authority).
              </label>
              <div className="di-op-actions">
                <button
                  type="button"
                  className={button}
                  data-testid="di-dl-advance"
                  disabled={!state.confirmed || blocked}
                  onClick={control.askForStep}
                >
                  Ask the adapter for this one step: {view.eligible.step}
                </button>
              </div>
            </div>
          ) : (
            view.notEligible !== null && (
              <div data-testid="di-dl-not-eligible">
                <p>No step can be confirmed here now ({view.notEligible.reason}).</p>
                <p>{view.notEligible.detail}</p>
              </div>
            )
          )}
        </section>
      )}
    </>
  );
}

export function DiDeliveryEntry({ control }: { control: DiDeliveryControl | null }) {
  if (control === null) return null;
  const { state } = control;
  return (
    <section className="di-op-entry" aria-labelledby="di-dl-entry-title" data-testid="di-dl-entry">
      <div className="di-op-entry-head">
        <h2 id="di-dl-entry-title" className="di-op-entry-title">
          {DL_NAME}
        </h2>
      </div>
      <p className="di-op-entry-note">
        Read one delivery&apos;s status, and ask its delivery adapter for one step you confirm. The delivery&apos;s own authorization,
        from its ceremony, is the only authority.
      </p>
      <div className="di-op-actions">
        <button
          type="button"
          className={`di-op-button ${DI_FOCUS_RING}`}
          data-testid="di-dl-entry-open"
          aria-expanded={state.open}
          onClick={state.open ? control.close : control.open}
        >
          {state.open ? 'Close the delivery panel' : 'Open the delivery panel'}
        </button>
      </div>
      {state.open && <DiDeliveryPanel control={control} />}
    </section>
  );
}
