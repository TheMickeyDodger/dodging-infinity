"""The Runtime-backed ``CapabilityAuthority``: exact delegation, nothing else.

Each method resolves its store function as an ATTRIBUTE of the
``target_runtime.capability`` module object at call time, never bound
at import time or in the constructor, so that module stays the single
point of substitution: replacing ``capability.mint`` on the module is
what this adapter picks up. The constructor binds the store directory
only and does no I/O: the store file is created by the first ``mint``
(inside ``target_runtime.capability``), never by construction, so a
Broker over a directory that does not exist yet leaves it absent.
Every return value and every exception passes through unchanged.

``nonce_factory`` is deliberately NOT carried through the seam: no
production caller passes it, and it stays a module-level test hook on
``target_runtime.capability``. The seam has exactly the three calls
the production call graph makes, with the bound directory removed.
"""

from target_runtime import capability as capability_module

from capability.contract import CapabilityAuthority


class RuntimeCapabilityAuthority(CapabilityAuthority):
    """Delegate the three seam calls to ``target_runtime.capability``
    over ONE bound store directory: the Broker's workflow-store
    directory, so mint and consume always read the same store."""

    def __init__(self, store_directory):
        self.store_directory = store_directory

    def mint(self, workflow_id, action, revision, now):
        return capability_module.mint(
            self.store_directory, workflow_id, action, revision, now
        )

    def validate_and_consume(self, token, workflow_id, action, revision,
                             now):
        return capability_module.validate_and_consume(
            self.store_directory, token, workflow_id, action, revision,
            now,
        )

    def compact(self, now, non_actionable, oracle_errors):
        return capability_module.compact(
            self.store_directory, now, non_actionable, oracle_errors
        )
