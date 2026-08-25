#!/usr/bin/env python3
"""Repository-root entry script for the Telegram Remote Operator CLI.

Thin delegate to ``telegram_operator.cli:main``. This script must not
be named ``telegram_operator.py`` — that would shadow the package. It
routes allowlisted Telegram intent only into the local Codex Operator
workflow via the Codex Gateway and never touches Herdr.
"""

import sys

from telegram_operator.cli import main

if __name__ == "__main__":
    sys.exit(main())
