# Operational reference

Back to [Wiki home](../wiki/Home.md) · [Project README](../../README.md)

**[IMPLEMENTED / PROVEN]** This directory documents what ships in the
v0.7.0 tree: commands, settings, gates, transports, and the evidence
behind them. It is the operating surface that used to live in the README,
preserved in full and moved here so the README can stay a front door.
These pages document what ships today. Where a page names a planned
migration or a known limit, that paragraph carries the `PLANNED / TARGET`
label; where it names a current implementation that the target
architecture replaces, it carries `REFERENCE / FALLBACK`. Both labels are
defined on the wiki [Home](../wiki/Home.md) page.

## Purpose

Two rules governed the move from README to this directory:

- Content and technical exactness are preserved. Commands, flags, paths,
  JSON keys, counts, SHAs, and run ids are unchanged.
- Punctuation was normalized. Nothing else was.

If a command here disagrees with `herdctl --help` in your checkout, the
help output wins and this page is stale.

## The pages

| Page | Contents |
|---|---|
| [herdr-operations.md](herdr-operations.md) | Repository setup, presets, rules, the mission contract, task lifecycle, Strict Reviewer protocol, heartbeat, multi-repo use, the full command surface, target topology, command safety, Claude Auto Mode, and the direct mission workflow. |
| [human-git-gates.md](human-git-gates.md) | The commit, push, and release-tag gates: why they are separate, what each binds, and what none of them authorizes. |
| [telegram-remote-operator.md](telegram-remote-operator.md) | Telegram as the current reference transport: the remote experience, setup detail, transport, interaction, approval binding, recovery, delivery authority today, and security requirements. |
| [codex-gateway.md](codex-gateway.md) | Codex Gateway v0.1: what it is, isolation, non-goals, live compatibility validation, and the command surface. |
| [runtime-and-host.md](runtime-and-host.md) | The `dirun` Runtime service and the always-on Mac host. |
| [observability.md](observability.md) | `doctor`, `status`, `health`, and how they relate to `herdctl observe`. |
| [release-evidence-v0.7.0.md](release-evidence-v0.7.0.md) | The release lineage from v0.6.1 to v0.7.0 and the evidence trail, including the historical external-target mountain. |

## What is not here

- The `herdctl observe` schema v3 contract, the model-observability
  statement, and the hard observation bounds stay in the
  [README](../../README.md#herdctl-observe), where they are pinned by
  `tests/test_docs_i8.py`.
- The DI-REMOTE-2 architecture and its proof boundary stay in the
  [README](../../README.md#remote-target-repository-routing-di-remote-2-v070).
- Target design lives in the [wiki](../wiki/Home.md), not here.
