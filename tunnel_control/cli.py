"""Command line for ``ditunnel.py``: JSON out, one object per command.

    ditunnel.py --state-dir DIR on --port PORT [--cloudflared ABS_PATH] [--stop-grace S]
    ditunnel.py --state-dir DIR off
    ditunnel.py --state-dir DIR status
    ditunnel.py --state-dir DIR forget
    ditunnel.py --state-dir DIR foreground --port PORT [--cloudflared ABS_PATH] [--stop-grace S]

``on`` prints the CURRENT ``trycloudflare.com`` URL for repointing the Grok
Bot connector. That URL changes on every start. If a tunnel with a DIFFERENT
port or cloudflared is already running, ``on`` refuses and names the
difference.

``forget`` moves aside, intact, a record this tool cannot resolve. It is a
human's decision, made after inspection, and it signals nothing.

``foreground`` is for the launchd job only (``scripts/ditunnel/``).

Run every command from a local shell you control. No MCP tool reaches this
command, and DI grants it to no worker. That is a workflow guardrail, not
access control: it is not designed to contain processes running with the
user's own privileges (``docs/tunnel.md``).

Exit codes: 0 done (or nothing to do); 1 the foreground tunnel exited by
itself; 3 refused or failed; 2 usage.
"""

import argparse
import json
import sys

from tunnel_control import common
from tunnel_control import tunnel

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3


def _parser():
    parser = argparse.ArgumentParser(
        prog="ditunnel",
        description="On-demand Cloudflare Quick Tunnel (free, accountless) to"
                    " the local Grok Bot MCP endpoint. Local shell only.")
    parser.add_argument("--state-dir", required=True,
                        help="absolute path of the owner-only state directory")
    commands = parser.add_subparsers(dest="command")
    commands.required = True
    for name, help_text in (
        ("on", "start the tunnel (or report the running one, if it is"
               " configured exactly as requested) and print its URL"),
        ("foreground", "the launchd job's long-running process (the"
                       " controller itself)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--port", type=int, required=True,
                             help="the MCP endpoint's loopback port")
        command.add_argument("--cloudflared",
                             help="absolute path of cloudflared (default: PATH)")
        command.add_argument("--stop-grace", type=float,
                             help="seconds cloudflared gets after SIGTERM before"
                                  " SIGKILL (default 10)")
    commands.add_parser("off", help="stop the tunnel this tool started")
    commands.add_parser("status", help="report what is actually running")
    commands.add_parser("forget", help="move an unresolvable record aside"
                                       " (after inspection); signals nothing")
    return parser


def main(argv=None, stdout=None):
    stdout = sys.stdout if stdout is None else stdout
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    try:
        if args.command == "on":
            result = tunnel.on(args.state_dir, args.port, args.cloudflared,
                               stop_grace=args.stop_grace)
        elif args.command == "off":
            result = tunnel.off(args.state_dir)
        elif args.command == "status":
            result = tunnel.status(args.state_dir)
        elif args.command == "forget":
            result = tunnel.forget(args.state_dir)
        else:
            return tunnel.foreground(args.state_dir, args.port,
                                     args.cloudflared, out=stdout,
                                     stop_grace=args.stop_grace)
    except common.TunnelError as exc:
        stdout.write(json.dumps({"ok": False, "reason": str(exc)},
                                sort_keys=True) + "\n")
        return EXIT_REFUSED
    stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return EXIT_OK
