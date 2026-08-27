"""DI-REMOTE-2 Runtime: deterministic target lifecycle, no authority.

The Runtime is its own local process (supervisor ruling E-1), coupled
to the control chain ONLY through the durable workflow authority
store: the Telegram adapter records a consumed authorization; the
Runtime claims it under the store lock and advances the workflow —
materialize, prepare, handoff-validation — ending, in this increment,
at "cleared to dispatch" (phase VALIDATED).

Structural properties, enforced by the static suite and behavioral
tests, not by prose:

- NO arbitrary-command surface: the only subprocess boundary is the
  constructor-injected git transport, whose argv is built from fixed
  literals plus values resolved from the protected record.
- ``delivery_authority: none`` is structural: no commit, push, PR,
  tag, release, deploy, merge, or shell invocation exists anywhere in
  this package, and a full lifecycle leaves the managed workspace
  with zero staged entries, zero new revisions, and nothing sent to
  any remote.
- The control chain (``telegram_operator``, ``codex_gateway``) must
  never import this package; the behavioral probe in the static suite
  enforces that, and is load-bearing now that this package exists.
- Recovered durable state is adversarial input: every claim
  re-validates the record, its rendered-binding lines, and the LIVE
  control repository's policy digest before any target work.
"""
