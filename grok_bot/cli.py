"""Command line for the Grok Bot transport adapter: one tool call, JSON in
(stdin) and JSON out (stdout).

    grokbot.py --state-dir DIR --repository REPO call request  < {"text": ...}
    grokbot.py --state-dir DIR call present|status|recover     < {"request_ref": ...}
    grokbot.py --state-dir DIR call approve                    < the displayed binding and approval_code
    grokbot.py --state-dir DIR call cancel                     < {"request_ref", "control_capability"}
    grokbot.py --state-dir DIR --control-repo C [WORKSPACES] call run < {"request_ref", "command", "arguments"}
    grokbot.py --state-dir DIR [WORKSPACES] call present_delivery|approve_delivery|delivery_status < ...
    grokbot.py --state-dir DIR --repository REPO --control-repo C [WORKSPACES] serve
               --auth-token-file TOKEN_FILE [--port N]
    grokbot.py --state-dir DIR authorize --request-ref R --mission-id M --revision N
               --proposal-digest D [--action-scope S ...] [--delivery-target T ...]
               --expires-at E --display-digest X
    grokbot.py --state-dir DIR [WORKSPACES] authorize-delivery --proposal-digest D
               --expires-at E --display-digest X --repository REPO_REALPATH

``authorize`` and ``authorize-delivery`` are the LOCAL arming commands
(``grok_bot.authorize``), never MCP tools: run in Grok Bot's
per-command, user-approved local shell, each carries the FULL displayed
binding (as ``present`` / ``present_delivery`` hand it over in ``arming``),
checks every value against the latest presentation before anything takes
effect, stores only a one-way commitment, and prints a one-time
approval_code that ``approve`` / ``approve_delivery`` then fire, once.

WORKSPACES is ``--workspace-repository R --workspaces-root W``: configured
once, they let ``run`` ``dispatch`` prepare the Mission's own isolated
worktree, so no conversation ever supplies a path. Without them every
dispatch is refused (durably) as unavailable. The optional
``workspace_path`` argument is operator recovery only: it may re-name the
Mission's own prepared workspace, never another path. They are also the
ONLY repositories the delivery tools accept: ``R`` itself, or one of its
worktrees directly under ``W``, named by its realpath and checked by
filesystem shape (not Git's own resolution;
``grok_bot.delivery.approved_repository``): ``present_delivery`` and
``approve_delivery`` check before their ceremony runs any Git;
``delivery_status``, which runs no Git, checks after loading the record
that names the repository. Without ``R`` every delivery tool is refused.

``request`` runs one FRESH turn of the Codex Outer Operator
(``operator_session.RestrictedCodexOperatorSession``) in REPO, under the
role-turn read-only posture (``--sandbox read-only``, no user config or
rules, ``approval_policy=never``), verified on the exact argv before the
process starts; it continues no session. Read-only confines writes, not
reads: request text can induce the Operator to read any file the serving
user can read, and its reply returns to the caller (a disclosed residual:
not demonstrated as an exploit, and not prevented). ``run`` builds the
real run bridge through ``local_request.cli.build_bridge``, for that tool
alone.

What comes in, and how. A TOOL CALL's arguments (``call``) are read from
stdin, never from argv, and this module reads no environment variable. Two
things DO come in on argv, deliberately:

- ``serve --auth-token-file`` names the owner-only file holding the bearer
  token (transport access control, which approves nothing). The token itself
  is read from that file, never from argv.
- The arming commands (``authorize``, ``authorize-delivery``) carry the
  FULL displayed consent binding in argv (the full digests, revision, expiry
  and the rest), on purpose: the command string the human approves in the
  local shell IS the artefact of consent.

Arming prints an ``approval_code`` that ``approve`` / ``approve_delivery``
must carry. That code is a redeemable one-time value with LIMITED
capability:

- it fires exactly one armed commitment, once, and dies after
  ``MAX_CODE_FAILURES`` wrong codes;
- it expires with that commitment: the displayed expiry is part of the
  binding it fires, and the approval it completes refuses an expired
  proposal;
- what it can do depends on its kind. A MISSION code (from ``authorize``)
  confers NO delivery authority. A DELIVERY code (from
  ``authorize-delivery``) redeems EXACTLY ONE locally armed delivery
  authorization. Presented to ``approve_delivery`` with the displayed
  binding (public values) over the endpoint (bearer access), it has
  pr_delivery's ceremony record one PR Delivery Authorization for exactly
  that displayed delivery, subject to the ceremony's remaining checks (the
  live candidate, expiry at application, one authorization per proposal);
- whichever its kind, it is not a principal and not a standing credential,
  and it confers no general or standing Git authority.

No flag, variable or field names a principal.

``serve`` exposes the same tools as an MCP endpoint (``grok_bot.server``)
listening on 127.0.0.1 ONLY; there is no option that names a host. It
REQUIRES ``--auth-token-file`` and refuses to start without it, before
anything is built or bound: every request must carry that bearer token
(transport access control only; the token approves nothing and is never
printed). It prints one JSON line (the URL, the access control, and the
public-reachability setup contract, which it never performs) and serves
until interrupted. The adapter's own code opens no outbound connection;
``present_delivery`` and ``approve_delivery`` run pr_delivery's ceremony,
whose ``git ls-remote`` (and a ``git fetch`` when the remote base moved)
reaches the configured remote.

Exit codes: 0 applied or read; 3 refused; 4 a store is unusable or
full; 2 usage.
"""

import argparse
import json
import os
import sys
import time

from local_request import cli as request_cli
from operator_session import RestrictedCodexOperatorSession

from grok_bot import adapter as adapter_module
from grok_bot import index as index_module

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_STORE = 4
MAX_ARGUMENTS_CHARS = request_cli.MAX_REQUEST_CHARS
DELIVERY_TOOLS = ("present_delivery", "approve_delivery", "delivery_status")

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
    "Approval needs a local arming first: authorize (or\n"
    "authorize-delivery), run in your local shell with exactly the values\n"
    "present handed over, prints a one-time approval_code the approval must\n"
    "carry. The arming command carries the full displayed binding in its\n"
    "arguments on purpose: the command you approve is the record of your\n"
    "consent. The approval_code fires that one armed approval once, expires\n"
    "with it, and is no principal or standing credential. Tool calls read\n"
    "their arguments from stdin. The MCP bearer token alone approves\n"
    "nothing.\n"
    "\n"
    "Limitation: approval is an operator-attested relay of a separate\n"
    "plain-text reply; it is not cryptographically authenticated. Grok Bot\n"
    "gives DI no signed sender attribution, so DI does not establish who\n"
    "sent the reply.\n"
    "\n"
    "The request tool's Codex turn is one fresh session under a read-only\n"
    "sandbox, with your Codex user configuration and rules ignored and\n"
    "approval_policy=never, verified before it starts; it continues no\n"
    "session. Read-only confines writes, not reads: request text can\n"
    "induce the Operator to read any file your user can read, and its\n"
    "reply returns to the caller. That residual is disclosed, not\n"
    "prevented. That it only proposes is an instruction, not mechanically\n"
    "enforced.\n"
    "\n"
    "Network: only serve listens, on 127.0.0.1 alone, and the adapter's own\n"
    "code connects out nowhere. present_delivery and approve_delivery run\n"
    "pr_delivery's ceremony, whose git ls-remote (and a git fetch when the\n"
    "remote base moved) reaches the configured remote, possibly an external\n"
    "host; the fetch writes local repository data. The delivery tools\n"
    "accept only the --workspace-repository checkout or its worktrees\n"
    "directly under --workspaces-root: present_delivery and approve_delivery\n"
    "check that before their ceremony runs any Git; delivery_status, which\n"
    "runs no Git, checks it after loading the record. serve requires\n"
    "--auth-token-file and refuses to start without it; every request must\n"
    "carry that bearer token: transport access control only, which\n"
    "approves nothing.\n"
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
    arm = commands.add_parser(
        "authorize", help="LOCAL arming of a displayed Mission approval (never"
                          " an MCP tool). Its arguments ARE the displayed"
                          " consent binding; it prints a single-use"
                          " approval_code bound to that one approval")
    arm.add_argument("--request-ref", required=True)
    arm.add_argument("--mission-id", required=True)
    arm.add_argument("--revision", required=True, type=json.loads)
    arm.add_argument("--proposal-digest", required=True,
                     help="the FULL displayed proposal digest")
    arm.add_argument("--action-scope", action="append", default=[])
    arm.add_argument("--delivery-target", action="append", default=[])
    arm.add_argument("--expires-at", required=True, type=json.loads)
    arm.add_argument("--display-digest", required=True,
                     help="the FULL digest of the displayed text")
    arm_delivery = commands.add_parser(
        "authorize-delivery", help="LOCAL arming of a displayed delivery"
                                   " approval (never an MCP tool). Its"
                                   " arguments ARE the displayed consent"
                                   " binding; it prints a single-use"
                                   " approval_code bound to that one approval")
    arm_delivery.add_argument("--proposal-digest", required=True)
    arm_delivery.add_argument("--expires-at", required=True, type=json.loads)
    arm_delivery.add_argument("--display-digest", required=True)
    arm_delivery.add_argument("--repository", required=True)
    serve.add_argument("--auth-token-file",
                       help="REQUIRED: absolute path of an owner-only file"
                            " holding the bearer token every request must"
                            " carry; serve refuses to start without it")
    return parser


ENTRY_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "grokbot.py")
ARMING_COMMANDS = ("authorize", "authorize-delivery")


def _local_command(args):
    """The command prefix a presentation's arming argv starts with: this
    interpreter, this entry script, this state directory and the configured
    workspaces (the delivery arming checks the same approved identity)."""
    prefix = [sys.executable, ENTRY_SCRIPT, "--state-dir", args.state_dir]
    for flag, value in (("--workspace-repository", args.workspace_repository),
                        ("--workspaces-root", args.workspaces_root)):
        if value is not None:
            prefix += [flag, value]
    return prefix


def _delivery_scope(args):
    """The configured, approved identity the delivery tools accept."""
    return {"delivery_repository": args.workspace_repository,
            "delivery_workspaces_root": args.workspaces_root}


def _bridge_factory(args):
    return request_cli.configured_bridge_factory(args.workspace_repository,
                                                 args.workspaces_root)


def _arm(args, stdout, clock):
    """One LOCAL arming (``grok_bot.authorize``, loaded for these two
    commands alone): JSON out, the approval_code printed once."""
    from grok_bot import arming
    from grok_bot import authorize
    index = index_module.RequestIndex(args.state_dir)

    def operation():
        if args.command == "authorize":
            return authorize.arm_mission(
                request_cli.build_surface(args.state_dir, clock), index, clock,
                args.request_ref, args.mission_id, args.revision,
                args.proposal_digest, args.action_scope, args.delivery_target,
                args.expires_at, args.display_digest)
        return authorize.arm_delivery(
            index, clock, args.proposal_digest, args.expires_at,
            args.display_digest, args.repository, args.workspace_repository,
            args.workspaces_root)
    try:
        result = operation()
    except adapter_module.surface_module.LocalRequestRefusal as refusal:
        result = adapter_module._label(refusal.as_dict())
    except (index_module.RequestIndexError, arming.CommitmentsError) as exc:
        result = adapter_module.refused(exc.problem, str(exc))
        result["status"] = "store_error"
    except adapter_module.surface_module.MISSION_CORE_ERRORS as exc:
        result = adapter_module.refused(getattr(exc, "problem", None), str(exc))
    stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result.get("ok"):
        return EXIT_OK
    return EXIT_STORE if result.get("status") == "store_error" else EXIT_REFUSED


def _serve(args, stdout, clock, bridge_factory, operator_session):
    # The only network module in the package, imported for serve alone.
    from grok_bot import server as server_module
    # Fail closed FIRST: without a token nothing is built, read or bound.
    if args.auth_token_file is None:
        stdout.write(json.dumps(adapter_module.refused(
            adapter_module.PROBLEM_BAD_REQUEST,
            "serve requires --auth-token-file: the endpoint is never served"
            " without a bearer token"), sort_keys=True) + "\n")
        return EXIT_USAGE
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
        surface, operator_session or RestrictedCodexOperatorSession(),
        args.repository, index_module.RequestIndex(args.state_dir), clock,
        local_command=_local_command(args), **_delivery_scope(args))
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
    if tool in DELIVERY_TOOLS or tool == "run" or args.command in (
        "serve", "authorize-delivery"
    ):
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
    if args.command in ARMING_COMMANDS:
        return _arm(args, stdout, clock)
    if args.tool == "run":
        surface = request_cli.build_surface(
            args.state_dir, clock, args.control_repo,
            bridge_factory or _bridge_factory(args))
    else:
        surface = request_cli.build_surface(args.state_dir, clock)
    if operator_session is None and args.tool == "request":
        operator_session = RestrictedCodexOperatorSession()
    adapter = adapter_module.GrokBotAdapter(
        surface, operator_session, args.repository,
        index_module.RequestIndex(args.state_dir), clock,
        local_command=_local_command(args), **_delivery_scope(args))
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
