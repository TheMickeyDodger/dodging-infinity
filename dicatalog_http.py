#!/usr/bin/env python3
"""Entry script for the passive loopback HTTP adapter over the mission
catalog (``target_runtime.catalog_http``): GET /catalog, /diagnostics and
/healthz only. Registration stays ``dicatalog.py``."""

import sys

from target_runtime.catalog_http import main

if __name__ == "__main__":
    sys.exit(main())
