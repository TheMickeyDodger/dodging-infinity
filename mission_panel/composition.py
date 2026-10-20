"""ONE composition of the Mission request stack, used identically by the panel
(``dimission_panel.py``) and by the progression runtime (``dirun`` in its
MissionCore mode).

Both must act on the same stores, through the same bridge, under the same
per-Mission run guard, or "the runtime advances what the panel started" would be
a claim about two different worlds. So there is exactly one place that builds
them from the four configured locations, and both entry points call it:

- the Mission Core store and the local_request store, in ``state_directory``;
- the panel journal, in the same directory (it is what says which requests are
  this panel's);
- the ``MissionBridge`` over the control repository and the workspace pair, with
  the real spawn bridge and read-only observers, and a ``RunGuard`` over the
  same state directory so the panel's explicit Start and the runtime's passes can
  never act on one Mission at the same time;
- the composition's identity, a digest of the four resolved locations, which the
  runtime publishes and the panel compares, so a runtime started against other
  locations is reported as that and never as this panel's.

A composition without a workspace pair has no bridge: it can read and propose
and can start and advance nothing.
"""

import os
import time

from local_request import cli as request_cli
from mission_panel import journal as journal_module
from target_runtime import mission_progress_status as progress_status
from target_runtime import mission_run_guard as run_guard_module


class Composition(object):
    """What the two entry points share. ``bridge`` is None without a workspace
    pair."""

    def __init__(self, surface, journal, bridge, digest, state_directory):
        self.surface = surface
        self.journal = journal
        self.bridge = bridge
        self.digest = digest
        self.state_directory = state_directory


def compose(state_directory, control_repo, workspace_repository=None,
            workspaces_root=None, clock=None, bridge_factory=None):
    """Build the stack. ``bridge_factory(missions, control_repo, clock,
    run_guard)`` exists ONLY for hermetic tests, which substitute the bridge's
    effect seams (the spawn, the observer) and nothing else; production passes
    nothing and gets the real bridge."""
    clock = clock or (lambda: int(time.time()))
    os.makedirs(state_directory, mode=0o700, exist_ok=True)
    captured = {}
    factory = None
    if workspace_repository is not None:
        run_guard = run_guard_module.RunGuard(state_directory)

        def factory(missions, control, clk):
            if bridge_factory is not None:
                captured["bridge"] = bridge_factory(missions, control, clk, run_guard)
            else:
                captured["bridge"] = request_cli.configured_bridge_factory(
                    workspace_repository, workspaces_root, run_guard)(
                        missions, control, clk)
            return captured["bridge"]
    surface = request_cli.build_surface(state_directory, clock, control_repo, factory)
    return Composition(
        surface, journal_module.PanelJournal(state_directory),
        captured.get("bridge"),
        progress_status.composition_digest(
            state_directory, control_repo, workspace_repository, workspaces_root),
        state_directory)
