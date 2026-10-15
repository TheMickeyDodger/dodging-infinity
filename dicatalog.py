#!/usr/bin/env python3
"""Entry script for the producer-neutral mission catalog
(``target_runtime.catalog``): explicit source registration and the
read-only catalog endpoint."""

import sys

from target_runtime.catalog import main

if __name__ == "__main__":
    sys.exit(main())
