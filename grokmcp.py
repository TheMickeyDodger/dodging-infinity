#!/usr/bin/env python3
"""Repository-root entry script for the Grok Bot MCP endpoint.

Thin delegate to ``grok_mcp.cli:main``. This script must not be named
``grok_mcp.py`` — that would shadow the package. It serves eight
bounded MCP tools to a Grok custom connector, routes the one text turn
into the local Codex Operator workflow through the neutral seams, relays
the five Mission tools into the neutral Mission Core when one is wired,
and never touches Herdr.
"""

import sys

from grok_mcp.cli import main

if __name__ == "__main__":
    sys.exit(main())
