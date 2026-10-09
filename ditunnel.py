#!/usr/bin/env python3
"""On-demand Cloudflare Quick Tunnel control for Dodging Infinity (local
shell only). See ``tunnel_control`` and ``docs/tunnel.md``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tunnel_control.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
