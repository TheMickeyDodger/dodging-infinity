"""Command-line entry for the Grok MCP endpoint: ``grokmcp.py serve``.

``main`` is the ONLY place configuration is read and the only module
in the package that imports the operator provider. It wires, in
order: ``load_config`` (file plus the environment mapping passed in)
-> ``CodexOperatorSession`` -> ``GrokMcpController`` ->
``GrokMcpServer`` -> ``serve_forever``. Every one of those seams is
injectable for tests; the defaults are the production objects.

The server binds the configured host (default 127.0.0.1) and port.
Exposing it publicly is an external tunnel concern outside this
package. Nothing here creates a tunnel, touches a Grok account, or
prints the bearer token.
"""

import argparse
import os
import sys

from operator_session import CodexOperatorSession

from grok_mcp import config as config_module
from grok_mcp import controller as controller_module
from grok_mcp import server as server_module

EXIT_OK = 0
EXIT_CONFIG = 2


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="grokmcp",
        description=(
            "Dodging Infinity MCP endpoint for the Grok Bot custom"
            " connector: three bounded tools, no shell, no file, no"
            " delivery surface."
        ),
    )
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="config file path (mode 600, in a mode-700 directory)")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="serve the MCP endpoint in the foreground")
    return parser


def _default_serve_forever(server):
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv=None, session_factory=None, serve_forever=None, environ=None,
         error_writer=None):
    write = error_writer or sys.stderr.write
    parser = _build_parser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_CONFIG
    if namespace.command != "serve":
        write("grokmcp: a command is required (serve)\n")
        return EXIT_CONFIG
    try:
        config = config_module.load_config(
            namespace.config, os.environ if environ is None else environ
        )
    except config_module.ConfigError as exc:
        write("grokmcp: config: %s\n" % exc)
        return EXIT_CONFIG
    session = (session_factory or CodexOperatorSession)()
    controller = controller_module.GrokMcpController(session, config.repository)
    server = server_module.GrokMcpServer(
        (config.bind_host, config.port), controller,
        bearer_token=config.bearer_token,
        endpoint_path=config.endpoint_path,
        allowed_origins=config.allowed_origins,
        log_writer=write,
    )
    try:
        host, port = server.server_address[:2]
        write("grokmcp: serving http://%s:%d%s\n" % (host, port, config.endpoint_path))
        (serve_forever or _default_serve_forever)(server)
    finally:
        server.server_close()
    return EXIT_OK
