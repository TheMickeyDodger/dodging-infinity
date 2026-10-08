"""Command line for the Grok Bot transport adapter: one tool call, JSON in
(stdin) and JSON out (stdout).

    grokbot.py --state-dir DIR --repository REPO call request  < {"text": ...}
    grokbot.py --state-dir DIR call present|status|recover     < {"request_ref": ...}
    grokbot.py --state-dir DIR call approve                    < the displayed binding
    grokbot.py --state-dir DIR call cancel                     < {"request_ref", "control_capability"}
    grokbot.py --state-dir DIR --control-repo C [WORKSPACES] call run < {"request_ref", "command", "arguments"}
    grokbot.py --state-dir DIR --repository REPO --control-repo C [WORKSPACES] serve [--port N]

WORKSPACES is ``--workspace-repository R --workspaces-root W``: configured
once, they let ``run`` ``dispatch`` prepare the Mission's own isolated
worktree, so no conversation ever supplies a path. Without them every
dispatch is refused (durably) as unavailable. The optional
``workspace_path`` argument is operator recovery only: it may re-name the
Mission's own prepared workspace, never another path.

``request`` runs one turn of the Codex Outer Operator
(``operator_session.CodexOperatorSession``) in REPO; ``run`` builds the
real run bridge through ``local_request.cli.build_bridge``, for that tool
alone. Arguments are read from stdin, never from argv, and this module
reads no environment: no flag, variable or field names a principal,
carries a credential or grants approval.

``serve`` exposes the same tools as an MCP endpoint (``grok_bot.server``)
listening on 127.0.0.1 ONLY; there is no option that names a host. With
``--auth-token-file`` it requires that bearer token on every request
(transport access control only; the token approves nothing and is never
printed). It prints one JSON line (the URL, the access control, and the
public-reachability setup contract, which it never performs) and serves
until interrupted. The adapter's own code opens no outbound connection;
the delivery tools run pr_delivery's ceremony, whose ``git ls-remote`` (and
a ``git fetch`` when the remote base moved) reaches the configured remote.

Exit codes: 0 applied or read; 3 refused; 4 a store is unusable or
full; 2 usage.
"""

import argparse
import json
import os
import sys
import time

from local_request import cli as request_cli
from operator_session import CodexOperatorSession

from grok_bot import adapter as adapter_module
from grok_bot import index as index_module

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_STORE = 4
MAX_ARGUMENTS_CHARS = request_cli.MAX_REQUEST_CHARS

DESCRIPTION = (
    "Grok Bot transport adapter for Dodging Infinity. A plain-text request\n"
    "goes to the Codex Outer Operator, which authors the Mission proposal;\n"
    "presentation, approval, status and the run commands are the local\n"
    "request surface's own. The adapter decides nothing and grants no\n"
    "delivery authority: no commit, push, pull request, merge, release or\n"
    "deploy.\n"
    "\n"
    "Delivery is a separate ceremony (present_delivery, approve_delivery):\n"
    "pr_delivery's own present-dots and attest-dots record an\n"
    "operator-attested PR Delivery Authorization for exactly the displayed\n"
    "delivery proposal. An engineering approval never authorizes delivery,\n"
    "and nothing here performs a delivery step.\n"
    "\n"
    "Limitation: approval is an operator-attested relay of a separate\n"
    "plain-text reply; it is not cryptographically authenticated. Grok Bot\n"
    "gives DI no signed sender attribution, so DI does not establish who\n"
    "sent the reply.\n"
    "\n"
    "Limitation: the request tool's Codex turn runs under your ambient\n"
    "Codex configuration with no read-only sandbox. Its proposal-only\n"
    "boundary is instruction-based, not mechanically enforced.\n"
    "\n"
    "Network: only serve listens, on 127.0.0.1 alone, and the adapter's own\n"
    "code connects out nowhere. The delivery tools run pr_delivery's\n"
    "ceremony, whose git ls-remote (and a git fetch when the remote base\n"
    "moved) reaches the configured remote, possibly an external host; the\n"
    "fetch writes local repository data. With --auth-token-file, every\n"
    "request must carry that bearer\n"
    "token: transport access control only, which approves nothing.\n"
    "Reaching serve from Grok Bot needs human setup this command never\n"
    "performs: provisioning that token, a public HTTPS forwarder, and the\n"
    "connector (docs/grok-bot.md). Its loopback tests prove protocol shape\n"
    "only; live Grok Bot compatibility is unverified.")


def _parser():
    parser = argparse.ArgumentParser(
        prog="grokbot", description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", required=True,
                        help="absolute path of the protected state directory")
    parser.add_argument("--repository",
                        help="absolute path of the repository the Outer"
                             " Operator runs in (request only)")
    parser.add_argument("--control-repo",
                        help="absolute path of the control repository the"
                             " run bridge uses (run only)")
    parser.add_argument("--workspace-repository",
                        help="absolute path of the local checkout of the"
                             " approved repository DI prepares Mission"
                             " worktrees from (run and serve)")
    parser.add_argument("--workspaces-root",
                        help="absolute path of the existing directory, outside"
                             " every repository, that holds the prepared"
                             " Mission worktrees (run and serve)")
    commands = parser.add_subparsers(dest="command")
    commands.required = True
    call = commands.add_parser("call", help="one tool call; arguments from stdin")
    call.add_argument("tool", choices=sorted(adapter_module.TOOLS))
    serve = commands.add_parser(
        "serve", help="the MCP endpoint on 127.0.0.1 only (no host option)")
    serve.add_argument("--port", type=int, default=0,
                       help="loopback port; 0 (the default) picks a free one")
    serve.add_argument("--auth-token-file",
                       help="absolute path of an owner-only file holding the"
                            " bearer token every request must carry (absent:"
                            " no transport token, loopback only)")
    return parser


def _bridge_factory(args):
    return request_cli.configured_bridge_factory(args.workspace_repository,
                                                 args.workspaces_root)


def _serve(args, stdout, clock, bridge_factory, operator_session):
    # The only network module in the package, imported for serve alone.
    from grok_bot import server as server_module
    token = None
    if args.auth_token_file is not None:
        try:
            token = server_module.read_bearer_token(args.auth_token_file)
        except server_module.BearerTokenError as exc:
            # The message never contains the token.
            stdout.write(json.dumps(adapter_module.refused(
                adapter_module.PROBLEM_BAD_REQUEST, str(exc)), sort_keys=True)
                + "\n")
            return EXIT_USAGE
    surface = request_cli.build_surface(
        args.state_dir, clock, args.control_repo,
        bridge_factory or _bridge_factory(args))
    adapter = adapter_module.GrokBotAdapter(
        surface, operator_session or CodexOperatorSession(), args.repository,
        index_module.RequestIndex(args.state_dir), clock)
    try:
        server = server_module.LoopbackMcpServer(adapter, args.port,
                                                 log=sys.stderr, bearer_token=token)
    except OSError as exc:
        stdout.write(json.dumps(adapter_module.refused(
            "grok_bot_listen_failed", "could not listen on %s port %d (%s)"
            % (server_module.LOOPBACK_HOST, args.port, exc)), sort_keys=True)
            + "\n")
        return EXIT_REFUSED
    started = {
        "ok": True, "status": "serving", "url": server.url,
        "bind_address": server_module.LOOPBACK_HOST, "port": server.server_port,
        "access_control": server.access_control,
        "mcp_transport": "streamable_http",
        "protocol_versions": list(server_module.mcp.PROTOCOL_VERSIONS),
        "public_reachability": server_module.PUBLIC_REACHABILITY,
        "delivery_authority": adapter_module.DELIVERY_AUTHORITY,
        "evidence_status": adapter_module.EVIDENCE_STATUS,
        "transport": adapter_module.TRANSPORT,
    }
    stdout.write(json.dumps(started, sort_keys=True) + "\n")
    stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def main(argv=None, stdin=None, stdout=None, clock=None, bridge_factory=None,
         operator_session=None):
    """``bridge_factory`` and ``operator_session`` exist for hermetic tests
    to inject recorders; production uses the real bridge and the Codex
    session."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    tool = getattr(args, "tool", None)
    paths = [("--state-dir", args.state_dir)]
    if tool == "request" or args.command == "serve":
        paths.append(("--repository", args.repository))
    if tool == "run" or args.command == "serve":
        paths.append(("--control-repo", args.control_repo))
        paths.extend((flag, value) for flag, value in (
            ("--workspace-repository", args.workspace_repository),
            ("--workspaces-root", args.workspaces_root)) if value is not None)
    for flag, value in paths:
        if value is None or not os.path.isabs(value):
            stdout.write(json.dumps(adapter_module.refused(
                adapter_module.PROBLEM_BAD_REQUEST,
                "%s must be an absolute path" % flag), indent=2,
                sort_keys=True) + "\n")
            return EXIT_USAGE
    clock = clock or (lambda: int(time.time()))
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            stdout.write(json.dumps(adapter_module.refused(
                adapter_module.PROBLEM_BAD_REQUEST,
                "--port must be 0 to 65535"), sort_keys=True) + "\n")
            return EXIT_USAGE
        return _serve(args, stdout, clock, bridge_factory, operator_session)
    if args.tool == "run":
        surface = request_cli.build_surface(
            args.state_dir, clock, args.control_repo,
            bridge_factory or _bridge_factory(args))
    else:
        surface = request_cli.build_surface(args.state_dir, clock)
    if operator_session is None and args.tool == "request":
        operator_session = CodexOperatorSession()
    adapter = adapter_module.GrokBotAdapter(
        surface, operator_session, args.repository,
        index_module.RequestIndex(args.state_dir), clock)
    text = stdin.read(MAX_ARGUMENTS_CHARS + 1)
    if len(text) > MAX_ARGUMENTS_CHARS:
        result = adapter_module.refused(
            adapter_module.PROBLEM_BAD_REQUEST,
            "the arguments are longer than %d characters" % MAX_ARGUMENTS_CHARS)
    else:
        try:
            arguments = json.loads(text)
        except (ValueError, RecursionError) as exc:
            # RecursionError: nested past what the decoder itself can parse.
            result = adapter_module.refused(
                adapter_module.PROBLEM_BAD_REQUEST,
                "the arguments are not JSON this command can parse (%s)"
                % type(exc).__name__)
        else:
            result = adapter.call(args.tool, arguments)
    stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result.get("ok"):
        return EXIT_OK
    return EXIT_STORE if result.get("status") == "store_error" else EXIT_REFUSED
