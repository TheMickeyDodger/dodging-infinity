# Design workflow for the DI World web UI (Impeccable)

How a later herd runs design review and design work on `integrations/pixel-agents/webview-ui/`. Product context is `PRODUCT.md`; the design system
is `DESIGN.md` (both at the repository root). This file is the durable instruction set; working records live under `.herd/state/`, which is not
committed and must not be the only place these instructions exist.

## Status (read first)

- **Pinned and inspected:** the sources below, with hashes and licence.
- **Prepared** (files only; nothing installed, linked or hooked): the 58-file Claude harness tree at `.claude/skills/impeccable/`, unchanged from the
  pinned archive (`diff -r` identical), plus `PROVENANCE.md` and `scripts/bin/.gitignore` beside it. On this machine the checksum-verified darwin-arm64
  engine sits at `scripts/bin/darwin-arm64/impeccable`, ignored by git. The prepared files are new, currently untracked, and intended for version control.
- **Project config:** `.impeccable/config.json` (`updateCheck: false`, `stalenessCheck: false`) at the repository root. The root `.gitignore` keeps its
  broad `config.json` rule (those files hold secrets) with ONE anchored exception, `!/.impeccable/config.json`, so this file is no longer ignored: it
  is new, currently untracked, and intended for version control, which is what makes these settings apply for later herds.
- **Design and product root for the webview:** `integrations/pixel-agents/webview-ui/DESIGN.md` and `integrations/pixel-agents/webview-ui/PRODUCT.md`,
  regular byte-identical copies of the root files. Both are guarded by ONE test, `webview-ui/test/designMdDrift.test.ts`, which fails on any
  drift in either file or on a symlink. Proof that the engine now finds both: a contained `context` run resolved
  `productPath: integrations/pixel-agents/webview-ui/PRODUCT.md` and `platform: web`. Before the PRODUCT.md copy, it reported `productPath: null`
  and asked for `init`, a false negative of the same walk-up. Never run `init` to fix that: it writes a second, authored PRODUCT.md.
- **First detector run (2026-11-05, contained, no browser, no ports):** `detect --json integrations/pixel-agents/webview-ui/src` in the working tree
  and in a HEAD `5a1585b` export, inside the containment described below. Both exited **2** with empty stderr. HEAD: 2 primary findings. Working
  tree: the same 2 plus 1 primary and 67 advisories, all from the `design-system-*` rules, which could run only where a DESIGN.md exists. Every
  `.css` scanned is unchanged from HEAD, so those findings are pre-existing values newly visible, not new code. The extra primary was a DESIGN.md
  gap: its typography frontmatter did not declare FS Pixel Sans. That entry (`typography.pixel`) was then added, and a rerun over the working tree
  dropped exactly that finding: 69 remain, 2 primary (both present at HEAD) and 67 advisory. **Exit 2 is not a clean run**, before or after the fix.
  Each remaining finding is recorded as an open existing-rule conflict (or, for Inter, kept as documented); none is suppressed and none restyled. No
  home-directory or repository write was found. The raw output, the record and the dispositions are local herd evidence
  (`.herd/state/verification/d0/detector/`, not committed); a later herd must rerun rather than cite them.
- **Final detector gate for B3 (2026-11-08):** the configured scan and the unconfigured scan (`--no-config`, which also disables DESIGN.md) are each run, contained (no network, no write outside the run's own directory), over the final source, with the source files AND the detector's context hashed before and after: the engine binary, its VERSION file and engine-checksums.txt, SKILL.md, PROVENANCE.md (the pinned sources), `.impeccable/config.json`, DESIGN.md, PRODUCT.md and this document. A count is only comparable against a stated context, so the context is recorded with the count. Exit 2 is NOT clean. The earlier runs (a1, and a2 on the final source before this documentation refresh) are kept as the historical runs they are: a2 hashed its context, a1 hashed only the source, so a claim that the configuration had not changed since a1 rested on modification times and not on a hash comparison. The final pass records its own figures and context hashes in its own run record, and this document cites that record and never embeds a figure the next scan would falsify: `.herd/state/verification/b/b3-detector/` (retained, not committed). Dispositions: the side-tab warning and the design-system advisories in the operational code-review modal and the operational panel are in surfaces the product face never renders; one advisory is a rule no component uses (`.di-scan-chevron`); the Inter warning is the DI chrome's documented face; none of the advisories that the raw all-src scan carried on the named product surfaces remains. Nothing was suppressed. The Impeccable overlay's findings on the final pages were each read at the detector's own target in the state they came from: the "positioned child clipped by overflow container" rule is containing-block-blind (the flagged menu lies outside the flagged aside and every item hit-tests), the one real finding (a skipped heading level) was repaired in its callers, and the rest were declined with their measurements or retained (DEGRADED: same-team, single-context; retained, not committed).

## Pinned sources

| What | Pin | Integrity |
|---|---|---|
| Skill sources (the Claude harness tree `.claude/skills/impeccable/`, 58 files) | `pbakaus/impeccable` tag `cli-v4.8.0` → commit `414603bf14c7a344edb466bb1f85690c77ce8b7d`, archive `https://codeload.github.com/pbakaus/impeccable/tar.gz/414603bf14c7a344edb466bb1f85690c77ce8b7d` | archive sha256 `362136338f03f251c30f84d437e7111cf20a6c7857aed068774ec43fcc9f8e8c`; licence Apache-2.0 |
| npm launcher (inspected, not used to install) | `impeccable@4.8.0` | sha256 `e2fd5ace80fd0d8298d7f4f348aacde443fed6b6a6081e6f6bd35f4e476b9c75`; sha512 `e0+wbJppJMiQN8npKmIkBbKetfSlarak7OIGlsRhc8folekz4PgStjuprhYi+imjqBb6OCMJLV9II2hHGmcCyg==` |
| Engine binary, darwin-arm64 | `@impeccable/cli-darwin-arm64@0.1.18`; tag `engine-v0.1.18` → commit `f29cd24fdecfa5612e35e34c1c868d4a3fca930c` | binary sha256 `543ee2f72036ad185dc9991ac3ba86b74d91f988f3d497776a088b8b65c8a9e1`, equal to the skill's own `scripts/engine-checksums.txt` |

**Limitations of these sources:** the npm package carries no skill files (it is a launcher plus a checksum list). The skill tree comes from the
GitHub archive of the tag's commit; npm records no `gitHead`, so the archive and the published package are not cross-verified. The 17 MB engine
binary is not vendored in git. **Wiring:** the Claude Code harness tree at `.claude/skills/impeccable/` (prepared and untracked; see Status). The Codex variant
(`$impeccable` prefix) is not planned.

## How to invoke (Claude Code)

The skill is `user-invocable`; its argument is the command. Mode for every DI surface: **Operate**.

1. **Once per session, load context** (cwd at the repository root): `.claude/skills/impeccable/scripts/impeccable context`. It reads `PRODUCT.md`
   and `DESIGN.md`. Do not rerun it in the same session.
2. **Plan before building:** `/impeccable shape <feature>`.
3. **Evaluate without changing code:** `/impeccable critique <target>` (UX review with heuristic scoring; report saved under
   `.impeccable/critique/`) and `/impeccable audit <target>` (accessibility, performance, responsive). Record every finding **and its
   disposition**; a declined finding is a recorded decision.
4. **Refine:** `/impeccable clarify <target>` (UX copy, labels, error messages), `/impeccable polish <target>` (final pass before shipping),
   `/impeccable harden <target>` (errors, i18n, edge cases). Visual `polish` and `harden` follow the design selection, not before it.
5. **Mechanical detector** over the changed web UI: `.claude/skills/impeccable/scripts/impeccable detect --json <targets>`. Exit 0 = no primary
   findings, 2 = primary findings, 1 = a target could not be scanned. Record the exact targets, the exit code and the real output, never a summary.

## Environment for every run

- `IMPECCABLE_NO_UPDATE_CHECK=1`, `IMPECCABLE_NO_TELEMETRY=1`, `DO_NOT_TRACK=1`.
- `IMPECCABLE_UPDATE_CACHE` and `IMPECCABLE_STALENESS_CACHE` pointed at an ignored scratch path, and `IMPECCABLE_HOME` likewise if the launcher's
  cache is involved. These are the engine's own documented settings.
- **Never repurpose `HOME`, `CODEX_HOME` or any home-directory variable.**
- Documented engine writes and calls (pinned `docs/CLI-CONTRACT.md`, `docs/RUNTIME-ENV.md`): the update check during `context` (network, and
  `~/.impeccable/update-check.json`); staleness notices (`~/.impeccable/staleness-check.json`); `impeccable telemetry off` (`~/.impeccable/config.json`);
  concept seeds (`~/.impeccable/seed-salt`); choice pings. Setting `IMPECCABLE_BIN`, or providing the sibling binary at
  `scripts/bin/<os>-<arch>/impeccable`, avoids **only the launcher's** cache lookup and download. It does **not** prove the engine makes no
  home-directory writes. Until a contained run has established what the engine writes, run it inside containment that denies writes outside one
  scratch directory (for example macOS `sandbox-exec`) and record any denial.

## The detector and DESIGN.md

The detector resolves a design system per scanned file by walking up from that file's directory. It stops at the first directory that has a project
marker (`.git`, `package.json` or `.impeccable`) and no DESIGN.md. For files under `integrations/pixel-agents/webview-ui/` that is
`webview-ui/package.json`, so the root `DESIGN.md` is **not** applied to them as is, and the `design-system-*` rules would not run. Before a detector
result on the webview is treated as checking the design system, the design root must be resolvable from the webview, and the run must show that
the design system was loaded.

**Resolution (made):** a **regular** file `integrations/pixel-agents/webview-ui/DESIGN.md`, a byte-identical copy of the canonical root `DESIGN.md`,
with the drift check `webview-ui/test/designMdDrift.test.ts` that fails when the two differ or the copy is a symlink. Edit the root file, then copy it
over the webview one. PRODUCT.md follows the same rule, for the same reason (the `context` command's walk-up stops at the same marker). The `design-system-*` rules read DESIGN.md's **frontmatter** (`colors`, `typography`, `rounded`, `spacing`), not its prose: a face
or value the prose describes but the frontmatter omits is reported as undeclared. **Not a symlink:** with `core.symlinks=false` Git checks a symlink out as a small
text file holding its target path, so checkout succeeds while the detector would read a pathname instead of design content. A pointer-only Markdown
file is not enough either, unless the detector is shown to follow it. Changing the invocation directory does not help: the walk starts from each
scanned file's own directory.

## Verification method: which tests read a file as text

**Before claiming that no test reads a source file as TEXT, check STRUCTURALLY, never by filename.** A filename grep finds tests that NAME a file and
misses tests that SWEEP a directory; it has been wrong twice here (`DiScanPages.tsx`, `diModalStack.ts`).
1. Find every directory sweep in the test tree: `readdirSync`, `readdir`, `opendirSync`, `walk(`/`sources(`-style helpers, `glob`/`globSync`/`fg`,
   `recursive: true`.
2. Resolve each sweep's ROOT and extension filter, and list the sweeps whose root contains the file and whose filter matches it.
3. For each such reader, check what it does with the text: whether it strips comments, and how (`diArchiveCountPin` blanks comments to spaces, so
   offsets and whitespace shift; `diMissionRequestStacking` removes block comments and WHOLE-LINE `//` comments only, so an end-of-line `//` comment
   survives).
4. A comment-only change is invisible to a reader only if it is invisible under THAT reader's own strip. Show it (equal stripped text, or an equal
   token sequence), or run the reader.

## Verification method: a failing run proves only what it reached (round 10 of B2a)

**Before citing a FAILING run as proof of a claim, confirm the run REACHED the assertion that carries that claim.** A run that dies early is evidence
about where it died, nothing more. The shape has recurred four times on this Mission: a run fails for reason A, the claim is B, and the failure is read
as proof of B (a capture taken before asserting the state it was named for; a probe that measured the wrong edge and reported overflow 0; a mutation
that was transient, so both arms passed and discriminated nothing; an isolation run that timed out on a missing element before its own scroll
assertion).
1. Name the assertion that carries the claim (file and line) BEFORE running.
2. After the run, read WHERE each test failed (the `❯ file:line` and the error): a timeout or a missing element before that line is not the claim.
3. If the claim's assertion is not reached, either restructure so it is (one claim per test; no earlier step that depends on the very thing the
   counterfactual removes), or say plainly that the claim is asserted by the passing run but not isolated. Never cite the early death as the proof.
4. Record the measured values at the claim's assertion (put them in the message), so the log shows the comparison actually ran.

## Verification method: seen is necessary and NOT sufficient (B3, 2026-11-08)
**A behaviour claim is the complete predicate: the destination is the INTENDED one, it is SEEN, and the state the control was meant to change HAS changed.** Each half alone passed a defect this Mission: identity without seen let a restore that left focus 1289px below the visible area pass; seen without identity or transition let a Back route that did nothing pass (focus began on a seen control and never left it); and presence without drawn geometry let a menu that was 14px wide pass 140 tests. Put the moved/named/seen assertions in separate checks, COLLECT every problem and assert once so a red run reaches each of them, derive an expected scroll or position from geometry read BEFORE the change, never from the result, and name the browser that established each claim: the Chromium primary matrix and the selected Brave states are different matrices, and one is never cited for the other. A walk is real keys from a natural pointer setup (a click into a field, the initial page): a programmatic landing is not a walk, and the initial focus is read before the first key, because a target the product already focused costs zero presses and an artificial lap is not a product cost.

## Verification method: what each kind of change needs (B3, 2026-11-08)
**The underlying rule is to gate every check on whether it could fail for the thing being claimed.** What a change needs follows from what it claims; there is no one rule for all:
- **A claimed behaviour REPAIR needs a TWO-SIDED RED:** a cell that FAILS on the bytes that have the defect, at the same assertion that PASSES after the repair. In B3 the Code Diff More menu's sliver, the Back route to the Mission list and the focus landing after Clear info each have one.
- **A REGRESSION GUARD must be shown capable of failing for the behaviour it claims.** The admissible evidence is any one of: a KNOWN NEGATIVE (a state where it does fail, already on record), a MUTATION (a deliberate break), or an INDEPENDENT COUNTEREXAMPLE (a separate observation that the guard would have caught). A guard on behaviour that was already correct has no red to show, so it needs one of these, not a red. In B3 the request workspace's notice Dismiss guard passed on the bytes before and after, and a deliberate defect in its focus keeper made it fail 3 of 3, at 1280, 1440 and 1600: that is one admissible proof, an example and not the required form.
- **A WORDING-ONLY change needs neither.** An over-specification that once demanded a red-first cell for a label was withdrawn, and it is the reason this clause exists: a test asserting that a label contains the model's own term passes by construction and proves nothing. Wording is verified by the existing checks and by the rendered inspection (in B3, the filter's label).
Do not write this as "every change needs a red" or "every guard needs a mutation": that costs runtime and produces no evidence. And a prepared check is not evidence until it has run once: a type-check and a search for the wrong identifier both passed a module that threw on its first call, because they did not model the bundler's own name wrapper (esbuild `keepNames`) around a function that Playwright serializes into the page.

## Verification method: removing a rendered element (B2a escape, 2026-11-05)

**When REMOVING a rendered element, structurally enumerate every test that asserts on its CONTENT.** The structural rule above applied to tests that
read a file as TEXT; it extends to tests that RENDER and assert on what is displayed. A removed element's real readers are every test asserting on
its strings, names, test ids, classes and meta line, wherever that test lives. An inventory judgement that a file "pins nothing relevant" is not a
structural check. (B2a removed Home's inline card; `diScanPanel.test.ts` asserted its swarm names and meta line, was listed as pinning nothing, was
never run, and failed on the accepted bytes.)
1. Render the element (before removal) and list what it displays: its visible strings (names, words, meta lines), its `data-testid`s and its classes.
2. Search the WHOLE test tree for each of them (the strings as the tests write them, including template pieces), not by file name or by plan.
3. Every hit is a reader: run it, before and after, and migrate it as an introduced update with its coverage named.
4. Deleting a visible element is a change whose blast radius a targeted run cannot bound: run the full suite before claiming the removal verified.

## Verification method: never overwrite a measurement artifact (B2b, 2026-11-05)

**Never overwrite a measurement artifact.** Every invocation that writes evidence (a log, a capture, a measurement file) writes to its OWN name or
directory; a run never reuses a path another run wrote. A superseded artifact is RELABELLED (an appended annotation, or a sibling note), never replaced.
Superseded evidence keeps being useful precisely because it still exists: it is how a later claim that depends on it can be re-examined (the v1 Brave
captures, the v1 action-row probe, the INCONCLUSIVE mutation and the invalid cut-2 images were each re-read after being superseded). B2b's cut-4
measurement file was overwritten by a narrower rerun, so its numbers survive only as assertions in a log.
1. Name every output after its run (or give each invocation its own directory), and check the target does not exist before writing.
2. A test that writes measurements must refuse to overwrite: write a fresh name if the file exists.
3. To correct an artifact, append a labelled correction, or write a new artifact and annotate the old one as superseded.
4. Copy aside BEFORE removing anything, and verify the copy exists (by hash) BEFORE the removal, not after. A plan that says "keep a copy" is not a
   copy (B2b round 13: a `patch` byproduct was removed by its recorded path without the planned copy, and its bytes were needed later).

## Verification method: content width is not capacity (B2b addendum, 2026-11-05)

**A measured element width is its CONTENT width unless the element is proven to fill its channel.** Capacity is measured as the CHANNEL,
separately from the content that sits in it, and a width that is identical across viewports is evidence of content-shrink, not of fit. In B2b the
Herdr notes block read 320 px at 1280, 1440 and 1600 alike: the notes row is `flex` with `space-between`, so the block shrinks to its text, and 320
was a one-line SYNTHETIC string's own width. A "~365" beside it was arithmetic, written as "measured", and was quoted onward as a measured fact.
1. Report THREE numbers, never one: the content's LINES, the content's WIDTH, and the CHANNEL's width, each measured on its own.
2. Measure the channel as the space the element can take: the container's content box (its box minus its computed padding) minus its siblings'
   measured boxes and the computed gaps, each read from the render, or a box proven to fill (`flex-grow`, `width: 100%`). Never infer it from the content.
3. Use the content the claim is about (a failing test's own text), never a short synthetic string, to say whether a real line fits.
4. A number derived by arithmetic is labelled COMPUTED, with its terms shown; only a reading from the render is labelled MEASURED.

## Verification method: a convention that is not enforced is half-applied (B2d, 2026-11-05)

**A convention stated in a comment and not enforced will be half-applied.** `tsconfig.node.json`'s comment stated a pairing: a browser test is
excluded there (that program has no DOM lib) AND included in a program that has it. It named two such programs, test-dom and test-city, and never
test-mission, which holds the tests that import the office's server modules. The rule by the configs' actual constraint is the guard's failure text
and `integrations/pixel-agents/CLAUDE.md` (Testing, Webview unit): a test that imports `server/src` needs `erasableSyntaxOnly` off, which only
test-mission and test-city give (they are compiler-identical, so which of the two is a CONVENTION, not a technical constraint: test-city holds the
living-city tests, test-mission the rest); any other test that needs
the DOM lib goes in test-dom; each is also excluded from node. Across this mission's stages (K1, B2b, B2c, F-b and B2d), 15
test files got the exclusion every time and the include never. `tsc -b` stayed green throughout, because a file in no program is checked by nothing.
Every packet's "tsc exit 0" was true for the programs as configured, and narrower than every reader took it to be. HEAD had none of these files; the
mission added all 15. Measured once found: under their proper settings all 15 type-checked clean. So the hole hid no error, and it gave no protection
going forward.
1. Enforce a convention with a check that fails, not with prose. `webview-ui/test/diTypecheckCoverage.test.ts` fails on any test source that no program
   `tsc -b` builds contains.
2. A guard reads the tool's OWN resolution (TypeScript's program membership, imports counted transitively), never the configuration's globs. Reading
   the globs is what produced an inverted model of what one exclusion line did.
3. A guard proves nothing until it is shown to fail. Give it a negative control, and prefer one against GROUND TRUTH over one against its own fixture.
   This guard ran on the real, unrepaired configuration and named exactly the 15 files an independent census (`tsc --showConfig`) had found; 14 or 16
   would have meant the guard was wrong. Its built-in control (a deliberate orphan in a fresh temp directory, never the repo) keeps it falsifiable on
   every run.
4. Read a green for its SCOPE: say what a passing check covered, not only that it passed.
5. Derive a rule from its source, never from the last statement of it. The "test-dom or test-city" heuristic was stated by the Executor, relayed
   unchecked by the Lead, and written into the guard's own failure message: three transmissions of one error, copied rather than verified. The fix
   read the configs (their resolved options compared, and each one's own text) and stated what SENDS a file to each program, not a list of names.
   It found there was no such rule: test-mission and test-city are compiler-identical, and both groups import the same server modules, so
   "server import goes to test-city" was a PATTERN someone observed and everyone then quoted. A choice with no technical constraint is stated AS a
   convention, or the next person hunts for a reason, finds none, and concludes the rule is arbitrary or wrong.
6. State a guard's LIMIT beside it. This one catches an orphan and cannot catch a mis-filing between two compiler-identical programs: a guard whose
   limits are unstated gets read as covering more than it does. And an error message is code that runs only when something is broken: render it in a
   passing run (this guard's controls render its failure text on their own orphan every run), or it can be wrong indefinitely.

## Never

`impeccable install`, `impeccable link`, `/impeccable hooks on`, any user-scope or global install, a git submodule, writes under `.agents/` or
`.codex/`, global settings or hooks, or changing `core.hooksPath`.
