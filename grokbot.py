#!/usr/bin/env python3
"""Repository-root entry script for the Grok Bot transport adapter.

Thin delegate to ``grok_bot.cli:main``. One tool call per run, JSON in
and out. Approval is an operator-attested relay of a separate plain-text
reply, not cryptographically authenticated; the adapter grants no
delivery authority and its own code opens no network connection (its
delivery tools run pr_delivery's ceremony, whose ``git ls-remote`` and
possible ``git fetch`` reach the configured remote).
"""

import sys

from grok_bot.cli import main

if __name__ == "__main__":
    sys.exit(main())
