"""The Runtime-backed ``Worker``: exact delegation, nothing else.

``RuntimeWorker`` wraps the ``target_runtime`` host modules behind the
neutral ``worker`` contract, named like its two siblings that wrap
``target_runtime`` modules behind a neutral contract
(``RuntimeCapabilityAuthority``, ``RuntimeDurableExecution``). It is
not host-specific beyond that: nothing here is macOS-specific, and
nothing here asserts same-process locality the contract does not
state.

Each method resolves its host function as an ATTRIBUTE of the
``target_runtime.workspace`` or ``target_runtime.workspace_trust``
module object at call time, never bound at import time or in the
constructor, so those modules stay the single point of substitution:
replacing ``workspace.release`` on the module is what this adapter
picks up. The constructor binds the transport, the managed workspace
root, the configuration path, and the three optional host callables,
and does no I/O: it touches no directory, reads no configuration
file, and creates no store. Every return value and every exception
passes through unchanged.

The two presence properties are computed from the bound callables
and are not settable state, so they cannot drift from what is
actually wired. A ``RuntimeWorker`` whose live projection or close
callable is absent raises the same ``TypeError`` on that call that
calling ``None`` raised before this seam existed; the Broker checks
the presence property first on every production path, and the one
combination that still reaches the call (close wired, projection
absent) is recorded behavior the Broker's own handler catches.

Test hooks are deliberately NOT carried through the seam, following
the ``nonce_factory`` precedent in ``capability_authority``:
``materialize``'s ``lease_id_factory`` and ``establish``/``revoke``'s
``sleeper`` and ``clock`` have no production caller and stay
module-level hooks on their own modules. The seam has exactly the
calls the production call graph makes, with the bound inputs removed.

The two production host readers below (``_production_readiness_probe``
and ``_production_live_workspaces``) live here as this
implementation's production defaults; ``target_runtime.cli`` hands
the live projection in by name and the Broker falls back to the
readiness probe when none is injected. They carry no Broker state.
"""

from target_runtime import workspace as workspace_module
from target_runtime import workspace_trust as workspace_trust_module

from worker.contract import Worker


def _production_readiness_probe(lease_repo):
    """The production BOOTSTRAP-READINESS probe (I3).

    Read-only and deliberately SEPARATE from `_production_observer`:
    that observation runs with `probe_agents=False`, so its `agents`
    section carries no live agent facts, and widening it would both
    reproduce the recorded agents-unprobed PARTIAL defect and loosen
    `_probe_one`'s allowlist on a path that feeds a model. This probe
    instead reads the leased workspace's own runtime mapping and the
    live agent registry, and returns `{logical: agent_record}` for the
    logical roles that mapping names.

    Within this function a target it is unable to see yields None,
    which the readiness layer records as UNOBSERVABLE rather than as a
    failure; outside it, what that absence means is the readiness
    layer's decision and not this probe's.
    """
    import json
    import os
    state = os.path.join(lease_repo, ".herd", "state", "runtime.json")
    try:
        with open(state, encoding="utf-8") as handle:
            agents = json.load(handle).get("agents")
    except Exception:                                     # noqa: BLE001
        return None
    if not isinstance(agents, dict):
        return None
    from herdr.tasks import run
    completed = run(["herdr", "agent", "list"])
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        listed = json.loads(completed.stdout)["result"]["agents"]
    except Exception:                                     # noqa: BLE001
        return None
    by_name = {
        record.get("name"): record
        for record in listed
        if isinstance(record, dict)
    }
    probe = {}
    for logical, name in agents.items():
        record = by_name.get(name)
        if record is not None:
            probe[logical] = record
    return probe


def _production_live_workspaces():
    """READ-ONLY projection of live workspaces and the agents in them.

    Built by JOINING two Herdr listings, because neither alone
    carries what the ownership proof needs: `herdr workspace list`
    reports `workspace_id`, `label` and pane counts and has NO agent
    mapping, while `herdr agent list` reports each agent's `name` and
    its `workspace_id`. The join therefore yields, per workspace, the
    SET OF AGENT NAMES currently in it.

    Read-only in the strict sense: it lists and it returns: no
    workspace, pane or session is created, focused, renamed or closed
    here. Returns None when either listing is unreadable, which the
    proof treats as degraded evidence and refuses on.
    """
    import json
    from herdr.tasks import run
    try:
        listed = run(["herdr", "workspace", "list"])
        agents = run(["herdr", "agent", "list"])
    except Exception:                                     # noqa: BLE001
        return None
    if getattr(listed, "returncode", 1) != 0:
        return None
    if getattr(agents, "returncode", 1) != 0:
        return None
    try:
        workspaces = json.loads(
            listed.stdout
        )["result"]["workspaces"]
        agent_rows = json.loads(agents.stdout)["result"]["agents"]
    except Exception:                                     # noqa: BLE001
        return None
    # R-32 X-1: # A ROW THIS IS UNABLE TO READ MAKES THE WHOLE PROJECTION DEGRADED.
    # Skipping it is outside what this function may do.
    #
    # Skipping looks harmless and is not. The ownership proof compares
    # the live agent NAME SET against the recorded one for EQUALITY,
    # so its strength rests entirely on that set being COMPLETE. A
    # silent skip makes it a possibly-strict subset, and while the
    # usual consequence is a false refusal, the dangerous one is a
    # truncated set that happens to EQUAL the recorded one — which
    # reports OWNED and closes a workspace containing live agents
    # nobody recorded. On a machine of fifteen workspaces, one ours,
    # that is unrecoverable.
    #
    # So this returns None — the degraded value the consumer refuses
    # on — rather than a narrower answer presented as a complete one.
    # # `observe_spawn_records` already takes this posture for the same
    # reason: within it a malformed record makes the WHOLE projection
    # malformed.
    by_workspace = {}
    for row in agent_rows:
        if not isinstance(row, dict):
            return None
        workspace_id = row.get("workspace_id")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            return None
        if workspace_id is not None and not isinstance(
            workspace_id, str
        ):
            return None
        if isinstance(workspace_id, str):
            by_workspace.setdefault(workspace_id, set()).add(name)
    projection = []
    for workspace in workspaces:
        if not isinstance(workspace, dict):
            return None
        workspace_id = workspace.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            return None
        projection.append({
            "workspace_id": workspace_id,
            "agent_names": by_workspace.get(workspace_id, set()),
        })
    return projection


class RuntimeWorker(Worker):
    """Delegate the seam calls to the ``target_runtime`` host modules
    over ONE bound transport, workspace root, and configuration path:
    the same values the Broker binds, so the workspace this worker
    materializes is the one the Broker's ownership predicates check
    containment against."""

    def __init__(self, transport, workspaces_root, config_path,
                 readiness_probe_fn=None, live_workspaces_fn=None,
                 workspace_close_fn=None):
        self.transport = transport
        self.workspaces_root = workspaces_root
        self.config_path = config_path
        self._readiness_probe_fn = readiness_probe_fn
        self._live_workspaces_fn = live_workspaces_fn
        self._workspace_close_fn = workspace_close_fn

    @property
    def observes_live_workspaces(self):
        return self._live_workspaces_fn is not None

    @property
    def closes_workspaces(self):
        return self._workspace_close_fn is not None

    def materialize_workspace(self, record, now):
        return workspace_module.materialize(
            record, self.transport, self.workspaces_root, now=now
        )

    def verify_workspace(self, record):
        return workspace_module.verify_leased_workspace(
            record, self.transport, self.workspaces_root
        )

    def relinquish_workspace(self, record, now):
        return workspace_module.release(
            record, self.workspaces_root, now=now
        )

    def establish_workspace_trust(self, record):
        return workspace_trust_module.establish(
            record, self.workspaces_root, self.config_path
        )

    def workspace_trust_consumable(self, record):
        """Is the workspace trusted in the config the CHILD will read?

        The child Herdr is started through the existing spawn bridge
        and inherits this process's environment, so the configuration
        it reads is ``default_config_path()`` resolved from the LIVE
        HOME — not whatever path this worker was told to write. When
        those differ, the establishment cannot be consumed by the
        child, and reporting success would be the recorded
        accepted-and-dropped class in a production path.
        """
        lease = record.get("workspace_lease")
        if not isinstance(lease, dict) or not lease.get(
            "path_realpath"
        ):
            return False, workspace_trust_module.PROBLEM_LEASE_MISSING, (
                "no workspace lease is recorded to verify trust for"
            )
        target = lease["path_realpath"]
        consumed = workspace_trust_module.resolve_config_path(
            workspace_trust_module.default_config_path()
        )
        established = workspace_trust_module.resolve_config_path(
            self.config_path
        )
        if consumed != established:
            return (
                False,
                workspace_trust_module.PROBLEM_CONFIG_NOT_CONSUMED,
                "trust was established in %s but the Herdr this"
                " dispatch would start reads %s; the establishment"
                " could not be consumed" % (established, consumed),
            )
        if not workspace_trust_module.is_trusted(consumed, target):
            return (
                False,
                workspace_trust_module.PROBLEM_TRUST_NOT_PRESENT,
                "%s no longer records trust for %s at the point of"
                " use; the Herdr would stop at the trust dialog"
                % (consumed, target),
            )
        return True, None, None

    def revoke_workspace_trust(self, record):
        return workspace_trust_module.revoke(
            record, self.workspaces_root, self.config_path
        )

    def probe_readiness(self, workspace_path):
        return self._readiness_probe_fn(workspace_path)

    def live_workspaces(self):
        return self._live_workspaces_fn()

    def close_workspace(self, workspace_id):
        return self._workspace_close_fn(workspace_id)
