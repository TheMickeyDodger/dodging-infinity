"""The panel's wire format: ONE serializer and ONE size bound, shared by what builds
a view and what sends it, so the two can never disagree.

Why this module exists. The office relays the panel's answers with a response bound
(``MAX_PANEL_RESPONSE_BYTES`` in ``server/src/di/missionPanelClient.ts``). A bound
stated in CHARACTERS cannot honour it: the same 8000 characters are 8000 bytes of
ASCII, 32000 bytes of astral text as raw UTF-8, 96000 bytes if every character is
escaped as a pair of ``\\uXXXX`` (the ``ensure_ascii`` default), and 48000 bytes if
every character is a control character (JSON must escape those, and a provider's text
may carry them). So the panel does two things, both here:

- it serializes as raw UTF-8 (``ensure_ascii=False``), the most compact exact form; a
  lone surrogate (which is not valid UTF-8 and cannot be written raw) is written as its
  ``\\uXXXX`` escape, so the data is preserved and the bytes stay valid;
- it MEASURES views in these serialized bytes, never in characters, and a view that
  could exceed its budget is cut by whole turns with the cut stated in the answer.

``MAX_RESPONSE_BYTES`` equals the office's relay bound; a test reads the TypeScript
constant and fails if the two ever differ.
"""

import json
import re

MAX_RESPONSE_BYTES = 4 * 1024 * 1024

# A Python ``str`` holds an astral character as ONE code point, so any surrogate code point
# in one is a lone surrogate: not representable as UTF-8, written as a JSON escape instead.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def encode(value):
    """The exact bytes that cross the relay for ``value``: sorted-key JSON, raw UTF-8,
    one trailing newline."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = _LONE_SURROGATE.sub(lambda match: "\\u%04x" % ord(match.group()), text)
    return (text + "\n").encode("utf-8")
