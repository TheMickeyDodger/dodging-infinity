"""P1-A6 Verified PR Delivery: the bounded, durable delivery state machine.

A separately human-authorized, Herdr-COMPLETE, canonical-Reviewer-APPROVE,
independently verified engineering result is delivered exactly once as a
GitHub pull request, without repeated human terminal approvals and without
any merge, tag, release, deploy, or publish authority.

Package boundaries, stated once here and pinned by ``tests/test_static.py``:

- The package imports nothing from the orchestration engine (``herdr``)
  and names no ``.herd`` path; it consumes recorded evidence references.
  The dependency runs ``herdr.guards -> pr_delivery.receipts`` only.
- ``transport.py`` is the ONLY module that starts a process. Every other
  module is pure logic over the validated record.
- Only ``cli.py`` constructs the real transport and the machine, and only
  ``cli.py`` mints a PR Delivery Authorization — after a human ceremony.
- The Mission Authorization stays a separate record with
  ``delivery_authority: "none"``; nothing here reads or writes the
  workflow store.
"""
