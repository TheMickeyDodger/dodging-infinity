#!/usr/bin/env python3
"""Repository-root entry script for the local operator request surface.

Thin delegate to ``local_request.cli:main``. Any local caller can run it:
it proposes Missions, reads their durable status, and cancels the
caller's own pending proposal. Ordinary approval is always refused here;
``attest-approval`` relays an operator-attested approval (not
independently verified). The run commands drive the request's own
AUTHORIZED Mission through the real run bridge and confer no delivery
authority.
"""

import sys

from local_request.cli import main

if __name__ == "__main__":
    sys.exit(main())
