# Team-discovery and desk-readability captures

Three captures of the Pixel Agents DI office, 21 October 2026, used in the README subsection "Teams show up without per-team setup, and their desks stay readable".

**These are synthetic owned-fixture captures from the candidate build, not owner-supplied captures and not live Mission work.** The agents are owned synthetic records (teams of four agents named after the Herdr roles, such as `lead1` and `executor1`, on synthetic tasks), shown through the real catalog bridge (`dicatalog_http.py`) and the office's real catalog reader. They were produced by `dist/di-cli.js --backend-discovery <bridge discovery file> --contract-version 2`; neither `--fixture-source` (which replaces the catalog) nor `--fixture-preview` (which draws a "fixture" label) was used, so the pictures carry no fixture overlay and no substituted catalog produced them.

Build identity: `dist/webview/assets/index-DmOhA-ZF.js` sha256 `453aad881f080ad1113ee943426b6174bca486e20917338b2fa1430b484400a9`; `dist/di-cli.js` sha256 `dc04bcefac393722112d85e484226fcdd89089e4b5e507c659da99755bbaddb8`; built from repository commit `423c96728f36f1201c486b89c78dea13aa740193` plus the uncommitted changes of this candidate.

Normalisation: each image is the page's canvas area (the office, its zoom buttons, its legend and toolbar) captured in headless Chrome at a device pixel ratio of 2. The browser chrome, the inspector panel, the address bar, the access token and any local path are not in the frame. Pixels are unaltered (the normalised file decodes to exactly the captured pixels), converted to a standard 8-bit sRGB PNG that carries only the IHDR, sRGB, IDAT and IEND chunks, so no embedded text, EXIF or other metadata remains. No world content was generated or composited.

- `team-discovery-default-office.png` (2160 × 2000): The default office, one team of four agents. 4 of the office's 140 decorative residents are on duty, so the seated agents and their desks stand out; the legend reads `Ambient residents · 4 on duty of 140 · decorative`. Whole canvas area of the page at a device pixel ratio of 2 (viewport 1500 × 1000 CSS pixels); the zoom buttons, legend and toolbar are shown whole, with no cropped fragments.
  - published file sha256 `53d77f9f34b82410008ed8b00a34c78ef5f4a9c0c6a1e1fb8beec7f7c8e46e2e`
  - source capture (the unmodified screenshot this was normalised from, same pixels) sha256 `90ecbdffb9139a8352ff80543e0e07d40907fadb07b2341adbbe659ed0f42ec1`
- `team-discovery-expanded-office.png` (2160 × 1400): The expanded-office preset, two teams of four agents. 16 of its 140 decorative residents are on duty; the legend reads `Ambient residents · 16 on duty of 140 · decorative`. Whole canvas area at a device pixel ratio of 2 (viewport 1500 × 700 CSS pixels).
  - published file sha256 `f551ae8ff9e5471f962b690d742f75afed87f084856494e3f80a2dca8a87760c`
  - source capture (the unmodified screenshot this was normalised from, same pixels) sha256 `b32e26c449e3f820210ea9c96227c350a6c35ee506ebc0bd31f22c80caf9566d`
- `team-discovery-palace-unchanged.png` (2160 × 1800): The Palace of the living-metropolis preset, four teams of four agents, at the view the office opens on. The Palace was already readable and is UNCHANGED by this work: its 11 residents are below the on-duty bound, so every resident of the world is drawn (`Ambient residents · 145 · decorative`). Whole canvas area at a device pixel ratio of 2 (viewport 1500 × 900 CSS pixels); the world continues beyond the frame.
  - published file sha256 `2700fc2a8de43d77f72d48b45f2983aba50e58d19a34fdfc85fe7220382e9817`
  - source capture (the unmodified screenshot this was normalised from, same pixels) sha256 `566d7319174e3a750b1247391f25bb5b7116249feca60953c8413ec909d57995`

These document appearance at capture time, not Mission success or live concurrency. The count of residents drawn at a time is a presentation of decorative residents only: the rest are off duty, still simulated, and none was removed. Code and underlying asset notices remain in `integrations/pixel-agents/UPSTREAM.md` and `packaging/di/CREDITS.txt`.
