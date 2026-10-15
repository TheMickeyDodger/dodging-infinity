# Pixel Agents in Dodging Infinity: provenance and licensing

This directory is a vendored copy of [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)
carrying the Dodging Infinity (DI) office changes: the early, read-only DI office. How to
install, register, launch and trust it is in
[docs/pixel-agents-office.md](../../docs/pixel-agents-office.md). This file records where the
bytes came from, what was left out, which licences apply, and how to take upstream updates.

## Provenance

| | |
|---|---|
| Upstream repository | `https://github.com/pixel-agents-hq/pixel-agents` |
| Upstream baseline commit | `d1e007a9fdf3003c252d2973abe1999ae59aec33` (package version 1.4.1) |
| Imported from | the reviewed source-input archive of that baseline plus the DI office changes (432 files) |
| Imported here | 414 files, byte-identical to that archive except the three publication edits below |

### Files the DI office changes relative to the upstream baseline

These 72 paths differ from upstream `d1e007a`. Apart from the
[publication edits](#publication-edits-made-on-import) below, every other imported file is
unchanged upstream source.

- `core/asyncapi.yaml`
- `core/src/messages.ts`
- `e2e/di/bootstrapRecovery.spec.ts`
- `e2e/di/officeUsability.spec.ts`
- `e2e/di/playwright.di.config.ts`
- `e2e/di/realisticFixture.ts`
- `esbuild.js`
- `packaging/di/CREDITS.txt`
- `packaging/di/OFL-1.1.txt`
- `packaging/di/README.txt`
- `scripts/build-di-package.mjs`
- `server/__tests__/diBackendClient.test.ts`
- `server/__tests__/diBackendDiscovery.test.ts`
- `server/__tests__/diBrowserAccess.test.ts`
- `server/__tests__/diBuiltOfficeFixtureBackend.test.ts`
- `server/__tests__/diCatalogProjection.test.ts`
- `server/__tests__/diCatalogV2.test.ts`
- `server/__tests__/diClientMessages.test.ts`
- `server/__tests__/diIdentityMap.test.ts`
- `server/__tests__/diOffice.test.ts`
- `server/__tests__/diOfficeStateMessage.test.ts`
- `server/__tests__/diOfficeView.test.ts`
- `server/__tests__/diStateRoot.test.ts`
- `server/__tests__/diTransport.test.ts`
- `server/__tests__/diWriteContainment.test.ts`
- `server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json`
- `server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.frozen-9b3da846.93976694.FIXTURE.json`
- `server/__tests__/fixtures/di-backend/v3-canonical-fixture.FIXTURE.json`
- `server/__tests__/helpers/diFixture.ts`
- `server/src/agentSeats.ts`
- `server/src/clientMessageHandler.ts`
- `server/src/configPersistence.ts`
- `server/src/constants.ts`
- `server/src/di/backendClient.ts`
- `server/src/di/backendDiscovery.ts`
- `server/src/di/browserAccess.ts`
- `server/src/di/catalogContract.ts`
- `server/src/di/catalogProjection.ts`
- `server/src/di/diCli.ts`
- `server/src/di/diClientMessages.ts`
- `server/src/di/diHttpServer.ts`
- `server/src/di/diInputLimits.ts`
- `server/src/di/diOffice.ts`
- `server/src/di/diStateRoot.ts`
- `server/src/di/identityMap.ts`
- `server/src/di/officeStateMessage.ts`
- `server/src/di/officeView.ts`
- `server/src/di/serveTimeVerdict.ts`
- `server/src/fileStateAdapter.ts`
- `server/src/httpServer.ts`
- `server/src/layoutPersistence.ts`
- `server/src/server.ts`
- `server/src/socketPipe.ts`
- `server/src/stateRoot.ts`
- `webview-ui/src/App.tsx`
- `webview-ui/src/components/ConnectionIndicator.tsx`
- `webview-ui/src/components/DebugView.tsx`
- `webview-ui/src/components/DiOfficePanel.tsx`
- `webview-ui/src/components/SettingsModal.tsx`
- `webview-ui/src/components/diOfficeFormat.ts`
- `webview-ui/src/constants.ts`
- `webview-ui/src/hooks/useExtensionMessages.ts`
- `webview-ui/src/office/components/OfficeCanvas.tsx`
- `webview-ui/src/office/components/ToolOverlay.tsx`
- `webview-ui/src/office/engine/characters.ts`
- `webview-ui/src/office/engine/officeState.ts`
- `webview-ui/src/office/layout/diWorld.ts`
- `webview-ui/src/office/reducedMotion.ts`
- `webview-ui/src/testHooks.ts`
- `webview-ui/test/diOfficeFormat.test.ts`
- `webview-ui/test/diReducedMotion.test.ts`
- `webview-ui/test/diWorld.test.ts`

### Publication edits made on import

Three imported files were edited when they were imported here, for publication only. No
behaviour changed. These are the only imported files that differ from the source-input archive.

- `packaging/di/README.txt` (a DI-changed file): the backend sections now launch the DI
  catalog bridge from a DI source tree, where `dicatalog.py` and `dicatalog_http.py` sit at
  the repository root, instead of from a separately delivered backend package.
  Machine-specific store paths are replaced by `$HOME`-relative examples, and the scratch
  root by a `$TMPDIR`-derived one. The build section states how the current package was
  built. The file says which sections are adapted and which are verbatim.
- `packaging/di/CREDITS.txt` (a DI-changed file): says where the built-package paths it names
  (`dist/...`, `THIRD-PARTY-NOTICES.txt`) come from in this source tree. Every credit and
  licence statement is unchanged.
- `README.md` (upstream, otherwise unchanged): the three star-history chart URLs in the
  "Star History" section carried `sealed_token` query parameters. Those token values are
  removed, and the chart links are otherwise kept.

Three imported files carry upstream whitespace that this repository's CI whitespace check
(`git diff --check`) would reject: `.prettierignore`, `core/asyncapi.yaml` and
`scripts/generate-messages.ts`. They stay byte-identical. Instead,
[`../.gitattributes`](../.gitattributes) unsets the `whitespace` attribute for exactly those
three paths. Re-check that list after every upstream update.

## Not imported

18 files of the source-input archive are deliberately not part of this directory.

| Files | Reason |
|---|---|
| `.github/**` (10 files: workflows, issue templates, `dependabot.yml`, `FUNDING.yml`, PR template) | Upstream repository-hosting metadata. GitHub reads only the repository-root `.github/`, so nested copies never run; the publish and badge workflows target upstream's own registry and marketplace accounts. |
| `.husky/pre-commit`, `.husky/pre-push` | Upstream's git hooks. Dodging Infinity's git safety hooks are its own; nothing in this directory may install or redirect hooks. |
| `.git-blame-ignore-revs` | Names an upstream commit that does not exist in this repository's history. |
| `icon.png`, `webview-ui/public/Screenshot.jpg`, `webview-ui/public/banner.png`, `webview-ui/public/office.png`, `webview-ui/public/characters.png` | Excluded by the earlier W3 publication ruling: marketing and README composites, not runtime assets. `scripts/build-di-package.mjs` refuses to ship them. |

Consequences, accepted deliberately:

- Apart from the token removal above, the upstream `README.md` is unchanged, so its three
  images (`banner.png`, `office.png`, `characters.png`) do not resolve here.
- `package.json` still names `icon.png` for the upstream VS Code and npm packages. This
  directory builds only the DI office package, which never ships `icon.png`.

## Licensing

Dodging Infinity is Apache-2.0. That licence does **not** cover this directory: Pixel Agents
keeps its own licences, as follows.

| What | Where | Licence and basis |
|---|---|---|
| Pixel Agents code and documentation | everything here unless listed below | MIT, [LICENSE](LICENSE), Copyright (c) 2026 Pablo De Lucca |
| Character sprites | `webview-ui/public/assets/characters/char_0..5.png` | Derived from JIK-A-4 "MetroCity — Free Topdown Character Pack", declared CC0 1.0 by its source. Attribution is kept deliberately in [packaging/di/CREDITS.txt](packaging/di/CREDITS.txt). |
| Pixel font | `webview-ui/public/fonts/FSPixelSansUnicode-Regular.ttf` | FS Pixel Sans Unicode Regular by NZWStudios2024, SIL Open Font License 1.1, **not** MIT. Licence text: [packaging/di/OFL-1.1.txt](packaging/di/OFL-1.1.txt). The font's own name table carries `Copyright NZWStudios2024 2025` and `Open Font License`. The binary is kept byte-for-byte. |
| Furniture, floor, wall, carpet and pet art and their manifests | `webview-ui/public/assets/{furniture,floors,walls,carpets,pets}/**`, `default-layout-1.json` | Part of the MIT-licensed upstream repository. Upstream replaced its earlier proprietary tileset with open-source assets (upstream CHANGELOG v1.1.0, #117); the pet sprites were contributed to it (upstream CHANGELOG contributors list). |
| npm dependencies | installed into `node_modules/`, never committed | Each package's own licence. The DI office package records the code bundled into its shipped files in a generated `THIRD-PARTY-NOTICES.txt`, and ships its runtime packages with their own licence files. |

Keep `LICENSE`, `packaging/di/CREDITS.txt` and `packaging/di/OFL-1.1.txt` with this directory.

## Updating from upstream

There is no automatic update. To take a newer upstream release:

1. In a separate clone of the upstream repository (never inside this checkout), diff the
   baseline `d1e007a9fdf3003c252d2973abe1999ae59aec33` against the new upstream commit.
2. Apply that upstream diff here. Resolve conflicts in the 72 DI-changed files above in favour of
   the DI office behaviour, and leave the [Not imported](#not-imported) files out again.
3. Review any new or changed image, font or other binary asset for a stated licence basis
   before adding it, and record it in [Licensing](#licensing).
4. Update the baseline commit in [Provenance](#provenance) and, if the version changes, the
   package name the docs mention.
5. Reinstall, rebuild and retest as in
   [docs/pixel-agents-office.md](../../docs/pixel-agents-office.md#install-and-build), with
   `HUSKY=0` and the `core.hooksPath` check before and after every install.
6. Rebuild the DI office package, record its new sha256, and treat it as a new package.
