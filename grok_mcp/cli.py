"""Command-line entry for the Grok MCP endpoint: ``grokmcp.py serve``.

``main`` is the ONLY place configuration is read and the only module
in the package that imports the operator provider. It wires, in
order: ``load_config`` (file plus the environment mapping passed in)
-> ``CodexOperatorSession`` -> optional Mission service (only when the
config names ``mission_store_dir``; constructing it reads and writes
nothing) -> ``GrokMcpController`` -> ``GrokMcpServer`` ->
``serve_forever``. Every one of those seams is injectable for tests;
the defaults are the production objects.

The server binds the configured host (default 127.0.0.1) and port.
Exposing it publicly is an external tunnel concern outside this
package. Nothing here creates a tunnel, touches a Grok account, or
prints the bearer token.
"""

import argparse
import os
import sys
import time

import functools

from mission import service as mission_service_module
from mission import store as mission_store_module
from mission_control import attention as attention_module
from mission_control import controls as controls_module
from mission_control import delivery as delivery_module
from mission_control import engineering as engineering_module
from mission_control import readiness as readiness_module
from mission_control import status as status_module
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
            " connector: sixteen bounded tools, no shell, no file, no Git"
            " effect surface."
        ),
    )
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="config file path (mode 600, in a mode-700 directory)")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="serve the MCP endpoint in the foreground")
    return parser


def _unix_seconds():
    return int(time.time())


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
    mission_service = None
    if config.mission_store_dir is not None:
        mission_service = mission_service_module.MissionService(
            mission_store_module.MissionStore(config.mission_store_dir),
            _unix_seconds,
        )
    # Task 8 S-IV: the engineering engagement bootstrap is wired only when
    # BOTH stores are configured; it composes the Mission service, the
    # workflow store and this node's control repository. It refuses on
    # its own until the integrated controls exist.
    engagement_bootstrap = None
    delivery_desk = None
    status_reader = None
    control_desk = None
    attention_desk = None
    if mission_service is not None and config.workflow_store_dir is not None:
        # Task 8 S-VII: the bootstrap records the engineering Runtime's
        # readiness from a non-destructive probe of the Runtime's lock in
        # its state directory (the workflow store directory).
        engagement_bootstrap = engineering_module.MissionControl(
            mission_service, config.workflow_store_dir, config.repository,
            readiness_producer=readiness_module.RuntimeReadinessProducer(
                mission_service, config.workflow_store_dir),
        ).dispatch
        # Task 8 S-VI: the delivery desk (card, client-confirmed decision,
        # pure status) over the same stores; the P1-A6 store it reads lives
        # beside the workflow store (the P1-A6 default convention, as the
        # Runtime wires it).
        delivery_desk = delivery_module.DeliveryDesk(
            mission_service, config.workflow_store_dir, config.workflow_store_dir)
    if mission_service is not None:
        # Task 8 S-VII: the pure status read over every configured store,
        # and the human's controls (hold, resume, cancel and its confirmation).
        status_reader = functools.partial(
            status_module.mission_status, mission_service,
            workflow_directory=config.workflow_store_dir,
            delivery_directory=config.workflow_store_dir,
            coordination_directory=config.coordination_store_dir,
            destination=attention_module.CLIENT_DESTINATION)
        control_desk = controls_module.ControlDesk(mission_service)
        if config.coordination_store_dir is not None:
            attention_desk = attention_module.AttentionDesk(
                mission_service, config.coordination_store_dir)
    controller = controller_module.GrokMcpController(
        session, config.repository, mission_service=mission_service,
        engagement_bootstrap=engagement_bootstrap, delivery_desk=delivery_desk,
        status_reader=status_reader, control_desk=control_desk,
        attention_desk=attention_desk,
    )
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
