"""Worker seam: a substrate-neutral boundary for host-bound work.

This module is SUBSTRATE-FREE. It imports nothing from any substrate:
no clone tool, no store of units of work, no configuration loader, no
transport, no provider, and no orchestration machinery; it is
standard-library only (``abc``). A substrate package subclasses
``Worker`` and is the only place that knows how the calls below are
actually carried out on the host that holds the workspace. This
module does not name that package: the dependency runs one way, from
the implementation to this contract, never back.

The seam carries exactly the host-bound calls the production path
makes on the machine that holds a leased workspace, with the inputs
an implementation binds when it is constructed (its clone transport,
its managed workspace root, its configuration path, and its optional
observation and close callables) removed. Every call returns the
substrate's own result UNCHANGED, including the substrate's own
refusal tuples and its own exceptions: this seam declares no refusal
codes and no exception type, on purpose. A caller that reads a
``problem`` value reads it from the module that defines it, whichever
implementation produced it; a neutral copy here would be a second
vocabulary for the same facts. This is a deliberate difference from a
sibling seam that does carry its own refusal codes.

``record`` below is the caller's durable record of one unit of work;
the seam reads and, where the substrate does, updates it in place,
exactly as the substrate does. ``now`` is always the caller's clock
value; the seam reads no clock.

- ``materialize_workspace(record, now)``: clone the recorded target
  at the recorded baseline revision into a fresh leased directory
  under the managed root, verify it, and lease it on the record;
  returns ``(ok, problem, detail)``.
- ``verify_workspace(record)``: re-verify the leased workspace
  read-only before any use; returns ``(ok, problem, detail)``.
- ``relinquish_workspace(record, now)``: give up the lease and remove
  the leased directory; returns ``(ok, problem, detail)``.
- ``establish_workspace_trust(record)``: record trust for the leased
  workspace in the bound configuration; returns
  ``(ok, problem, detail)``.
- ``workspace_trust_consumable(record)``: is that trust present in
  the configuration a process started on this host will actually
  read; returns ``(ok, problem, detail)``.
- ``revoke_workspace_trust(record)``: remove that one trust entry;
  returns ``(ok, problem, detail)``.
- ``probe_readiness(workspace_path)``: read the live agent facts for
  the workspace at that path; returns the substrate's own mapping, or
  ``None`` when the workspace is not observable.
- ``live_workspaces()``: the read-only projection of live workspaces
  and the agent names in each; returns the substrate's own list, or
  ``None`` when the projection is unreadable.
- ``close_workspace(workspace_id)``: close exactly that live
  workspace; returns the substrate's own result.

Two read-only PRESENCE facts accompany the calls, because the
production path branches on them before it calls anything:
``observes_live_workspaces`` (this implementation can produce a
live-workspace projection at all) and ``closes_workspaces`` (this
implementation carries the ability to close a workspace it is told
to close). They are wiring-presence facts and they GRANT NOTHING:
``closes_workspaces`` being true authorizes no close; the caller's
own ownership proof still decides whether any workspace is closed,
and an implementation that cannot observe live workspaces cannot
prove ownership of one. The distinction between "not wired" and
"wired but unreadable" is load-bearing for the caller, and a
``None`` return already means the latter, so the former is a
property rather than a sentinel return.

What the seam does NOT own, in plain terms: it carries no identity of
its own (no name, no identifier, no label; the record's own recorded
identifiers are the only proof inputs, and nothing here stands in for
one); it proves no ownership and decides no cleanup (the caller
proves from the record and the projection, and revalidates before
the destructive call); it grants no authority of any kind (being
able to reach a host, a tree, or a network authorizes nothing); it
holds no lock, reads no clock, keeps no store, loads no
configuration of its own, retries nothing, wraps no error, and has
no relationship with any other seam in the tree.

The vocabulary here is generic on purpose: a product, provider,
sibling-seam, or delivery name in this package would turn the
neutral contract into a statement about one particular substrate.
The static suite scans every token of this package, docstrings and
comments included, so such a name cannot return unnoticed.
"""

import abc


class Worker(abc.ABC):
    """The substrate-neutral host-bound work boundary.

    Every method makes exactly ONE substrate call with the inputs
    bound at construction plus the arguments given, returns the
    substrate's result unchanged, retries nothing, and swallows
    nothing. The two properties are computed from the bound inputs
    and grant nothing.
    """

    @property
    @abc.abstractmethod
    def observes_live_workspaces(self):
        """Wiring presence: this implementation can produce a
        live-workspace projection at all. Grants nothing."""

    @property
    @abc.abstractmethod
    def closes_workspaces(self):
        """Wiring presence: this implementation carries the ability to
        close a workspace it is told to close. Grants nothing: the
        caller's ownership proof decides every close."""

    @abc.abstractmethod
    def materialize_workspace(self, record, now):
        """Clone the recorded target at the recorded baseline revision
        into a fresh leased directory, verify it, and lease it on the
        record; returns the substrate's own ``(ok, problem, detail)``
        unchanged."""

    @abc.abstractmethod
    def verify_workspace(self, record):
        """Re-verify the leased workspace read-only before any use;
        returns the substrate's own ``(ok, problem, detail)``
        unchanged."""

    @abc.abstractmethod
    def relinquish_workspace(self, record, now):
        """Give up the lease and remove the leased directory; returns
        the substrate's own ``(ok, problem, detail)`` unchanged."""

    @abc.abstractmethod
    def establish_workspace_trust(self, record):
        """Record trust for the leased workspace in the bound
        configuration; returns the substrate's own
        ``(ok, problem, detail)`` unchanged."""

    @abc.abstractmethod
    def workspace_trust_consumable(self, record):
        """Is that trust present in the configuration a process
        started on this host will actually read; returns the
        substrate's own ``(ok, problem, detail)`` unchanged."""

    @abc.abstractmethod
    def revoke_workspace_trust(self, record):
        """Remove that one trust entry from the bound configuration;
        returns the substrate's own ``(ok, problem, detail)``
        unchanged."""

    @abc.abstractmethod
    def probe_readiness(self, workspace_path):
        """Read the live agent facts for the workspace at that path;
        returns the substrate's own mapping unchanged, or ``None``
        when the workspace is not observable."""

    @abc.abstractmethod
    def live_workspaces(self):
        """The read-only projection of live workspaces and the agent
        names in each; returns the substrate's own list unchanged, or
        ``None`` when the projection is unreadable."""

    @abc.abstractmethod
    def close_workspace(self, workspace_id):
        """Close exactly that live workspace; returns the substrate's
        own result unchanged."""
