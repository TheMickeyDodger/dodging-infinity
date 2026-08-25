#!/usr/bin/env python3
"""Repository-root entry script for the Codex Gateway terminal CLI.

Thin delegate to ``codex_gateway.cli:main``. This script must not be
named ``codex_gateway.py`` — that would shadow the package. It routes
terminal intent only into the local Codex CLI operator workflow and
never touches Herdr.
"""

import sys

from codex_gateway.cli import main

if __name__ == "__main__":
    sys.exit(main())
