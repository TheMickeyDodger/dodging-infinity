# DI Mission preview: isolated, fixture-only (FIXTURE / VISUAL TEST)

The Mission preview is the **real office app**: the webview's own production build, on its product face, the scan panel with its navigation rail. It
runs over a **labelled fixture transport** instead of an office server. Each scene sends one office state. The real server projected that state from
a real producer answer, and the server's own tests exported it. The app then draws the product's own label, "FIXTURE / VISUAL TEST - synthetic
hash-pinned fixture data, not live Missions", on every screen.

Nothing here is live. The preview starts no office server, so no `--state-dir`, `--fixture-source`, request panel, delivery adapter or Herdr flag
exists to pass. It reads no discovery file, opens no connection to a request panel, delivery adapter, Herdr or backend, and reads or writes no Mission
store and no `~/.pixel-agents` file. A delete or a delivery step the page sends goes nowhere. The page is answered only from the recorded replies a
scene names, or from the one declared-synthetic answer below.

**A preview is not proof of retention.** The screenshots and this walkthrough show what the office says. The proof that local data is actually
reclaimed and deleted on disk is the retention suite's own: `tests/test_mission_retention.py` and `tests/test_mission_retention_http.py`.

## Launch

From `integrations/pixel-agents/webview-ui`:

```sh
npx vite build --config test/harness/di-mission-preview.config.ts
npx vite preview --config test/harness/di-mission-preview.config.ts --port 5179 --strictPort
```

Then open http://localhost:5179/test/harness/di-mission-preview.html (the scene index). Each link opens one scene. Open one scene at a time, because
a cookie names the scene a page reads. Stop the preview with Ctrl-C.

- The build goes to `webview-ui/node_modules/.di-mission-preview` (ignored by git), or to `DI_PREVIEW_OUT_DIR` if you set it to an absolute
  directory. It refuses to empty any other non-empty directory.
- It refuses a development build. If `NODE_ENV` is set to anything but `production`, unset it: a development build would run the development asset
  mock instead of the app as it ships.
- The page opens exactly **one** socket, the preview's own `/ws`. A production build carries no development client, so no hot-reload socket is
  attempted.

**One path, asserted.** `webview-ui/test/diMissionPreviewBrowser.test.ts` runs these exact two commands, changing only the port (to a free one) and
`DI_PREVIEW_OUT_DIR` (to its own temporary directory). It checks that this page quotes them, and it produces the captures below. It also holds every
page it opens to a **clean console**: no error, no warning, no failed request. Its `ISOLATION` test checks the launch path's sources for every office
server, discovery and Herdr route. It checks that the page reaches only the preview itself and opens only `/ws`, and that every answer the page got
was either recorded or declared synthetic.

What the preview does to make the scenes honest:

- **The page clock is held at each scene's own evaluation time,** the moment the real server projected that answer. The client's own local-expiry
  check then reads the same moment the server did. Without the pin, an answer produced under a test clock would read Stale for no reason of its own.
- **Document-relative asset addresses are served from the build's own `/assets/`.** The app's asset base is `./`, made for a page at the server's
  root, but the scene page lives under `/test/harness/`.
- **The Disconnected scene:** a moment after the page opens, the preview closes the page's socket and leaves every reconnection unanswered, as an
  unreachable office would.

## Scenes and where each comes from

Every office state is **producer-derived**: a real producer answer (a Python test's export), projected by the real server (a server test's export).
What in it is **declared synthetic** is said per scene. No scene presents a synthetic input as a real one.

| Scene | What it shows | Its office state (in `webview-ui/test/fixtures/`) | Declared synthetic |
|---|---|---|---|
| `lifecycle-of-record` | A Mission Core Mission of record, never started (Not started). Beside it, the overdue Mission is already finished (Ended) and filed in Archived. | `di-office-state-mission-lifecycle-v2.FIXTURE.json` `states.of_record` | Mission Core's records come from the producer's test harness, whose observer and spawner are test stand-ins. |
| `lifecycle-approved` | Approved, and still never started: approval does not start a run. | same, `states.approved_never_started` | same |
| `lifecycle-start-requested` | Its start was requested, but the catalog does not yet observe a team. The office says **Unknown**, with its reason, never Running. | same, `states.start_requested` | same |
| `lifecycle-task-observed` | Its task is observed, and no worker is confirmed (**Task observed**). | same, `states.task_observed` | The runtime snapshot is synthetic: it stands for a runtime capture, and no process ran. |
| `lifecycle-archived` | Finished: **Ended**, and filed into Archived at once. The Mission is read-only. | same, `states.completed_ended_archived` | As above. The "completed" step is Mission Core's verify invocation, which is not canonical Verified. This fixture's Mission State composer still reports the target task in progress, so no completion is observed and the status is Ended. |
| `lifecycle-reclaimed` | Reclaimed by the startup sweep 30 days after it finished. | same, `states.reclaimed_at_startup` | same |
| `lifecycle-deleted` | Deleted by its owner. | same, `states.deleted_by_owner` | same |
| `lifecycle-stale` | The task-observed answer, read 600 s after it was produced: **Stale**. | same, `expired.state` | The server's own local expiry of the real answer. No new answer arrives. |
| `lifecycle-disconnected` | The same answer, then the office disconnects: **Disconnected**. | same, `states.task_observed` | The preview closes the socket. Disconnected is the client's own state for a transport that is not connected. |
| `herdr-roster` | Herdr tasks, as the producer's roster decides them: **Running** (a live agent at work, drawn in the office), **Blocked**, **Failed**, **Waiting**, **Stopped**, **Ended**, **Unrecognised** (a status literal the office does not know) and **Unavailable**. Unavailable means no live session corroborates the task, so no worker is confirmed now. That is not proof the task never started. | `di-office-state-herdr-roster-v2.FIXTURE.json` | The runtime snapshot and the task records are synthetic. |
| `workflow-phases` | Workflows: **Start requested** (a handoff was sent), **Verified** (its declared verified success) and **Waiting** (it needs reauthorization). | `di-office-state-workflow-phases-v2.FIXTURE.json` | The workflow records come from the workflow authority's own test constructor. No engine ran. |
| `delete-in-flight`, `delete-pending`, `delete-refused-busy`, `delete-refused-stale`, `delete-completed` | The owner's hold-to-delete on an archived Mission Core Mission (Stopped), with a running Mission beside it. | `di-office-state-progress-abandoned-v2.FIXTURE.json` | The answer is the request panel's **real** answer over its real HTTP route, relayed by the office's **real** relay (`di-mission-request-delete-replies.FIXTURE.json`). Only its sequence number is re-addressed to the request this page sent. `delete-in-flight` is never answered. |
| `archived-delivery` | An archived COMPLETE Herdr task's linked delivery: read-only, still readable. | `di-office-state-archived-delivery-v2.FIXTURE.json` | The delivery adapter's **status answer is DECLARED SYNTHETIC** (the shape `test/diArchivedDeliveryBrowser.test.ts` uses). No delivery adapter is connected. |

The producer exports behind these states:

| Export | Made by |
|---|---|
| `server/__tests__/fixtures/di-backend/mission-lifecycle-answers.FIXTURE.json` | `tests/test_mission_lifecycle_answers.py` |
| `herdr-roster-answers.FIXTURE.json` | `tests/test_herdr_roster_answers.py` |
| `workflow-phase-answers.FIXTURE.json` | `tests/test_workflow_phase_answers.py` |
| `mission-panel-archived-refusals.FIXTURE.json` | `tests/test_mission_archived_refusal_http.py` |

`server/__tests__/diMissionLifecycleWire.test.ts` projects the first three through the real server. Each Python export reproduces exactly at
different temporary-root lengths: the temporary root is written as `/synthetic-test-root`, and every value derived from a path is recomputed by the
product's own function. For the lifecycle export only, the execution identity and cursor are written as labelled stand-ins, and their real values are
checked against the producer's.

## The lifecycle, step by step

One Mission Core Mission's path through the vocabulary. Each step names the fixture that shows it.

| Step | Scene | The office says | Producer-derived or declared synthetic |
|---|---|---|---|
| Never started | `lifecycle-of-record`, `lifecycle-approved` | **Not started**, on Mine and All, its details and Reception | Producer-derived (Mission Core's own record) |
| Start requested | `lifecycle-start-requested` | **Unknown**. The catalog does not observe the team, and the office says so rather than claim a start or a run | Producer-derived |
| Task observed | `lifecycle-task-observed` | **Task observed**, the same word and glyph (◎) on the Missions table, its details, Reception and the rail. No worker is drawn as working | Producer-derived; the runtime snapshot is declared synthetic |
| Running (worker activity) | **No fixture for this Mission.** `herdr-roster` shows Running for a Herdr task whose live agent is at work | **Running**, and only that task | Producer-derived (roster); its snapshot is declared synthetic |
| Terminal | `lifecycle-archived` | **Ended**. In this fixture no completion is observed (its composer still reports the target task in progress), so the office does not say Verified | Producer-derived |
| Archived | `lifecycle-archived` | Gone from Mine's All, Needs you, Stalled, Running and Done at once, and listed under Archived. Read-only | Producer-derived |
| Reclaimed | `lifecycle-reclaimed` | Its local data was reclaimed (the retained summary, and what is no longer kept) | Producer-derived (the request panel's startup sweep, against real owned stores) |
| Deleted | `lifecycle-deleted` | Its local data was deleted by its owner. The hold is not offered again | Producer-derived |

Where no fixture exists, it is said plainly:

- No producer fixture has **this** Mission Core Mission running with a live working team. Running is shown by a Herdr task instead.
- No producer fixture has a Mission Core Mission reading **Verified**. A Mission Core Mission reads Verified when its record has ended **and** its
  Mission State completion is **observed** with `verifiedSuccess` true (`webview-ui/src/components/diMissionStatus.ts`). An ended record otherwise
  reads Ended, and "Ended is not verified success". Two different facts both read Ended there:
  - verification was observed and did not affirm success (`verifiedSuccess` false);
  - no completion was observed at all (`verifiedSuccess` not computed).

  **This fixture** reads Ended for the second reason. Its Mission State composer still reports the target task in progress, so it never reaches an
  observed completion, even though Mission Core's verify ran. Verified is shown by a workflow instead (`workflow-phases`).
- **Start requested** for this Mission is not shown. The preview's request workspace is not connected, and the catalog reads the step as Unknown.
  Start requested is shown by a workflow (`wf-dispatched`).

The other surface of the start-requested step, the request workspace, is covered by `webview-ui/test/diMissionStatusSurfaces.test.ts`. There a
dispatched request reads Start requested and is never counted as running.

## All 15 statuses

The closed vocabulary (`DI_MISSION_STATUS_IDS`, `webview-ui/src/components/diMissionStatus.ts`), each with its own glyph. `not_read` is **not** a
status: it is a fact condition (a source that was not read), shown through each status's mapped result and its reason, never as a 16th chip.

| Status | Glyph | Scene | | Status | Glyph | Scene |
|---|---|---|---|---|---|---|
| Not started | □ | `lifecycle-of-record` | | Stopped | ⊘ | `herdr-roster` |
| Start requested | ◔ | `workflow-phases` | | Ended | ■ | `lifecycle-archived` |
| Task observed | ◎ | `lifecycle-task-observed` | | Verified | ◆ | `workflow-phases` |
| Running | ● | `herdr-roster` | | Stale | ◇ | `lifecycle-stale` |
| Waiting | ◐ | `herdr-roster`, `workflow-phases` | | Disconnected | ◌ | `lifecycle-disconnected` |
| Blocked | ▲ | `herdr-roster` | | Unknown | ○ | `lifecycle-start-requested` |
| Failed | ✕ | `herdr-roster` | | Unrecognised | ? | `herdr-roster` |
| | | | | Unavailable | ⊗ | `herdr-roster` |

A Mission status's mark is its **canonical glyph**, the same for that status on every surface: the Missions table, Mission details, Reception, and
the rail's Orchestrators. Marks from other vocabularies keep the dot: attention ("Needs you"), a delivery step's gate, and the labelled specimen's
sample pills. The glyph is `aria-hidden`. The word beside it is the status's name for assistive technology, and a screen reader would only read the
glyph's Unicode name ahead of that word. The browser test asserts each status's own glyph, not merely that a mark exists.

## Owner hold-to-delete

1. Open **Missions**, choose **Archived**, then open the Mission's row.
2. In its details, **hold** the trash button for 1.5 s (`DI_DELETE_HOLD_MS`). A click does nothing.
3. The answer is shown **in those details**, tied to that Mission. Nothing opens or switches a workspace to show it.

| Scene | What happens |
|---|---|
| `delete-in-flight`, the running Mission | The trash is disabled: "A running Mission cannot be deleted." Nothing is sent. |
| `delete-in-flight`, the archived Mission | Sent, and no answer yet. The hold is withdrawn, so it cannot be sent twice. |
| `delete-pending` | Accepted and still finishing. Its summary is already removed; the rest is retried by the next retention pass. Never shown as refused or as done. |
| `delete-refused-busy`, `delete-refused-stale` | Refused, with the request panel's own reason. Nothing was removed, and the hold is offered again. |
| `delete-completed` | Deleted after reclamation: its kept summary was deleted, and a record that it was deleted remains. |
| `lifecycle-deleted` | Already deleted. The hold is not offered again. |

A delete removes the Mission's local data and its retained summary. It deliberately keeps two things: Mission Core's own record of the request and
its authority (authority history is never erased), and the retention bookkeeping that records the Mission's identity and that it was deleted. When
and how reclamation runs is in the root README, "One status per Mission, and what finishing does to it".

## The archived boundary: read-only, still inspectable

An archived Mission is read-only. Nothing can be started, restarted, run or delivered from it. Every control that cannot apply is drawn **disabled
with its reason**, never hidden. What stays readable:

- **Its delivery.** An archived Herdr task's **linked** delivery status is read through the existing status route, and its facts are shown. The
  confirmation box and the step stay disabled beside "Not offered: this Mission is archived…" (`archived-delivery`).
- **Its Herdr status.** "Check again" re-reads the Herdr status, which is a read, never an action.

The request panel refuses a restart or a run of an archived Mission **itself**, whatever a page sends. `test/diMissionArchivedRefusal.test.ts` feeds
the panel's real refusals, relayed by the real relay, through the request model, and asserts each is shown as a refusal in the panel's own words.

**Creating a delivery record requires a COMPLETE task.** The existing delivery ceremony collects a COMPLETE task and the latest canonical APPROVE. A
COMPLETE task is archived, and the archived view is read-only, so the office shows no creation command for it: "Creating a delivery record is not
offered here…". An ACTIVE task's route is never implied to create one, because the ceremony would refuse it. You create the record with the ceremony
from your own terminal, as `docs/pixel-agents-office.md` describes ("Creating a delivery record for a Herdr task"). Once a record links, its status is
readable from the archived details. The standalone delivery path and every human gate are unchanged.

## Captures

All captures are in `integrations/pixel-agents/docs/di-mission-preview/`, produced by the browser test on the launch path above (desktop 1440 × 900,
narrow 414 × 896 and 375 × 812):

```sh
DI_PREVIEW_CAPTURE_DIR="$PWD/../docs/di-mission-preview" npx vitest run test/diMissionPreviewBrowser.test.ts
```

`capture-report.json` beside them records each image's scene, its fixture and that fixture's sha256, its viewport, and the image's own sha256. They
are not pixel-compared on later runs: a capture shows the build it was taken from.

What to look at first:

- **One Mission, one status:** `missions-lifecycle-task-observed-1440.png`, `details-lifecycle-task-observed-1440.png`,
  `reception-lifecycle-task-observed-1440.png` (Task observed ◎ on the table, the details, Reception and the rail).
- **Never started, Unknown, working:** `details-lifecycle-approved-1440.png`, `details-lifecycle-start-requested-1440.png`,
  `missions-herdr-roster-1440.png`, `office-herdr-roster-1440.png`.
- **Filed at once:** `missions-all-after-finish-1440.png` (All lists none), then `missions-archived-1440.png`.
- **Read-only with reasons:** `details-lifecycle-archived-1440.png`, `details-lifecycle-archived-reasons-375.png`.
- **Hold-to-delete:** `delete-running-refused-1440.png`, `delete-in-flight-1440.png`, `delete-pending-1440.png`, `delete-refused-busy-1440.png`,
  `delete-refused-stale-1440.png`, `delete-completed-1440.png`.
- **Archived delivery, read:** `archived-delivery-read-1440.png`.
- **Keyboard:** `keyboard-archived-row-1440.png`, `keyboard-archived-hold-1440.png`.
- **Narrow:** every `-414` and `-375` capture.

A per-scene Missions capture shows **All** where that scene lists any Mission under All, and **Archived** where every one is filed closed (its
filter is in the table). The reclaimed and deleted scenes' Missions captures are identical: the Missions list does not show a Mission's retention
phase; its details do.

Every capture that landed:

| Capture | Scene | Viewport |
|---|---|---|
| [`archived-delivery-read-1440.png`](di-mission-preview/archived-delivery-read-1440.png) | `archived-delivery` | 1440 × 900 |
| [`delete-completed-1440.png`](di-mission-preview/delete-completed-1440.png) | `delete-completed` | 1440 × 900 |
| [`delete-in-flight-1440.png`](di-mission-preview/delete-in-flight-1440.png) | `delete-in-flight` | 1440 × 900 |
| [`delete-pending-1440.png`](di-mission-preview/delete-pending-1440.png) | `delete-pending` | 1440 × 900 |
| [`delete-refused-busy-1440.png`](di-mission-preview/delete-refused-busy-1440.png) | `delete-refused-busy` | 1440 × 900 |
| [`delete-refused-busy-375.png`](di-mission-preview/delete-refused-busy-375.png) | `delete-refused-busy` | 375 × 812 |
| [`delete-refused-busy-414.png`](di-mission-preview/delete-refused-busy-414.png) | `delete-refused-busy` | 414 × 896 |
| [`delete-refused-stale-1440.png`](di-mission-preview/delete-refused-stale-1440.png) | `delete-refused-stale` | 1440 × 900 |
| [`delete-running-refused-1440.png`](di-mission-preview/delete-running-refused-1440.png) | `delete-in-flight` | 1440 × 900 |
| [`details-lifecycle-approved-1440.png`](di-mission-preview/details-lifecycle-approved-1440.png) | `lifecycle-approved` | 1440 × 900 |
| [`details-lifecycle-archived-1440.png`](di-mission-preview/details-lifecycle-archived-1440.png) | `lifecycle-archived` | 1440 × 900 |
| [`details-lifecycle-archived-375.png`](di-mission-preview/details-lifecycle-archived-375.png) | `lifecycle-archived` | 375 × 812 |
| [`details-lifecycle-archived-414.png`](di-mission-preview/details-lifecycle-archived-414.png) | `lifecycle-archived` | 414 × 896 |
| [`details-lifecycle-archived-reasons-375.png`](di-mission-preview/details-lifecycle-archived-reasons-375.png) | `lifecycle-archived` | 375 × 812 |
| [`details-lifecycle-archived-reasons-414.png`](di-mission-preview/details-lifecycle-archived-reasons-414.png) | `lifecycle-archived` | 414 × 896 |
| [`details-lifecycle-deleted-1440.png`](di-mission-preview/details-lifecycle-deleted-1440.png) | `lifecycle-deleted` | 1440 × 900 |
| [`details-lifecycle-reclaimed-1440.png`](di-mission-preview/details-lifecycle-reclaimed-1440.png) | `lifecycle-reclaimed` | 1440 × 900 |
| [`details-lifecycle-start-requested-1440.png`](di-mission-preview/details-lifecycle-start-requested-1440.png) | `lifecycle-start-requested` | 1440 × 900 |
| [`details-lifecycle-task-observed-1440.png`](di-mission-preview/details-lifecycle-task-observed-1440.png) | `lifecycle-task-observed` | 1440 × 900 |
| [`details-lifecycle-task-observed-375.png`](di-mission-preview/details-lifecycle-task-observed-375.png) | `lifecycle-task-observed` | 375 × 812 |
| [`details-lifecycle-task-observed-414.png`](di-mission-preview/details-lifecycle-task-observed-414.png) | `lifecycle-task-observed` | 414 × 896 |
| [`home-herdr-roster-375.png`](di-mission-preview/home-herdr-roster-375.png) | `herdr-roster` | 375 × 812 |
| [`home-herdr-roster-414.png`](di-mission-preview/home-herdr-roster-414.png) | `herdr-roster` | 414 × 896 |
| [`index-1440.png`](di-mission-preview/index-1440.png) | `lifecycle-of-record` | 1440 × 900 |
| [`keyboard-archived-hold-1440.png`](di-mission-preview/keyboard-archived-hold-1440.png) | `lifecycle-archived` | 1440 × 900 |
| [`keyboard-archived-row-1440.png`](di-mission-preview/keyboard-archived-row-1440.png) | `lifecycle-archived` | 1440 × 900 |
| [`missions-all-after-finish-1440.png`](di-mission-preview/missions-all-after-finish-1440.png) | `lifecycle-archived` | 1440 × 900 |
| [`missions-archived-1440.png`](di-mission-preview/missions-archived-1440.png) | `lifecycle-archived` | 1440 × 900 |
| [`missions-archived-375.png`](di-mission-preview/missions-archived-375.png) | `lifecycle-archived` | 375 × 812 |
| [`missions-archived-414.png`](di-mission-preview/missions-archived-414.png) | `lifecycle-archived` | 414 × 896 |
| [`missions-archived-delivery-1440.png`](di-mission-preview/missions-archived-delivery-1440.png) | `archived-delivery` (filter: archived) | 1440 × 900 |
| [`missions-delete-in-flight-1440.png`](di-mission-preview/missions-delete-in-flight-1440.png) | `delete-in-flight` (filter: all) | 1440 × 900 |
| [`missions-herdr-roster-1440.png`](di-mission-preview/missions-herdr-roster-1440.png) | `herdr-roster` (filter: all) | 1440 × 900 |
| [`missions-herdr-roster-375.png`](di-mission-preview/missions-herdr-roster-375.png) | `herdr-roster` | 375 × 812 |
| [`missions-herdr-roster-414.png`](di-mission-preview/missions-herdr-roster-414.png) | `herdr-roster` | 414 × 896 |
| [`missions-lifecycle-approved-1440.png`](di-mission-preview/missions-lifecycle-approved-1440.png) | `lifecycle-approved` (filter: all) | 1440 × 900 |
| [`missions-lifecycle-archived-1440.png`](di-mission-preview/missions-lifecycle-archived-1440.png) | `lifecycle-archived` (filter: archived) | 1440 × 900 |
| [`missions-lifecycle-deleted-1440.png`](di-mission-preview/missions-lifecycle-deleted-1440.png) | `lifecycle-deleted` (filter: archived) | 1440 × 900 |
| [`missions-lifecycle-disconnected-1440.png`](di-mission-preview/missions-lifecycle-disconnected-1440.png) | `lifecycle-disconnected` (filter: all) | 1440 × 900 |
| [`missions-lifecycle-of-record-1440.png`](di-mission-preview/missions-lifecycle-of-record-1440.png) | `lifecycle-of-record` (filter: all) | 1440 × 900 |
| [`missions-lifecycle-reclaimed-1440.png`](di-mission-preview/missions-lifecycle-reclaimed-1440.png) | `lifecycle-reclaimed` (filter: archived) | 1440 × 900 |
| [`missions-lifecycle-stale-1440.png`](di-mission-preview/missions-lifecycle-stale-1440.png) | `lifecycle-stale` (filter: all) | 1440 × 900 |
| [`missions-lifecycle-start-requested-1440.png`](di-mission-preview/missions-lifecycle-start-requested-1440.png) | `lifecycle-start-requested` (filter: all) | 1440 × 900 |
| [`missions-lifecycle-task-observed-1440.png`](di-mission-preview/missions-lifecycle-task-observed-1440.png) | `lifecycle-task-observed` (filter: all) | 1440 × 900 |
| [`missions-workflow-phases-1440.png`](di-mission-preview/missions-workflow-phases-1440.png) | `workflow-phases` (filter: all) | 1440 × 900 |
| [`office-herdr-roster-1440.png`](di-mission-preview/office-herdr-roster-1440.png) | `herdr-roster` | 1440 × 900 |
| [`reception-lifecycle-task-observed-1440.png`](di-mission-preview/reception-lifecycle-task-observed-1440.png) | `lifecycle-task-observed` | 1440 × 900 |
| [`reception-lifecycle-task-observed-375.png`](di-mission-preview/reception-lifecycle-task-observed-375.png) | `lifecycle-task-observed` | 375 × 812 |
| [`reception-lifecycle-task-observed-414.png`](di-mission-preview/reception-lifecycle-task-observed-414.png) | `lifecycle-task-observed` | 414 × 896 |

## Narrow windows

The rail keeps its width (200 px), so at 414 px the stage beside it is 214 px wide, and at 375 px it is 175 px. In that width:

- the fixture label takes its own strip across the top of the window;
- the panel's list starts past the rail;
- the filter chips keep each word with its count inside their own edge;
- each Missions row stacks, with the Mission, its status, then each fact under its column's own word;
- status pills wrap their words inside their own border;
- the disabled actions' reasons stack.

The browser test measures every chip, status, row and reason in **both dimensions**: content, every line of text inside its own border, and its box
inside what can actually show it. A long Mission title in the details header wraps over several lines. That is expected with the rail as it is, and
the narrow work did not redesign the rail or the navigation.

## Limits and disclosures

- **Figures 4.4a–c are not in this repository.** This preview shows the status contract's own words and states, not a match to any figure, and
  claims none.
- **Acceptance tokens that cannot be resolved here:**
  - AC-4.4-13 appears only in in-repo comments (`webview-ui/src/components/diScanModel.ts:668` and `:1412`, `DiScanPages.tsx:242` and `:245`) and
    test names. Its definition is not in this repository.
  - **AC-4.4-17** is not cited or defined anywhere here.
  - `DI_HARNESS_REPO` cites D107 and AC-4.3-45 (`diScanModel.ts:1410`), and D107 with AC-4.3-06 (`diScanPagesModel.ts:747`). None of them is in
    this repository.
  - `DI_OP_GLYPH`, named in the brief as the existing "never colour alone" rule, does not exist by that name. The rule lives in the per-status
    glyphs of `diMissionStatus.ts`.
- **What the preview does not connect:** the request workspace's own overview (Ask, Requests), Herdr, and delivery steps. Their controls show what
  an office without those connections shows.
- **Observations, reported and not changed:**
  - A list row's second line is its source record's own lifecycle literal and source. It is drawn as the record's, **"Record: RUNNING · Mc"**, beside
    the canonical status (Task observed), so the record's word is never read as DI saying the team is working. It used to be the bare
    `RUNNING · Mc`.
  - The request panel's own stage reads Verified for a verified proposal, while the catalog's canonical status for the same Mission is Ended.
  - A workflow in its VERIFIED phase is not filed closed (it stays under All).
