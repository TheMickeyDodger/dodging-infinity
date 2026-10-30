#!/usr/bin/env python3
"""Entry script for the office delivery adapter's local interface
(``office_delivery.http_adapter``): a loopback, bearer-authenticated JSON adapter over the office's
delivery adapter (status, and one confirmed step through pr_delivery's expected-step guard). It is
STARTED BY YOU, never by the office, and it grants no delivery authority: the one human delivery
ceremony remains the only authority."""

import sys

from office_delivery.http_adapter import main

if __name__ == "__main__":
    sys.exit(main())
