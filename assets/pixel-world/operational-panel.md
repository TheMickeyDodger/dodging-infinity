# Operational panel screenshots

Provenance of the two images used in the README section "A calm overview and a full-size inspector". They are
**not** owner-supplied captures, so the statements in `README.md` in this folder about its six images do not describe them.

- `operational-panel-overview.png`: the side panel's overview, cropped to the panel itself. Under the product's own
  `DI Mission office: LIVE - FIXTURE DATA` header it shows two Missions, each with its objective, observed status,
  team and an **Open inspector** button.
- `operational-panel-inspector.png`: the full-size inspector for the first Mission, cropped to the dialog: the heading and
  status, the objective and observed status, and the team, each member with its identity and observed state.

How they were made:

- Captured on 21 October 2026 from the feature13 candidate build (the production build of the Pixel Agents DI office),
  through the product's own offered interface.
- The data is a **synthetic test fixture** served with the office's `--fixture-preview` mode, so the product marks it
  itself in the frame: `LIVE - FIXTURE DATA` in the panel header, and `· FIXTURE DATA` in the inspector. No caption was
  added.
- Publication uses a separate short-goal variant of the owned fixture (generic "sample" goals, no real project, person or
  repository), so the screenshots can show the objective, status and team without one very long paragraph. The long-text
  fixtures that the tests use are unchanged.
- Each image is a crop of the rendered page: no browser chrome, address bar or access token is in frame, and nothing was
  generated or composited. Both are 8-bit sRGB PNGs carrying only the standard header, colour-space, image-data and end
  chunks; no text or EXIF metadata remains.
- The inspector shows the honest `Code review is not offered` line on each member, because this fixture speaks the older
  query contract; nothing was added to make a review look available.

Like the other screenshots, they document appearance at capture time, not Mission success or live concurrency, and they
make no statement about how many residents the office holds.
