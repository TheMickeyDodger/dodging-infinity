"""Operator session seam: a provider-neutral prepare/execute boundary.

``OperatorSession`` (with ``PreparedTurn`` and ``PreparedTurnError``)
lives in ``operator_session.session`` and is provider-free;
``FunctionOperatorSession`` adapts two injected callables;
``CodexOperatorSession`` (``operator_session.codex``) is the reference
provider-backed implementation and the only module here that imports a
provider package; ``PiOperatorSession`` (``operator_session.pi``) is the
second, substitute implementation, built on the standard library alone.
The Pi session is deliberately NOT re-exported from this package: the
reference provider is the default everywhere, and the substitute is
reached only by an explicit, per-invocation selection at the caller
(``operator_session.pi`` imported by name), so importing the neutral
package neither advertises nor selects it. The seam owns no authority
and no lifecycle — see the ``operator_session.session`` docstring.
"""

from operator_session.codex import CodexOperatorSession
from operator_session.session import (
    FunctionOperatorSession,
    OperatorSession,
    PreparedTurn,
    PreparedTurnError,
)

__all__ = [
    "CodexOperatorSession",
    "FunctionOperatorSession",
    "OperatorSession",
    "PreparedTurn",
    "PreparedTurnError",
]
