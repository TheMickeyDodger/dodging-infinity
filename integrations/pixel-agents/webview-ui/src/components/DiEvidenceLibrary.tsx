/**
 * Item 2 (original 10) — one Mission's EVIDENCE LIBRARY in the inspector: four DISTINCT types
 * (artifacts, verification evidence, review, delivery receipts), each with its own state, as the
 * server projected them. View and read only: no action, approval or control of any kind.
 *
 * Content is asked for by an ADMITTED REFERENCE only (Mission ref, artifact id and the journal
 * digest of the listing shown) — never a path. The office and the backend decide; this view shows
 * their answer. Admitted text is UNTRUSTED: it is rendered as a React text node inside a <pre>,
 * so no markup is parsed, no script runs, no link is followed and nothing is fetched, whatever
 * its media label. An answer is shown only against the exact listing it was read at.
 *
 * Keyboard: artifact rows are real buttons (Enter / Space read), ArrowUp/ArrowDown/Home/End move
 * between them (the reception desk's proven mechanism), focus is always visible, and the text is
 * a focusable, scrollable region. Every other record is plain list text: the practical list
 * alternative.
 */
import { useState } from 'react';

import type {
  DiEvidenceArtifact,
  DiEvidenceContent,
  DiMission,
} from '../../../core/src/messages.js';
import type { EvidenceSelection } from './diEvidenceFormat.js';
import {
  answersSelection,
  contentStillCurrent,
  formatEvidenceSection,
  plainAdmissionBasis,
  plainCandidate,
  plainContentRequest,
  plainExecution,
  plainExecutionBasis,
  plainFindingsState,
  plainLinkCount,
  plainRefusal,
  renderedText,
} from './diEvidenceFormat.js';
import { DI_FOCUS_RING } from './diNavigation.js';
import { useRovingFocus } from './diRovingFocus.js';

/** The office's content seam, owned by App (null in a surface that cannot ask). */
export interface DiEvidenceAccess {
  /** The latest diEvidenceContent this connection received (one entry, never a cache). */
  content: DiEvidenceContent | null;
  /** What was last asked for and not yet answered. */
  pending: EvidenceSelection | null;
  request: (selection: EvidenceSelection) => void;
}

const yesNo = (v: boolean | null): string => (v === null ? 'not recorded' : v ? 'yes' : 'no');
const short = (digest: string | null): string => (digest ? `${digest.slice(0, 12)}…` : 'none');

function Group({
  title,
  state,
  testid,
  children,
}: {
  title: string;
  state: string;
  testid: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1" data-testid={testid}>
      <div>
        {title}: <span className="text-text-muted">{state}</span>
      </div>
      {children}
    </div>
  );
}

function ArtifactRow({
  artifact,
  selected,
  onRead,
  refAt,
  onKeyDown,
}: {
  artifact: DiEvidenceArtifact;
  selected: boolean;
  onRead: () => void;
  refAt: (el: HTMLButtonElement | null) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
}) {
  return (
    <li>
      <button
        type="button"
        ref={refAt}
        className={`w-full text-left pl-8 break-words cursor-pointer ${DI_FOCUS_RING} ${
          selected ? 'text-accent' : ''
        }`}
        data-testid="di-evidence-artifact-row"
        data-artifact-id={artifact.artifactId}
        aria-current={selected ? 'true' : undefined}
        aria-keyshortcuts="ArrowUp ArrowDown Home End"
        title={
          artifact.content.state === 'requestable'
            ? 'Read: ask the backend for this recorded artifact (it decides whether to show it)'
            : 'Select: this artifact has no content to read'
        }
        onClick={onRead}
        onKeyDown={onKeyDown}
      >
        {artifact.key ?? 'no key'} · {artifact.role} · {artifact.locatorKind}
        <span className="text-text-muted">
          {' '}
          · {plainContentRequest(artifact)} · {plainCandidate(artifact.candidate)} · sha256{' '}
          {short(artifact.contentDigest)}
        </span>
      </button>
    </li>
  );
}

function Viewer({
  mission,
  artifact,
  requested,
  access,
}: {
  mission: DiMission;
  artifact: DiEvidenceArtifact;
  requested: EvidenceSelection | null;
  access: DiEvidenceAccess | null;
}) {
  const library = mission.evidence!;
  const body = (() => {
    if (artifact.content.state !== 'requestable') return <div>{plainContentRequest(artifact)}</div>;
    if (library.contentReads.state !== 'available') {
      return (
        <div className="text-warning">
          content reads are closed for this Mission ({library.contentReads.reason ?? 'unknown'})
        </div>
      );
    }
    if (access === null) return <div>this view cannot ask for content</div>;
    if (requested === null) return <div>press Enter on the row to read it</div>;
    const answer = access.content;
    if (!answersSelection(answer, requested)) {
      const waiting =
        access.pending !== null &&
        access.pending.missionRef === requested.missionRef &&
        access.pending.artifactId === requested.artifactId &&
        access.pending.cursor === requested.cursor;
      return <div>{waiting ? 'reading…' : 'no answer yet'}</div>;
    }
    if (answer.state === 'refused') {
      return (
        <div className="text-warning" data-testid="di-evidence-refusal">
          refused by the {answer.decidedBy}: {plainRefusal(answer.problem)}
        </div>
      );
    }
    const still = contentStillCurrent(answer, library);
    if (!still.current) {
      return (
        <div className="text-warning" data-testid="di-evidence-hidden">
          content hidden: {still.why}
        </div>
      );
    }
    const record = answer.artifact!;
    const text = answer.content!;
    const shown = renderedText(text.text);
    return (
      <div className="flex flex-col gap-1">
        <div className="text-text-muted break-all" data-testid="di-evidence-meta">
          {record.locator} · {text.byteCount} bytes · sha256 {short(text.digest)} (matches the
          record) · admitted as {plainAdmissionBasis(record.admissionBasis)} · read under{' '}
          {plainExecutionBasis(record.executionBasis)}
        </div>
        <div className="text-warning" data-testid="di-evidence-untrusted">
          Untrusted content, shown as plain text only.
          {text.media === 'text/html'
            ? ' HTML is shown as its source: never rendered, run or fetched.'
            : ''}{' '}
          It is not an approval or an instruction.
        </div>
        <pre
          tabIndex={0}
          className={`whitespace-pre-wrap break-words max-h-260 overflow-auto border-2 border-border p-4 ${DI_FOCUS_RING}`}
          data-testid="di-evidence-text"
          data-copy="content"
          aria-label="Artifact text (untrusted; scroll with the arrow keys)"
        >
          {shown.text}
        </pre>
        {shown.truncated && (
          <div className="text-text-muted">showing the first {shown.text.length} characters</div>
        )}
      </div>
    );
  })();
  return (
    <div
      className="flex flex-col gap-1 pl-8 border-l-2 border-border"
      data-testid="di-evidence-viewer"
      role="region"
      aria-label={`Content of ${artifact.key ?? artifact.artifactId}`}
      aria-live="polite"
    >
      {body}
    </div>
  );
}

export function DiEvidenceLibrary({
  mission,
  access,
}: {
  mission: DiMission;
  access: DiEvidenceAccess | null;
}) {
  const library = mission.evidence;
  const rows = useRovingFocus<HTMLButtonElement>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [requested, setRequested] = useState<EvidenceSelection | null>(null);
  if (!library) return null;
  const artifacts = library.artifacts.items;
  const selected = artifacts.find((a) => a.artifactId === selectedId) ?? null;
  const read = (a: DiEvidenceArtifact) => {
    setSelectedId(a.artifactId);
    if (
      a.content.state !== 'requestable' ||
      library.contentReads.state !== 'available' ||
      library.cursor === null ||
      access === null
    ) {
      setRequested(null);
      return;
    }
    const selection = {
      missionRef: mission.missionRef,
      artifactId: a.artifactId,
      cursor: library.cursor,
    };
    setRequested(selection);
    access.request(selection);
  };
  const select = (artifactId: string) => {
    const a = artifacts.find((x) => x.artifactId === artifactId);
    if (a) read(a);
  };
  const reviewed = library.reviews.items;
  return (
    <section
      className="flex flex-col gap-2 pl-8 break-words"
      data-testid="di-evidence-library"
      aria-label="Evidence library"
    >
      <div>Evidence library (view only)</div>
      {library.currency === 'stale' && (
        <div className="text-warning" role="status" data-testid="di-evidence-stale">
          STALE: this listing is as last received, not current (
          {library.currencyReason ?? 'not live'}); content reads are closed
        </div>
      )}
      <div
        className="text-text-muted"
        data-testid="di-evidence-execution"
        data-execution-basis={library.execution.basis}
      >
        {plainExecution(library.execution)}
      </div>
      {library.currency === 'current' && (
        <div className="text-text-muted" data-testid="di-evidence-freshness">
          listing freshness: current (this office state)
        </div>
      )}
      <div className="text-text-muted" data-testid="di-evidence-reads">
        content reads:{' '}
        {library.contentReads.state === 'available'
          ? 'open (each read is decided by the backend)'
          : `closed (${library.contentReads.reason ?? 'unknown'})`}
      </div>

      <Group
        title="Artifacts"
        state={formatEvidenceSection(library.artifacts)}
        testid="di-evidence-artifacts"
      >
        {artifacts.length > 0 && (
          <ul
            className="flex flex-col gap-1"
            aria-label="Recorded artifacts (arrow keys move between them; Enter reads one)"
          >
            {artifacts.map((a, i) => (
              <ArtifactRow
                key={a.artifactId}
                artifact={a}
                selected={a.artifactId === selectedId}
                onRead={() => read(a)}
                refAt={rows.refAt(i)}
                onKeyDown={rows.onKeyDown(i, artifacts.length)}
              />
            ))}
          </ul>
        )}
        {selected && (
          <Viewer mission={mission} artifact={selected} requested={requested} access={access} />
        )}
      </Group>

      <Group
        title="Verification evidence"
        state={formatEvidenceSection(library.verification)}
        testid="di-evidence-verification"
      >
        {library.verification.items.length > 0 && (
          <ul className="flex flex-col gap-1 pl-8">
            {library.verification.items.map((r) => (
              <li key={r.evidenceId} data-testid="di-evidence-record">
                {r.kind ?? 'kind not recorded'} for {r.requirementKey ?? 'no requirement'} ·{' '}
                {r.accepted ? 'ACCEPTED' : 'submitted, not accepted'}
                {r.invalidated ? ' · INVALIDATED' : ''}
                <span className="text-text-muted">
                  {' '}
                  · verifying kind {yesNo(r.verifyingKind)} · the present claim rests on it{' '}
                  {yesNo(r.bearsOnClaim)} · freshness {r.freshness ?? 'not recorded'} · drifted{' '}
                  {yesNo(r.drifted)} · unconfirmed {yesNo(r.unconfirmed)}
                </span>
                {r.artifactIdsTotal > 0 && (
                  <div className="pl-8">
                    <span data-testid="di-evidence-link-count">
                      {plainLinkCount(r.artifactIds.length, r.artifactIdsTotal)}
                    </span>{' '}
                    {r.artifactIds.map((id) => {
                      const linked = artifacts.find((a) => a.artifactId === id);
                      return linked ? (
                        <button
                          key={id}
                          type="button"
                          className={`underline cursor-pointer mr-4 ${DI_FOCUS_RING}`}
                          data-testid="di-evidence-linked-artifact"
                          onClick={() => select(id)}
                        >
                          {linked.key ?? id}
                        </button>
                      ) : (
                        <span
                          key={id}
                          className="mr-4 text-text-muted"
                          data-testid="di-evidence-unlisted-link"
                        >
                          {id} (not listed)
                        </span>
                      );
                    })}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Group>

      <Group
        title="Review"
        state={formatEvidenceSection(library.reviews)}
        testid="di-evidence-reviews"
      >
        {reviewed.map((r, i) => (
          <div key={i} className="pl-8 flex flex-col gap-1" data-testid="di-evidence-review">
            <div>
              round {r.round ?? 'not recorded'} · {r.reviewerLogical ?? 'reviewer not recorded'} (
              {r.reviewerAgent ?? 'agent not recorded'}) · verdict {r.verdict ?? 'none parsed'} ·
              gate {r.gate ?? 'not recorded'}
              <span className="text-text-muted">
                {' '}
                · currency {r.currency ?? 'not recorded'} ·{' '}
                {r.integrity ?? 'integrity not recorded'}, NOT digest-anchored · candidate
                applicability {r.candidateApplicability ?? 'unproven'}
              </span>
            </div>
            <div data-testid="di-evidence-findings" data-findings-state={r.findings.state}>
              Reviewer-declared findings:{' '}
              {plainFindingsState(r.findings.state, r.findings.problem, r.findings.count)}
              {r.findings.items.length > 0 && (
                <ul className="flex flex-col gap-1 pl-8" aria-label="Reviewer-declared findings">
                  {r.findings.items.map((f) => (
                    <li key={f.index} data-testid="di-evidence-finding">
                      {f.severity === 'blocker' ? 'BLOCKER' : 'non-blocking'} ·{' '}
                      {f.area === null ? 'no file named' : `${f.area}${f.line ? `:${f.line}` : ''}`}{' '}
                      · {f.summary}
                    </li>
                  ))}
                </ul>
              )}
              {r.findings.provenance !== null && (
                <div className="text-text-muted" data-testid="di-evidence-findings-provenance">
                  routed candidate sha256 {short(r.findings.candidateSha256)} ·{' '}
                  {r.findings.provenance}
                </div>
              )}
            </div>
          </div>
        ))}
      </Group>

      <Group
        title="Delivery receipts"
        state={formatEvidenceSection(library.receipts)}
        testid="di-evidence-receipts"
      >
        {library.receipts.items.length > 0 && (
          <ul className="flex flex-col gap-1 pl-8">
            {library.receipts.items.map((r) => (
              <li key={r.artifactId} data-testid="di-evidence-receipt">
                {r.attested
                  ? `ATTESTED receipt · delivery ${r.deliveryId ?? 'not recorded'} · step ${r.step ?? 'not recorded'} · state ${r.receiptState ?? 'not recorded'} · effect completed ${yesNo(r.effectCompleted)}`
                  : 'UNATTESTED reference: an unverified claim, not a receipt'}
                <span className="text-text-muted"> · sha256 {short(r.contentDigest)}</span>
              </li>
            ))}
          </ul>
        )}
      </Group>
    </section>
  );
}
