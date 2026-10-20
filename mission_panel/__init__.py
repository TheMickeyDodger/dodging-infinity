"""The DI World mission-request panel's backend: a bounded, authenticated,
loopback-only interface between the panel and the EXISTING lifecycle.

It adds no authority and no second execution engine. The conversation is the
restricted operator session; the proposal, the revision and the digest are
Mission Core's own, through the local request surface; the decision is the
surface's operator-attested approval (an exact affirmative the human typed);
dispatch is the surface's run route over the existing ``MissionBridge``.
``mission_panel.journal`` is bookkeeping only: it is never the source of truth
for a proposal, a decision or a run.
"""
