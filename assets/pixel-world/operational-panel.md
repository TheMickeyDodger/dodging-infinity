# Operational panel screenshots

Provenance of the two images used in the README section "A calm overview and a full-size details view". They are
**not** owner-supplied captures, so the statements in `README.md` in this folder about its six images do not describe them.

- `operational-panel-overview.png`: the side panel's overview, cropped to the panel itself. Under the product's own
  `DI Mission office: LIVE - FIXTURE DATA` header it shows the attention line ("1 needs attention", with **Show the list**)
  and the one plain sentence saying what this view may not show; Find; the quiet notice that the Codex Orchestrator is
  not available in this office; and two Mission cards, each leading with its goal, then its status (a word and a shape)
  with its short source-qualified reference at the end of that line, its team as observed ("4 of 4 members observed
  running · 4 terminals busy"), its actions (**Show in the office**, **Show details**) and its team's roles.
- `operational-panel-inspector.png`: the full-size details view of the first Mission, cropped to the dialog: its title,
  short reference and status; its complete objective and status; its results said once ("This office doesn't report
  Mission results …"); and its team, with the office-wide code review limit said once for the team ("This office can't
  confirm which code workspace belongs to which agent, so code review is not offered here") and each member with its one
  observed phrase ("Terminal busy") and **Show in the office**.

How they were made:

- Captured at 2026-10-22T01:56:33.240Z (UTC) by `e2e/di/publicationCaptures.ts`, from the production build of the
  product-experience candidate (built 2026-10-22T01:56:19.712Z; `dist/di-cli.js` sha256 `6fb79552bb4166c3782afb2a33521a61e82bf7330788e90bdc33291ebf36483b`, `dist/webview/assets/index-COlUsr_r.js` sha256
  `033b3e93eb1943be8eceb68ceb4232500c95afbd030ce464c14025301381bd85`). The tool refuses a build older than its sources, and its report binds every built file by
  sha256. Both images from this capture are byte-identical to the files in this folder, so they were left in place.
- The data is a **synthetic test fixture** served with the office's `--fixture-preview` mode, so the product marks it
  itself in the frame: `LIVE - FIXTURE DATA` in the panel header, and `· FIXTURE DATA` in the details view. No caption was
  added.
- Publication uses the owned short-goal variant of the fixture (`e2e/di/publicationFixture.ts`: generic "sample" goals, no
  real project, person, repository or task id), so the screenshots can show the hierarchy without one very long paragraph.
  The fixtures that the tests use are unchanged.
- Each image is a crop of the rendered page at device scale 2 (overview from a 1280x900 window,
  details from 1280x1000): no browser chrome, address bar or access token is in frame, and nothing was
  generated or composited. Before each capture the whole page text and markup were scanned for the access token, a local
  path, a loopback host, a URL, a bearer credential and private evidence, and none was found. Both are 8-bit sRGB PNGs
  carrying only the standard header, colour-space, image-data and end chunks, proved pixel-identical to the raw capture;
  no text or EXIF metadata remains. sha256: overview `de2dc68270ce2a171d0487044887db6237d177f455bfe3e1f21bd20df894917d`, details `96412767296b98bd1f15096c40f327dc0a52bee88e4da68997fe69e1c6e0e910`.
- This fixture speaks the older query contract, so no code review is offered; the details view says so once, for the
  team, in plain words, and nothing was added to make a review look available.

Like the other screenshots, they document appearance at capture time, not Mission success or live concurrency, and they
make no statement about how many residents the office holds.
