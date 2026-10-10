"""Where the tunnel tool runs: macOS only.

Every public entry (the command line, ``tunnel_control.tunnel``'s commands,
the controller's ``main`` and ``Controller.start``) asks ``unsupported``
first, before touching any state or starting anything, so that on any other
platform it refuses clearly and launches nothing. The tool's modules still
import there: their platform-specific pieces are bound only where the tool
is supported. This module imports nothing but ``sys``."""

import sys

SUPPORTED_PLATFORMS = ("darwin",)


class Unsupported(Exception):
    """The refusal, raised if anything reaches a piece of the tool that is
    bound only where the tool is supported."""


def unsupported(platform=None):
    """None on a supported platform; otherwise the refusal reason."""
    platform = sys.platform if platform is None else platform
    if platform in SUPPORTED_PLATFORMS:
        return None
    return ("the tunnel control tool supports macOS only; on this platform"
            " (%s) it refuses to start and launches nothing" % platform)


def refuse(*args, **kwargs):
    """Stands in, on an unsupported platform, for a piece bound only where
    the tool is supported."""
    raise Unsupported(unsupported()
                      or "the tunnel control tool supports macOS only")
