"""The Runtime-backed ``DurableExecution``: exact delegation, nothing else.

Each method resolves its Runtime function as an ATTRIBUTE of the
``target_runtime.runtime`` module object at call time, never bound at
import time or in the constructor, so the module stays the single
point of substitution: replacing ``runtime.process_once`` on the module
(which the CLI tests do through ``cli.runtime_module``) is what this
adapter picks up. Inputs are bound at construction; the constructor
does no I/O and reads nothing from either argument. Every return value
and every exception passes through unchanged.
"""

from target_runtime import runtime as runtime_module

from durable_execution.contract import DurableExecution


class RuntimeDurableExecution(DurableExecution):
    """Delegate the three seam calls to ``target_runtime.runtime``."""

    def __init__(self, broker, state_directory):
        self.broker = broker
        self.state_directory = state_directory

    def recover_inherited_processes(self):
        return runtime_module.recover_inherited_processes(
            self.state_directory
        )

    def readiness_attention(self):
        return runtime_module.readiness_attention(self.state_directory)

    def process_once(self):
        return runtime_module.process_once(self.broker)
