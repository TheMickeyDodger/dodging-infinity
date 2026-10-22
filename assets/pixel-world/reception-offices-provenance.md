# Reception inbox, receptionist, and three-office pictures: provenance

Pictures of the Pixel Agents DI office taken on 22 October 2026 for the README subsection "One Reception inbox, a receptionist, and agents in all three grand offices".

**These are synthetic owned-fixture captures, not owner-supplied captures and not live Mission work.** Every record, agent, conversation, delivery proposal and request in them is a labelled synthetic fixture (the product says so in the frame: "FIXTURE / VISUAL TEST", "FIXTURE DATA"). They were taken from owned previews that each had their own brand-new state directory, never from a user's World, a live catalog or personal paths, and they contain no access token.

## Which build each picture comes from

The set is **mixed**, and each picture says which build it is from:

| Pictures | Build | Why |
|---|---|---|
| the two Reception inbox pictures, the reception desk, the three four-agent office pictures, the three 39-agent office pictures (9) | **the final package** `build-F3b-4052b0e6-4989dbb9` (identity (a) below) | captured from the immutable package that the full matrix built, so what served them is the shipped runtime |
| the nine zero-agent time-lapse pictures | **the earlier runtime F2a** (identity (b) below), before the workstation correction | supplemental, historical: they show no agents, so seating cannot appear in them. They were **not** captured from the final build and are not described as such |

## Runtime identities

**(a) The final package, `build-F3b-4052b0e6-4989dbb9`:** the immutable standalone DI package the full matrix assembled (step `package`, EXIT=0, 3620 files, made read-only afterwards), built by the CI sequence (`build:extension` is the production build, then `build:webview`).

- source state it was built from (the freeze `F3b-4052b0e6-4989dbb9`): HEAD `53af6c2bec38d37e623cae24124338193ea27389`; tracked-diff sha256 `4052b0e69952c3d8b26f414473e388bd69c902386f012d3ee36caf88bbee8cb5`; untracked-set sha256 `4989dbb90ff394f6990c547c35762830bfe72c365778ed7404c848c791bd185d`
- the runtime files that produced the pixels (contained-file sha256; the tarball's own hash is not relied on):
  - `dist/di-cli.js` sha256 `6a2bc30fed16a1efab479bd3907af41534aeed200256b36b8555f1332b262432`
  - `dist/webview/index.html` sha256 `8343d46ffc3cdb7ccd90b6209e62a274ed803d995ba41c2528333aa568939620`
  - `dist/webview/assets/index-BV_4ojx-.js` sha256 `8706531dbc00f7f6c71ebba4dd6bcc5dd064318a616796391cd2f6e5ec4a2c7b`
  - `dist/webview/assets/index-PprUz-if.css` sha256 `cf0cf21687ef62c86b4a46ec05472d9887627cbfaa980b9e733d162cf6298340`

**(b) The earlier runtime (F2a)**, used only for the supplemental time-lapse:

- source state: HEAD `53af6c2bec38d37e623cae24124338193ea27389`; tracked-diff sha256 `5fe890d7d75367ba45a96f753c803f61251e031b068dfe7b0bac26fc61d5c42f`; untracked-set sha256 `f9f6c8b6dfce466dee3dfc3fd18d01fc3182ee569e4822b7444b944bf062620a`
  - `dist/di-cli.js` sha256 `0cf341923849ee41970e84fc2ce877a5f180167bed956489c227a563be901597`
  - `dist/webview/assets/index-DGjXzp-L.js` sha256 `9ae663c28b53f654f413f3597edd0a276316d5f5159309985cb0d3d6ff2afa2d`
  - `dist/webview/assets/index-PprUz-if.css` sha256 `cf0cf21687ef62c86b4a46ec05472d9887627cbfaa980b9e733d162cf6298340`
  - `dist/webview/index.html` sha256 `2b8d3bfea42bd7410e9e45626e1db5d99108f06e344032c799f66315530f275b`

Between (b) and (a) the stylesheet is byte-identical; the webview script, the `index.html` that names it and the server bundle differ. The source change that matters for these pictures is the workstation correction (what counts as a workstation, below); the residents' plan input is unchanged by it. An intermediate capture package, built with `npm run build` (an unminified server bundle), served the same webview files as (a) byte for byte; its pictures were replaced by captures from (a) itself and are not published.

**(c) The published tree** is the freeze source state (a) plus exactly the files below, none of which is part of the package runtime (the served bundle is built from the application source, and the final tree adds no application-source edit after the freeze). Its own identity is recorded with the candidate's evidence and is not repeated here, because this note and these pictures are part of it.

- 7 pictures under `assets/pixel-world/` that differ from the freeze tree:
  - `reception-offices-39-agents-atrium.png`
  - `reception-offices-39-agents-observatory.png`
  - `reception-offices-39-agents-palace.png`
  - `reception-offices-4-agents-atrium.png`
  - `reception-offices-4-agents-observatory.png`
  - `reception-offices-4-agents-palace.png`
  - `reception-offices-desk.png`
- `tests/test_workflow_authority.py`
- `assets/pixel-world/reception-offices-provenance.md`

The nine final pictures were captured from the package in (a), which is the package the matrix built from the freeze, not from the final tree; that tree adds only the files above.

## Why the office pictures were captured again

The first office pictures (earlier runtime F2a, never published) showed the satellites' agent sitting at a small table in a library-style room, with no monitor in front of it: the engine called any chair facing a desk-category table a workstation, bare tables and coffee tables included. The product was corrected so that a workstation is a reachable chair that faces a desk **and** a PC or monitor, by the same rule that lights a PC when an agent sits down. The pictures below are fresh captures from the final package after that correction, and each specified agent was checked, not assumed, to sit at one (next sections).

## How the final-package pictures were taken

Headless Chrome 155.0.8059.85 driven by playwright-core 1.62.0, device pixel ratio 2, on a fresh state directory per scene. The office pictures are the office canvas area (the office, its zoom buttons, legend and toolbar) at 1080 × 1000 CSS pixels; the inbox pictures are the Reception dialog; the desk picture is a 640 × 460 CSS pixel region. Nothing outside the page is in them.

- Camera framing used the ordinary UI: the World/office view buttons, the on-screen zoom buttons and a middle-mouse pan. The page ran with the e2e observation flag set only so the repository's own read-only hooks (character positions and states, the view, the office-assignment export) could verify each picture before it was kept; they draw nothing and change nothing.
- A picture is written **only if every assertion passed** (the capture fails loudly instead). For the desk: the receptionist and the Reception target with its label lie inside the crop, and nothing but the canvas and the target is over the crop. For each office picture, per specified agent: its exact fixture identity; the observed character state seated (typing) at its assigned seat tile within a 25 second bound; the tile it stands on equal to its assigned seat tile; the seat reported by the product as a workstation and, from the layout by the engine's own rule, facing a desk and a PC or monitor; and its sprite inside the crop. The agents are re-observed before and after the shot. The logs are `final-pkg-capture-offices-1.log` (50 assertions, EXIT=0), `final-pkg-capture-desk-1.log` and `final-pkg-capture-inbox-1.log`.
- The persistent status line "Camera jump to … (not a ride)" under the quick-view buttons was hidden with one page style rule in the office pictures; nothing else on the page was altered. The one-time "Updated to …" notice was dismissed on each fresh state directory before capture and checked gone.
- The page labels these placements `preserved` rather than `fresh` once it has saved its own seat preferences; the label was recorded, not asserted, and the state directories were new, so the placements were fresh.
- The two inbox pictures are pixel-identical to the same scenes captured on the intermediate package and on the earlier runtime; they were still captured again from (a).

## Fixtures (all synthetic, labelled in the product frame)

- `p2bc2-four` (one four-member Mission, no runtime agents) sha256 `4854b2cfa80f447155321f2c7ac41704d4603f5935085f67f6bf3270f875e5dc`; identity set `p2bc2-four.expected-members.json` sha256 `53ca43d685c198c3f8144d5bc2432bb65497d7f262d20405e7f60d1846e60c8c`
- `p2bc2-large-small` (a 30-member team and teams of 4, 3 and 2: 39 characters) sha256 `dd8fcaa7c53f48b04b97246e0e043a684ee780ec85d2b9ba04b9499b545a0a0a`; identity set sha256 `f72f574f1e780f7439e0859f57627fa0a30948b91cc987f610cf940df9d6e9bf`
- `p2a-rich` for the inbox and desk pictures, built by the product's own delivery reader over owned synthetic directories for a freshly seeded request panel (a real panel with a scripted assistant and synthetic conversations, one of them awaiting an answer): sha256 `c2da1bce3f5db2de666737574556837ad1d6e7415cbad7484079c1847d911055`
- `zero-agent.FIXTURE.json` (the packaged zero-agent office, contract version 1), used only for the supplemental time-lapse: sha256 `03633b695f48b380748cbdc3aba50b7bfc80f2a4457ff9f8559b4fff260b0ee9`

## The final-package pictures

- `reception-offices-inbox-top.png`: The Reception inbox (top of the list): search and category chips, two plain coverage lines and one closed disclosure, then the first two Mission cards.
  - build: final package (a); published file sha256 `5f252800c3db7ec572ac8197b22fb5760dfe9208e0c31b54cd80b5c534dfb692`
- `reception-offices-inbox-decision.png`: The inbox filtered to Authorization / reauthorization: the decision Mission Core and the request panel both report is ONE card (next step and its Open the request panel button agree), above a reauthorization with no panel route. (The blocker's "opened" date is the fixture's placeholder, epoch second 1,000,000, which the card renders as 1970-01-12.)
  - build: final package (a); published file sha256 `031a006e5e0b9707b48062ddeb5777f42eff81ab6872e839a7e1cf4bfe0721dc`
- `reception-offices-desk.png`: The Palace lobby: the Reception target and label, the permanent receptionist at its post, and the lobby around them.
  - build: final package (a); published file sha256 `75ef7920c1e4a78ee6db47f975981df2d66ac69c404d7216ab79753d91742775`

- `reception-offices-4-agents-palace.png`: Palace, one four-member Mission; 2 specified agent(s), all seated at workstations (observed seated on the first check).
  - build: final package (a); published file sha256 `26843a69fecc12f50865f2ac83281bdbd400a1a48530769cbcf14f24afa824f9`
  - | member | seat (tile) | state | faces | with |
    |---|---|---|---|---|
    | `lead1` | `expansion-102` (88,60) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor02` | `expansion-105` (92,60) | type | DESK_FRONT | PC_FRONT_OFF |

- `reception-offices-4-agents-atrium.png`: Botanical Atrium, one four-member Mission; 1 specified agent(s), all seated at workstations (observed seated on the first check).
  - build: final package (a); published file sha256 `5bf94a82824728326ce530f6a98629ee0ab816a8cea891dea4e730763b276baf`
  - | member | seat (tile) | state | faces | with |
    |---|---|---|---|---|
    | `reviewer1` | `lw-botanical-atrium-212` (19,268) | type | DESK_FRONT | PC_FRONT_OFF |

- `reception-offices-4-agents-observatory.png`: Deco Observatory, one four-member Mission; 1 specified agent(s), all seated at workstations (observed seated on the first check).
  - build: final package (a); published file sha256 `ae8a11b053bd3a164a1fc5c15dad81f23d7c090e520eeaae19098b4f4ee57ba4`
  - | member | seat (tile) | state | faces | with |
    |---|---|---|---|---|
    | `supervisor` | `lw-deco-observatory-738` (95,268) | type | DESK_FRONT | PC_FRONT_OFF |

- `reception-offices-39-agents-palace.png`: Palace, the 39-character multi-team set; 13 specified agent(s), all seated at workstations (observed seated on the first check).
  - build: final package (a); published file sha256 `f2e1075abe12fd7338338ff32156360b9aa8f72dbc7c304ced889795987eafb6`
  - | member | seat (tile) | state | faces | with |
    |---|---|---|---|---|
    | `lead1` | `expansion-102` (88,60) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor02` | `expansion-105` (92,60) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor03` | `expansion-108` (88,64) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor04` | `expansion-111` (92,64) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor05` | `expansion-118` (98,60) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor06` | `expansion-60` (88,49) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor07` | `expansion-121` (102,60) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor08` | `expansion-124` (98,64) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor09` | `expansion-54` (88,45) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor10` | `expansion-63` (92,49) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor11` | `expansion-127` (102,64) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor12` | `expansion-57` (92,45) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor13` | `expansion-134` (108,60) | type | DESK_FRONT | PC_FRONT_OFF |

- `reception-offices-39-agents-atrium.png`: Botanical Atrium, the 39-character multi-team set; 13 specified agent(s), all seated at workstations (observed seated on the first check).
  - build: final package (a); published file sha256 `b9a8a0361d1d36ca183ae06efea8e8227d1c8086b6104bda0a16015cae1dc240`
  - | member | seat (tile) | state | faces | with |
    |---|---|---|---|---|
    | `executor14` | `lw-botanical-atrium-212` (19,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor15` | `lw-botanical-atrium-199` (19,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor16` | `lw-botanical-atrium-209` (15,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor17` | `lw-botanical-atrium-196` (15,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor18` | `lw-botanical-atrium-206` (11,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor19` | `lw-botanical-atrium-192` (11,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor20` | `lw-botanical-atrium-202` (7,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor21` | `lw-botanical-atrium-189` (7,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor22` | `lw-botanical-atrium-268` (19,287) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor23` | `lw-botanical-atrium-128` (19,246) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor24` | `lw-botanical-atrium-265` (15,287) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor25` | `lw-botanical-atrium-272` (23,287) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor26` | `lw-botanical-atrium-286` (19,291) | type | DESK_FRONT | PC_FRONT_OFF |

- `reception-offices-39-agents-observatory.png`: Deco Observatory, the 39-character multi-team set; 13 specified agent(s), all seated at workstations (observed seated on the first check).
  - build: final package (a); published file sha256 `e2edc2f5c1903b28b0a3efd7959f650595f815ccee2021ca3068e72b73d8b363`
  - | member | seat (tile) | state | faces | with |
    |---|---|---|---|---|
    | `reviewer1` | `lw-deco-observatory-735` (91,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `supervisor` | `lw-deco-observatory-721` (91,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor27` | `lw-deco-observatory-738` (95,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor28` | `lw-deco-observatory-725` (95,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `lead1` | `lw-deco-observatory-731` (87,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `reviewer1` | `lw-deco-observatory-728` (83,268) | type | DESK_FRONT | PC_FRONT_OFF |
    | `supervisor` | `lw-deco-observatory-715` (83,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `executor02` | `lw-deco-observatory-718` (87,264) | type | DESK_FRONT | PC_FRONT_OFF |
    | `lead1` | `lw-deco-observatory-794` (95,287) | type | DESK_FRONT | PC_FRONT_OFF |
    | `reviewer1` | `lw-deco-observatory-791` (91,287) | type | DESK_FRONT | PC_FRONT_OFF |
    | `supervisor` | `lw-deco-observatory-797` (99,287) | type | DESK_FRONT | PC_FRONT_OFF |
    | `lead1` | `lw-deco-observatory-653` (95,246) | type | DESK_FRONT | PC_FRONT_OFF |
    | `reviewer1` | `lw-deco-observatory-640` (95,242) | type | DESK_FRONT | PC_FRONT_OFF |

## The supplemental time-lapse (earlier runtime F2a, zero agents)

Zero-agent synthetic office, **wall-clock** time after the page loaded (the office runs in real time), not simulated time. The exact elapsed seconds of each are below; the nominal "about 10 / 20 minutes" are approximations. They were captured on the earlier runtime (b), before the workstation correction, are shown as separately attributed historical evidence, and were not repeated on the final build.

- `reception-offices-calm-f2a-palace-start.png`: Palace, a few seconds after load (actual: 6 s after load); build: earlier runtime (F2a); published file sha256 `b3078032acefe9cbe90988b4d8ed0eeb4d98395c9dc693ceae4d0c45c92264f9`
- `reception-offices-calm-f2a-palace-10min.png`: Palace, about 10 minutes after load (actual: 603 s after load); build: earlier runtime (F2a); published file sha256 `b8e28535123b7e57f53a1dbd35b37d7d633f44112525aed932591de989ec977d`
- `reception-offices-calm-f2a-palace-20min.png`: Palace, about 20 minutes after load (actual: 1203 s after load); build: earlier runtime (F2a); published file sha256 `d6ca3669be63e30c193e7c60c9aaeaec53d16e868a30d470e1dc03508d851d46`
- `reception-offices-calm-f2a-atrium-start.png`: Botanical Atrium, a few seconds after load (actual: 9 s after load); build: earlier runtime (F2a); published file sha256 `7d4bfcfa5bf00647a7c2e85579382e788047a6e3940bb7a89d3b74295ffb189c`
- `reception-offices-calm-f2a-atrium-10min.png`: Botanical Atrium, about 10 minutes after load (actual: 605 s after load); build: earlier runtime (F2a); published file sha256 `0f606bac61126cc1bf02c32a8e6a6c94f15129c9ec18e3ad95f8b7cd4ae9d5ec`
- `reception-offices-calm-f2a-atrium-20min.png`: Botanical Atrium, about 20 minutes after load (actual: 1205 s after load); build: earlier runtime (F2a); published file sha256 `a480e148274c6e52c28f1d603d84020b07799fa10286ba62f3f911f886d9f2c9`
- `reception-offices-calm-f2a-observatory-start.png`: Deco Observatory, a few seconds after load (actual: 11 s after load); build: earlier runtime (F2a); published file sha256 `620aa92831d9c779f92fb0187284be718dcf7d5514c269d6ec588d287b0593e8`
- `reception-offices-calm-f2a-observatory-10min.png`: Deco Observatory, about 10 minutes after load (actual: 607 s after load); build: earlier runtime (F2a); published file sha256 `d86140273b0fb657ae83aa72da9d3baec48c365649645872fe99e87b405811ec`
- `reception-offices-calm-f2a-observatory-20min.png`: Deco Observatory, about 20 minutes after load (actual: 1208 s after load); build: earlier runtime (F2a); published file sha256 `49cb263c7f9c0a696dfdddd6e89ffa035df82c873e88ddb80b54313e621a0db3`

The three "start" pictures were taken 6, 9 and 11 seconds after load because the three offices are photographed one after another. The time-lapse ran while other captures used the same machine, so real-time frame rate may have been shared. The simulated-second measurements (121 samples, one every 10 simulated seconds) are a separate series in `docs/pixel-agents-office.md`; the two axes are never compared.

## What these pictures do and do not show

- Each picture is one instant: where agents and residents happened to be, not a promise about every moment. The legend ("Ambient residents · 145 · decorative") and the fixture banners are the product's own.
- Nothing here is Mission success, delivery or live concurrency, and nothing here is an acceptance by anyone. Acceptance with real identities is a separate, later step.
- Capacity and what a workstation is: see the office guide. In the shipped world 188 workstations exist (Palace 44, Atrium 72, Observatory 72); 57 table seats and 59 lounge chairs are used only after every workstation is taken, for a FRESH placement; an agent whose valid held or identity-preferred seat is a table seat or a lounge chair keeps it.
