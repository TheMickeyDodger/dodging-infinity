#!/usr/bin/env python3
"""Entry script for the DI World request panel's local interface
(``mission_panel.http_adapter``): a loopback, bearer-authenticated JSON adapter
over the existing request lifecycle. It grants no approval and names no
principal."""

import sys

from mission_panel.http_adapter import main

if __name__ == "__main__":
    sys.exit(main())
