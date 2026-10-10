"""Repository-owned, on-demand tunnel control.

``ditunnel.py`` (``tunnel_control.cli``) starts, stops and reports a free
Cloudflare Quick Tunnel to the local Grok Bot MCP endpoint. Its commands are
``on``, ``off``, ``status`` and ``forget``, plus ``foreground`` for the
optional launchd job.

A persistent controller (``tunnel_control.controller``) owns each tunnel. It
holds ``cloudflared`` as its own unreaped child, so shutdown reaches exactly
the tunnel's process group, descendants included, without re-deriving
ownership from a recyclable pid.

Lifecycle control is LOCAL-SHELL ONLY: no MCP tool exposes it, and no worker
role is granted it (both absences pinned by test). That pin is necessary but
not sufficient: local-shell-only control is a workflow guardrail, not
designed to contain processes running with the user's own privileges.

``tunnel_control.tunnel`` states the ownership rule and exactly what it does
and does not establish. ``docs/tunnel.md`` is the operator documentation.
"""
