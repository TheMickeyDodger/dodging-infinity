/// <reference types="vite/client" />
/**
 * FIXTURE / TEST ONLY. Mounts the REAL DI office panel (`DiOfficePanel`) over the labelled office
 * state that `server/__tests__/diOfficeStateExport.test.ts` projects from a packaged fixture, with
 * a minimal stand-in for the office canvas state (the panel reads only the fields below). The
 * browser regression (`test/diPanelKeyboard.test.ts`) drives it with real key presses.
 */
import '../../src/index.css';

import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

import type { DiEvidenceArtifact, DiEvidenceContent, DiOfficeState } from '../../../core/src/messages.ts';
import type { DiCodeReviewAccess } from '../../src/components/DiCodeReviewEntry.tsx';
import type { EvidenceSelection } from '../../src/components/diEvidenceFormat.ts';
import type { DiEvidenceAccess } from '../../src/components/DiEvidenceLibrary.tsx';
import { DiMinimizedTrayContext } from '../../src/components/diMinimizedTrayContext.ts';
import type { DiMissionRequestAccess } from '../../src/components/DiMissionRequestEntry.tsx';
import type { MrOverview } from '../../src/components/diMissionRequestModel.ts';
import { INITIAL_STATE } from '../../src/components/diMissionRequestModel.ts';
import { DiOfficePanel } from '../../src/components/DiOfficePanel.tsx';
import { useDiReceptionInbox } from '../../src/components/diReceptionInboxState.ts';
import { DI_MINIMIZED_STRIP_WIDTH_PX } from '../../src/constants.ts';
import { diDockFor } from '../../src/diPanelLayout.ts';
import { useViewportWidth } from '../../src/hooks/useViewportWidth.ts';
import type { OfficeState } from '../../src/office/engine/officeState.ts';
import fixture from '../fixtures/di-office-state-session-mismatch-v2.FIXTURE.json';

const initial: DiOfficeState = {
  ...(fixture.state as unknown as DiOfficeState),
  fixtureMode: true,
};

/**
 * OPT-IN evidence seam (`?evidence=1`; every other page load is unchanged): two Missions get DIFFERENT,
 * labelled SYNTHETIC artifact lists, and the panel gets an evidence reader that RECORDS every content
 * request (source-qualified missionRef, artifactId, cursor) and answers it with admitted text that names
 * exactly what was asked, the way the office answers a read. The two Missions are the reception entry's
 * Mission (A: it has drawn members) and the first Mission with no drawn member (B). Nothing here is live
 * Mission data; it exists so a route test can tell WHOSE evidence a view reached.
 */
const WITH_EVIDENCE = new URLSearchParams(window.location.search).get('evidence') === '1';
interface SyntheticArtifact {
  artifactId: string;
  key: string;
  digest: string;
  role: 'PRODUCED' | 'VERIFICATION';
}
interface SyntheticEvidence {
  missionRef: string;
  cursor: string;
  artifacts: SyntheticArtifact[];
}
const synthetic: { A: SyntheticEvidence; B: SyntheticEvidence } | null = (() => {
  if (!WITH_EVIDENCE) return null;
  const missions = initial.missions.missions;
  const entryRef = initial.reception!.entries[0].missionRef;
  const a = missions.find((m) => m.missionRef === entryRef)!;
  const b = missions.find((m) => m.members.members.every((x) => x.characterId === null))!;
  const build = (label: 'A' | 'B', hex: string): SyntheticEvidence['artifacts'] =>
    (['report', 'log'] as const).map((kind, i) => ({
      artifactId: `mf-synthetic-${label}-000${i + 1}`,
      key: `synthetic_${label}_${kind}`,
      digest: hex.repeat(32),
      role: i === 0 ? 'PRODUCED' : 'VERIFICATION',
    }));
  const graft = (m: typeof a, label: 'A' | 'B', hex: string): SyntheticEvidence => {
    const artifacts = build(label, hex);
    const cursor = `${label.toLowerCase()}c`.repeat(32);
    const ev = m.evidence!;
    ev.cursor = cursor;
    ev.currency = 'current';
    ev.contentReads = { state: 'available', reason: null };
    ev.artifacts = {
      state: 'observed',
      reason: null,
      total: artifacts.length,
      items: artifacts.map(
        (x): DiEvidenceArtifact => ({
          artifactId: x.artifactId,
          key: x.key,
          role: x.role,
          locatorKind: 'REPOSITORY_PATH',
          contentDigest: x.digest,
          availableReported: true,
          recordedAt: 1800000000,
          candidate: 'not_observed',
          content: { state: 'requestable', reason: null },
        }),
      ),
    };
    return { missionRef: m.missionRef, cursor, artifacts };
  };
  return { A: graft(a, 'A', 'a1'), B: graft(b, 'B', 'b2') };
})();
const evidenceRequests: EvidenceSelection[] = [];

/** The answer the office would give for exactly this selection: admitted text naming what was asked. */
function evidenceAnswer(sel: EvidenceSelection): DiEvidenceContent {
  const spec = [synthetic!.A, synthetic!.B].find((e) => e.missionRef === sel.missionRef);
  const art = spec?.artifacts.find((x) => x.artifactId === sel.artifactId);
  const text = `SYNTHETIC evidence text | mission=${sel.missionRef} | artifact=${sel.artifactId} | cursor=${sel.cursor}`;
  return {
    type: 'diEvidenceContent',
    missionRef: sel.missionRef,
    artifactId: sel.artifactId,
    cursor: sel.cursor,
    state: 'admitted',
    decidedBy: 'backend',
    problem: null,
    detail: null,
    artifact: {
      key: art?.key ?? null,
      role: art?.role ?? 'PRODUCED',
      locatorKind: 'REPOSITORY_PATH',
      locator: `synthetic/${art?.key ?? 'unknown'}.txt`,
      contentDigest: art?.digest ?? '',
      recordedAt: 1800000000,
      admissionBasis: ['contract_required_artifact'],
      executionBasis: 'active_binding',
    },
    content: { text, byteCount: text.length, digest: art?.digest ?? '', media: 'text/plain' },
    observedAt: 1800000001,
  } as DiEvidenceContent;
}

function drawnIds(state: DiOfficeState): number[] {
  const ids: number[] = [];
  for (const m of state.missions.missions)
    for (const member of m.members.members)
      if (member.characterId !== null) ids.push(member.characterId);
  for (const a of state.runtimeAgents?.agents ?? [])
    if (a.characterId !== null) ids.push(a.characterId);
  return ids;
}

/** The fields of the office canvas state the panel reads; everything else is absent. */
const office = {
  characters: new Map<number, unknown>(drawnIds(initial).map((id) => [id, {}])),
  selectedAgentId: null as number | null,
  cameraFollowId: null as number | null,
  areaMappings: {} as Record<string, string[]>,
  getLayout: () => ({ areas: [] as Array<{ label: string; color: string }> }),
};

/**
 * OPT-IN P2-A seam (`?p2a=1`; every other page load is unchanged). On top of the labelled office state it
 * adds, all SYNTHETIC and labelled so: a Mission Core pending decision (revision 2) on the fixture's
 * Mission Core Mission; Grok delivery proposals in each state; and a request-panel overview holding a
 * conversation awaiting an answer, a legacy conversation, the request offering that SAME decision, an
 * approval check and a result to verify. The panel's two routes record what they were asked for.
 */
const WITH_P2A = new URLSearchParams(window.location.search).get('p2a') === '1';
const P2A = (() => {
  if (!WITH_P2A) return null;
  const rec = initial.reception!;
  const core = initial.missions.missions.find((m) => m.sourceKind === 'mission_core')!;
  const base = rec.entries.find((e) => e.missionRef === core.missionRef)!;
  rec.entries = [
    ...rec.entries,
    {
      ...structuredClone(base),
      key: JSON.stringify(['authorization', core.sourceId, core.nativeId]),
      signature: 'p2a-synthetic-decision',
      category: 'authorization',
      causes: [
        {
          cause: 'awaiting_decision',
          label: 'Mission Core: this Mission awaits a human decision',
          details: [
            { label: 'awaiting decision', value: 'true' },
            { label: 'revision', value: '2' },
          ],
          staleReasons: [],
        },
      ],
    },
  ];
  rec.entriesTotal = rec.entries.length;
  const digest = (c: string) => c.repeat(64);
  const delivery = (
    n: number,
    state: string,
    over: Partial<NonNullable<typeof rec.sourceEntries>[number]> = {},
  ) => ({
    key: JSON.stringify(['delivery_decision', 'grok_delivery_presentations', digest(String(n))]),
    signature: `p2a-${n}`,
    category: 'delivery_decision' as const,
    sourceId: 'grok_delivery_presentations',
    proposalDigest: digest(String(n)),
    state,
    needsYou: state === 'awaiting_no_recorded_authorization',
    presentedAt: 1_800_000_000,
    expiresAt: 4_100_000_000,
    mode: 'pull_request',
    repositoryUrl: `https://github.com/synthetic/repo-${n}`,
    workflowId: null,
    matchingDeliveries: state === 'unknown' ? null : state === 'authorized' ? 1 : 0,
    linkState: 'unlinked_no_mission_binding',
    missionRef: null,
    authorizationCheck: 'not_comparable',
    current: true,
    staleReasons: [],
    route: 'Outside this office: the proposal was presented through the Grok Bot route.',
    ...over,
  });
  rec.sourceEntries = [
    delivery(1, 'awaiting_no_recorded_authorization', {
      workflowId: 'wf-synthetic',
      linkState: 'unlinked_no_registered_mission',
    }),
    delivery(2, 'awaiting_no_recorded_authorization', {
      workflowId: core.nativeId,
      linkState: 'linked',
      missionRef: core.missionRef,
    }),
    delivery(3, 'authorized'),
    delivery(4, 'expired_without_authorization', { expiresAt: 1_800_000_100 }),
    delivery(5, 'unknown'),
  ];
  rec.sourceEntriesTotal = rec.sourceEntries.length;
  rec.sourceEntriesCap = 16;
  rec.sourceCoverage = [
    {
      sourceId: 'grok_delivery_presentations',
      label: 'Delivery proposals (Grok Bot route)',
      outcome: 'present',
      reason: null,
      statement:
        'SYNTHETIC. Covers proposals presented through the Grok Bot route only, the most recent 16 that route keeps. A proposal presented directly from the command line (present-dots) is not stored, so it cannot appear here: a missing entry is not proof that no delivery is awaiting a decision.',
      total: rec.sourceEntries.length,
    },
  ];
  const cat = rec.categories.find((c) => c.category === 'delivery_decision')!;
  cat.outcome = 'present';
  cat.reason = null;
  cat.entriesTotal = rec.sourceEntries.length;
  return {
    conversationId: `cv-${'c'.repeat(32)}`,
    legacyConversationId: `cv-${'e'.repeat(32)}`,
    decisionRequest: `lr-${'d'.repeat(32)}`,
    checkRequest: `lr-${'a'.repeat(32)}`,
    verifyRequest: `lr-${'f'.repeat(32)}`,
    coreMissionRef: core.missionRef,
    coreNativeId: core.nativeId,
    deliveryDigests: [1, 2, 3, 4, 5].map((n) => digest(String(n))),
  };
})();

function p2aOverview(): MrOverview | null {
  if (P2A === null) return null;
  const summary = (requestRef: string, missionId: string, actions: string[], label: string) => ({
    requestRef,
    missionId,
    revision: 2,
    createdAt: 5,
    objective: `SYNTHETIC request ${requestRef.slice(3, 7)}`,
    digest: 'b'.repeat(64),
    stage: 'of_record',
    label,
    terminal: false,
    actions: actions as MrOverview['requests'][number]['actions'],
    notice: null,
    attestedAt: null,
    cancelledAt: null,
  });
  return {
    serverNow: 1_800_000_000,
    capabilities: {} as MrOverview['capabilities'],
    requestsTruncated: false,
    requests: [
      summary(P2A.decisionRequest, P2A.coreNativeId, ['decide'], 'Waiting for your decision'),
      summary(P2A.checkRequest, 'mn-synthetic-other-1', ['check_approval'], 'Approval recorded'),
      summary(P2A.verifyRequest, 'mn-synthetic-other-2', ['verify'], 'Result ready'),
    ],
    conversations: [
      {
        conversationId: P2A.conversationId,
        turns: 2,
        lastActivity: 9,
        firstWords: 'SYNTHETIC: make the probe stable',
        awaitingAnswer: true,
        questionsReporting: 'read',
        questionsProblem: null,
        awaitingTurnId: `ct-${'b'.repeat(32)}`,
        awaitingQuestions: ['Which branch should this change?', 'Which tests prove it?'],
        latestTurnState: 'COMPLETED',
      },
      {
        conversationId: P2A.legacyConversationId,
        turns: 1,
        lastActivity: 3,
        firstWords: 'SYNTHETIC: an older conversation',
        awaitingAnswer: false,
        questionsReporting: 'not_reported',
        questionsProblem: null,
        awaitingTurnId: null,
        awaitingQuestions: [],
        latestTurnState: 'COMPLETED',
      },
    ],
  };
}

/**
 * FIXTURE stand-ins for the App's two entry seams, recording what a person's action reached: the live
 * code review (offered per member by the office state) and the Mission request workspace (configured:
 * availability "ok"). They open nothing themselves; the App's own tests cover the dialogs they open.
 */
const calls = {
  review: [] as Array<{
    missionRef: string;
    memberRef: string;
    taskId: string;
    listing: string;
    openerTestId: string | null;
  }>,
  mission: [] as Array<{ tab: string; openerTestId: string | null }>,
  /** What the inbox's routes into the request panel were asked for (P2A seam). */
  panel: [] as Array<{ route: 'conversation' | 'request'; id: string; openerTestId: string | null }>,
};
const review: DiCodeReviewAccess = {
  open: (target, opener) =>
    calls.review.push({
      missionRef: target.missionRef,
      memberRef: target.memberRef,
      taskId: target.taskId,
      listing: target.listing,
      openerTestId: opener.getAttribute('data-testid'),
    }),
};
const missionAccess: DiMissionRequestAccess = {
  state: { ...INITIAL_STATE, availability: { kind: 'ok' }, overview: p2aOverview() },
  open: (tab, opener) =>
    calls.mission.push({ tab, openerTestId: opener.getAttribute('data-testid') }),
  openConversation: (id, opener) =>
    calls.panel.push({ route: 'conversation', id, openerTestId: opener.getAttribute('data-testid') }),
  openRequest: (id, opener) =>
    calls.panel.push({ route: 'request', id, openerTestId: opener.getAttribute('data-testid') }),
};

/**
 * OPT-IN (`?strip=1`; every other page load is unchanged): the edge strip of minimized surfaces, mounted and
 * provided exactly as App does (AC3), so a test can minimize several surfaces into it at once.
 */
const WITH_STRIP = new URLSearchParams(window.location.search).get('strip') === '1';
/** OPT-IN (`?face=scan`): the product's scan face. Every other page load stays the operational list. */
const SCAN_FACE = new URLSearchParams(window.location.search).get('face') === 'scan';

const listeners = new Set<(next: DiOfficeState) => void>();
/** The reception inbox seam of the mounted harness, so a test can open it where no entry is drawn (a collapsed sidebar). */
const inbox: { open: (() => void) | null; close: (() => void) | null; collapsed: boolean } = {
  open: null,
  close: null,
  collapsed: false,
};

export function Harness() {
  const [state, setState] = useState(initial);
  // `?collapsed=1` starts with the sidebar collapsed (the panel then draws only its "show" button).
  const [collapsed, setCollapsed] = useState(
    () => new URLSearchParams(window.location.search).get('collapsed') === '1',
  );
  inbox.collapsed = collapsed;
  const [evidenceContent, setEvidenceContent] = useState<DiEvidenceContent | null>(null);
  const [evidencePending, setEvidencePending] = useState<EvidenceSelection | null>(null);
  const evidenceAccess: DiEvidenceAccess | null = WITH_EVIDENCE
    ? {
        content: evidenceContent,
        pending: evidencePending,
        request: (selection) => {
          evidenceRequests.push({ ...selection });
          setEvidencePending(selection);
          setEvidenceContent(evidenceAnswer(selection));
        },
      }
    : null;
  const receptionInbox = useDiReceptionInbox();
  inbox.open = () => receptionInbox.openFrom(null);
  inbox.close = receptionInbox.close;
  const [strip, setStrip] = useState<HTMLDivElement | null>(null);
  // the same docked layout as the App (diPanelLayout.ts): the strip counts only while it holds a restore control
  const [stripCount, setStripCount] = useState(0);
  useEffect(() => {
    if (strip === null) return;
    const count = () => setStripCount(strip.childElementCount);
    count();
    const observer = new MutationObserver(count);
    observer.observe(strip, { childList: true });
    return () => observer.disconnect();
  }, [strip]);
  const dock = diDockFor(useViewportWidth(), collapsed, stripCount > 0);
  useEffect(() => {
    listeners.add(setState);
    return () => {
      listeners.delete(setState);
    };
  }, []);
  return (
    <DiMinimizedTrayContext.Provider value={WITH_STRIP ? strip : null}>
      <DiOfficePanel
        state={state}
        officeState={office as unknown as OfficeState}
        collapsed={collapsed}
        onCollapsedChange={setCollapsed}
        widthPx={dock.panelPx}
        review={review}
        mission={missionAccess}
        evidence={evidenceAccess}
        receptionInbox={receptionInbox}
        presentation={SCAN_FACE ? 'scan' : 'operational'}
      />
      {WITH_STRIP && (
        <div
          ref={setStrip}
          className="di-op-theme di-min-tray"
          role="group"
          aria-label="Minimized panels"
          data-testid="di-minimized-tray"
          style={{ width: DI_MINIMIZED_STRIP_WIDTH_PX, right: dock.trayRightPx }}
        />
      )}
    </DiMinimizedTrayContext.Provider>
  );
}

declare global {
  interface Window {
    __diHarness: {
      office: typeof office;
      calls: typeof calls;
      initial: DiOfficeState;
      /** Publish a new office state (a later projection), as the transport would. */
      update: (edit: (state: DiOfficeState) => DiOfficeState) => void;
      current: () => DiOfficeState;
      /** Open the reception inbox (as the in-world desk does, with no opener to restore focus to). */
      openReception: () => void;
      /**
       * Close the reception inbox through the SAME `close()` the panel's own routes call (a route into the request
       * panel, "Show this Mission", details and evidence), whatever its state, including minimized. A lifecycle
       * seam: it lets a test close a HIDDEN inbox while another dialog is in front.
       */
      closeReception: () => void;
      /** Whether the sidebar is collapsed right now. */
      isCollapsed: () => boolean;
      /** With `?p2a=1`: the labelled synthetic ids the seam added (null otherwise). */
      p2a: typeof P2A;
      /** With `?evidence=1`: the two Missions' labelled synthetic evidence, and every content request made. */
      evidence: {
        missions: { A: SyntheticEvidence; B: SyntheticEvidence } | null;
        requests: () => EvidenceSelection[];
      };
    };
  }
}

let latest = initial;
window.__diHarness = {
  office,
  calls,
  initial,
  update: (edit) => {
    latest = edit(structuredClone(latest));
    office.characters = new Map(drawnIds(latest).map((id) => [id, {}]));
    for (const listener of listeners) listener(latest);
  },
  current: () => latest,
  openReception: () => inbox.open?.(),
  closeReception: () => inbox.close?.(),
  isCollapsed: () => inbox.collapsed,
  p2a: P2A,
  evidence: { missions: synthetic, requests: () => evidenceRequests.map((r) => ({ ...r })) },
};

createRoot(document.getElementById('root')!).render(<Harness />);
