"""Operator session seam: a provider-neutral prepare/execute boundary.

``OperatorSession`` (with ``PreparedTurn`` and ``PreparedTurnError``)
lives in ``operator_session.session`` and is provider-free;
``FunctionOperatorSession`` adapts two injected callables;
``CodexOperatorSession`` (``operator_session.codex``) is the only
provider-backed implementation and the only module here that imports
one. The seam owns no authority and no lifecycle — see the
``operator_session.session`` docstring.
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
