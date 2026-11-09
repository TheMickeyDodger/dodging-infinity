# Provenance of this vendored skill

This directory is the Claude Code harness tree of **Impeccable 4.8.0**, vendored as plain files (no installer, no `link`, no hooks). How to use it, and
what never to run, is in `docs/design-workflow.md` at the repository root.

| | |
|---|---|
| Upstream | `https://github.com/pbakaus/impeccable`, tag `cli-v4.8.0` → commit `414603bf14c7a344edb466bb1f85690c77ce8b7d` |
| Archive | `https://codeload.github.com/pbakaus/impeccable/tar.gz/414603bf14c7a344edb466bb1f85690c77ce8b7d`, sha256 `362136338f03f251c30f84d437e7111cf20a6c7857aed068774ec43fcc9f8e8c` |
| Path in the archive | `impeccable-414603bf14c7a344edb466bb1f85690c77ce8b7d/.claude/skills/impeccable/` (58 files, copied unchanged; `diff -r` identical at staging) |
| Licence | Apache-2.0 (`SKILL.md` frontmatter `license: Apache 2.0`; the archive's `LICENSE`) |
| Skill version | `SKILL.md` `version: 4.8.0`; engine version `scripts/VERSION` = `0.1.18` |

Added here, not from upstream: this file, and `scripts/bin/.gitignore` (ignores everything in `scripts/bin/` except itself).

**The engine binary is not vendored in git.** The launcher (`scripts/impeccable`) uses, in order: `$IMPECCABLE_BIN`, the sibling
`scripts/bin/<os>-<arch>/impeccable`, `~/.impeccable/bin`, `$IMPECCABLE_HOME`, then a download into that cache. To keep the launcher's binary
lookup off that home-directory cache and its download path (the launcher only, not every engine run), place the sibling binary yourself, from `@impeccable/cli-<os>-<arch>@0.1.18` (npm) or the `engine-v0.1.18` release, and check its sha256
against `scripts/engine-checksums.txt`. For darwin-arm64 that is `543ee2f72036ad185dc9991ac3ba86b74d91f988f3d497776a088b8b65c8a9e1`.

The sibling binary only keeps the **launcher** away from the home directory. The engine's own documented home writes (the `context` update check and
staleness notices) are turned off by `.impeccable/config.json` at the repository root (`updateCheck: false`, `stalenessCheck: false`) or by the
environment variables in `docs/design-workflow.md`. The root `.gitignore` exempts exactly that file (`!/.impeccable/config.json`) from its broad
`config.json` rule, so it can be version-controlled.
