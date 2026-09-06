"""Grok Bot MCP transport: the thinnest interaction path from a Grok
custom MCP connector into Dodging Infinity.

Route: Grok -> HTTP POST (Streamable HTTP) -> ``grok_mcp.server`` ->
``grok_mcp.protocol`` -> ``grok_mcp.adapter`` (a real
``HumanInteractionAdapter``) -> ``grok_mcp.controller`` (neutral
``OperatorSession`` prepare/execute) -> structured MCP tool result ->
Grok renders it in its own conversation.

Grok owns the visible conversation, rendering, persistence,
notification, and continuity. DI returns a tool result and never sends
an addressed message into Grok; that is the design. DI mints every
identity it records and never reads, requires, or fabricates a Grok
conversation id, message id, user id, or thread id.

This package depends on ``human_interaction`` and (in ``cli`` only)
``operator_session``; neither of those may ever name this package. It
imports no Telegram, gateway, authority, runtime, delivery, or
orchestration module and no process-spawning or temp-file machinery.
It holds no authority: no shell, no run-command, no file write, no
delivery, no Mission or Capability surface. It writes no durable
state. ``grok_mcp.cli`` is deliberately not imported here so that
importing the package loads no provider.
"""

from grok_mcp.adapter import GrokMcpInteractionAdapter
from grok_mcp.controller import (
    GrokMcpController,
    InvalidParamsError,
    ToolResult,
    UnknownToolError,
)
from grok_mcp.protocol import SUPPORTED_PROTOCOL_VERSIONS, TOOL_NAMES, TOOLS
from grok_mcp.server import GrokMcpServer

__all__ = [
    "GrokMcpController",
    "GrokMcpInteractionAdapter",
    "GrokMcpServer",
    "InvalidParamsError",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "TOOLS",
    "TOOL_NAMES",
    "ToolResult",
    "UnknownToolError",
]
