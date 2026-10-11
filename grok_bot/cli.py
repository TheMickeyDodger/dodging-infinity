"""Command line for the Grok Bot transport adapter: one tool call, JSON in
(stdin) and JSON out (stdout).

    grokbot.py --state-dir DIR --control-repo C --workflow-store S call request < {"text": ...}
    grokbot.py --state-dir DIR [--control-repo C --workflow-store S] call present|status|result|recover < {"request_ref": ...}
    grokbot.py --state-dir DIR call approve                    < the displayed binding and approval_code
    grokbot.py --state-dir DIR call cancel                     < {"request_ref", "control_capability"}
    grokbot.py --state-dir DIR call run                        < refused: Grok Bot runs nothing
    grokbot.py --state-dir DIR [WORKSPACES] call present_delivery|approve_delivery|delivery_status < ...
    grokbot.py --state-dir DIR --control-repo C --workflow-store S [WORKSPACES] serve
               --auth-token-file TOKEN_FILE [--port N]
    grokbot.py --state-dir DIR authorize ...                   RETIRED: arms nothing (below)
    grokbot.py --state-dir DIR [WORKSPACES] authorize-delivery --proposal-digest D
               --expires-at E --display-digest X --repository REPO_REALPATH
    grokbot.py --state-dir DIR --workflow-store S authorize-workflow --request-ref R
               --workflow-id W --revision N --authorization-digest A
               --expires-at E --display-digest X

``authorize-workflow`` and ``authorize-delivery`` are the LOCAL arming
commands (``grok_bot.authorize``), never MCP tools: run in Grok Bot's
per-command, user-approved local shell, each carries the FULL displayed
binding (as ``present`` / ``present_delivery`` hand it over in ``arming``),
checks every value against the latest presentation before anything takes
effect, stores only a one-way commitment, and prints a one-time
approval_code that ``approve`` / ``approve_delivery`` then fire, once.

``authorize`` (the legacy Mission-Core arming) is RETIRED (I3 of task
20261010-164104-1761f6): a legacy proposal's approval is always refused, so
a code for it could never be used. It still parses, so an old command is
answered rather than misread, but it arms nothing, mints no code, writes
nothing, and prints ``grok_bot_legacy_arming_retired`` with the
reauthorization remedy.

Grok Bot RUNS NOTHING. ``run`` keeps its argument checks and then refuses
every command by name; no run bridge is ever built here, and nothing this
module imports reaches the dispatch path. ``status`` and ``result`` read
the durable records instead. The local operator surface (``direquest.py``)
keeps its run commands, under the operator's own authority on the DI
machine.

WORKSPACES is ``--workspace-repository R --workspaces-root W``. They are
the ONLY repositories the delivery tools accept: ``R`` itself, or one of its
worktrees directly under ``W``, named by its realpath and checked by
filesystem shape (not Git's own resolution;
``grok_bot.delivery.approved_repository``): ``present_delivery`` and
``approve_delivery`` check before their ceremony runs any Git;
``delivery_status``, which runs no Git, checks after loading the record
that names the repository. Without ``R`` every delivery tool is refused.

``request`` is the CONFORMING route (I2, ``grok_bot.conforming``),
OPERATOR_PROTOCOL.md route (b), two fresh turns rooted at the control
repository C, each under the role-turn read-only posture (``--sandbox
read-only``, no user config or rules, ``approval_policy=never``) verified
on the exact argv before the process starts, neither continuing a session:
the INITIAL Operator turn
(``operator_session.RestrictedCodexOperatorSession``) clarifies in prose or
emits the routing signal, and the signal triggers the separate planning
turn (``operator_session.codex.RestrictedPlanningOperatorSession`` ->
``codex_gateway.role_turn.run_planning_turn``). Its Mission Authorization
is validated against C (pinned by its realpath) and saved as ONE PLANNED
DI-REMOTE-2 workflow in the workflow store S, the store a Runtime
(``dirun``) running against S claims from. ``--control-repo`` and
``--workflow-store`` have NO default: without them ``request`` is refused,
naming the missing flag. Read-only confines writes, not reads: request text
can induce the planning turn to read any file the serving user can read (a
disclosed residual: not demonstrated as an exploit, and not prevented).

What comes in, and how. A TOOL CALL's arguments (``call``) are read from
stdin, never from argv, and this module reads no environment variable. Two
things DO come in on argv, deliberately:

- ``serve --auth-token-file`` names the owner-only file holding the bearer
  token (transport access control, which approves nothing). The token itself
  is read from that file, never from argv.
- The arming commands (``authorize-workflow``, ``authorize-delivery``)
  carry the FULL displayed consent binding in argv (the full digests, revision, expiry
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
- what it can do depends on its kind. A WORKFLOW code (from
  ``authorize-workflow``) confers NO delivery authority. A DELIVERY code (from
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

from local_request import surface as request_surface
from operator_session import RestrictedCodexOperatorSession
from operator_session.codex import RestrictedPlanningOperatorSession

from grok_bot import adapter as adapter_module
from grok_bot import conforming
from grok_bot import index as index_module

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_STORE = 4
# The same bound as ``local_request.cli.MAX_REQUEST_CHARS`` (pinned equal by
# test), written here so this module never imports that one: it carries the
# run bridge's construction (I3).
MAX_ARGUMENTS_CHARS = 65536
DELIVERY_TOOLS = ("present_delivery", "approve_delivery", "delivery_status")

DESCRIPTION = (
    "Grok Bot transport adapter for Dodging Infinity. A plain-text request\n"
    "goes to one fresh restrictive Codex planning turn, which authors a\n"
    "DI-REMOTE-2 Mission Authorization; it is saved as one workflow in the\n"
    "workflow store (--workflow-store) that the Runtime (dirun) claims\n"
    "after you present, arm and approve it. Grok Bot runs nothing: only a\n"
    "DI Runtime running against that workflow store advances an approved\n"
    "workflow, and status and result read its durable record. Requests made\n"
    "before this route stay readable but can no longer be approved or run;\n"
    "they show a reauthorization remedy instead. The adapter decides\n"
    "nothing and grants no delivery authority: no commit, push, pull\n"
    "request, merge, release or deploy.\n"
    "\n"
    "Delivery is a separate ceremony (present_delivery, approve_delivery):\n"
    "pr_delivery's own present-dots and attest-dots record an\n"
    "operator-attested PR Delivery Authorization for exactly the displayed\n"
    "delivery proposal. An engineering approval never authorizes delivery,\n"
    "and nothing here performs a delivery step.\n"
    "\n"
    "Approval needs a local arming first: authorize-workflow (or\n"
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
    "The request tool's Codex planning turn is one fresh session under a\n"
    "read-only sandbox, rooted at --control-repo, with your Codex user\n"
    "configuration and rules ignored and approval_policy=never, verified\n"
    "before it starts; it continues no session. Read-only confines writes,\n"
    "not reads: request text can induce it to read any file your user can\n"
    "read. That residual is disclosed, not prevented. Its document is\n"
    "validated against the closed Mission Authorization schema. That the\n"
    "initial turn only routes or asks is an instruction, not mechanically\n"
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
                        help="absolute path of the control repository: the"
                             " planning turn runs in it and every Mission"
                             " Authorization is pinned to it (request)")
    parser.add_argument("--workflow-store",
                        help="absolute path of the DI-REMOTE-2 workflow store"
                             " directory the Runtime (dirun) claims from"
                             " (request and the conforming route); no default")
    parser.add_argument("--workspace-repository",
                        help="absolute path of the local checkout of the"
                             " approved repository: the delivery tools accept"
                             " only it or its worktrees under"
                             " --workspaces-root (delivery and serve)")
    parser.add_argument("--workspaces-root",
                        help="absolute path of the existing directory, outside"
                             " every repository, whose direct worktrees of"
                             " --workspace-repository the delivery tools"
                             " accept (delivery and serve)")
    commands = parser.add_subparsers(dest="command")
    commands.required = True
    call = commands.add_parser("call", help="one tool call; arguments from stdin")
    call.add_argument("tool", choices=sorted(adapter_module.TOOLS))
    serve = commands.add_parser(
        "serve", help="the MCP endpoint on 127.0.0.1 only (no host option)")
    serve.add_argument("--port", type=int, default=0,
                       help="loopback port; 0 (the default) picks a free one")
    retired = ("RETIRED legacy Mission-Core arming: it arms nothing, mints"
               " no approval_code and writes nothing. A legacy proposal can"
               " no longer be approved; it prints the reauthorization remedy"
               " (send a request that differs)")
    arm = commands.add_parser("authorize", help=retired, description=retired)
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
    arm_workflow = commands.add_parser(
        "authorize-workflow", help="LOCAL arming of a displayed DI-REMOTE-2"
                                   " Mission Authorization approval (never an"
                                   " MCP tool). Its arguments ARE the displayed"
                                   " consent binding; it prints a single-use"
                                   " approval_code bound to that one approval")
    arm_workflow.add_argument("--request-ref", required=True)
    arm_workflow.add_argument("--workflow-id", required=True)
    arm_workflow.add_argument("--revision", required=True, type=json.loads)
    arm_workflow.add_argument("--authorization-digest", required=True,
                              help="the FULL displayed authorization digest")
    arm_workflow.add_argument("--expires-at", required=True, type=json.loads)
    arm_workflow.add_argument("--display-digest", required=True,
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
ARMING_COMMANDS = ("authorize", "authorize-workflow", "authorize-delivery")


def _local_command(args):
    """The command prefix a presentation's arming argv starts with: this
    interpreter, this entry script, this state directory and the configured
    workspaces (the delivery arming checks the same approved identity)."""
    prefix = [sys.executable, ENTRY_SCRIPT, "--state-dir", args.state_dir]
    for flag, value in (("--workspace-repository", args.workspace_repository),
                        ("--workspaces-root", args.workspaces_root),
                        ("--workflow-store", args.workflow_store)):
        if value is not None:
            prefix += [flag, value]
    return prefix


def _delivery_scope(args):
    """The configured, approved identity the delivery tools accept."""
    return {"delivery_repository": args.workspace_repository,
            "delivery_workspaces_root": args.workspaces_root}


def _route(args, clock, planning_session=None):
    """The conforming route, or None with the flags it is missing. Both
    flags are explicit; neither has a default. The control repository is
    pinned by its realpath (the form the planning request and the Runtime's
    configuration both carry). The planning turn's session is the restricted
    planning session unless a hermetic test injects one."""
    missing = tuple(flag for flag, value in (
        ("--control-repo", args.control_repo),
        ("--workflow-store", args.workflow_store)) if value is None)
    if missing:
        return None, missing
    return conforming.WorkflowRoute(
        conforming.workflow_store().WorkflowStore(args.workflow_store),
        os.path.realpath(args.control_repo),
        planning_session=(planning_session
                          or RestrictedPlanningOperatorSession(clock=clock))), ()


def recover_legacy_result(missions, mission_id):
    """The ONE legacy result recovery (``target_runtime.legacy_result``,
    which ``MissionBridge.result`` also runs), imported here, lazily, for a
    legacy ``result`` read alone. It is READ-ONLY, and its import closure
    excludes the dispatch path (pinned by test): handing the adapter this
    function hands it no handle that can start work."""
    from target_runtime import legacy_result
    return legacy_result.recover_result(missions, mission_id)


def _adapter(args, surface, operator_session, clock, planning_session):
    route, missing = _route(args, clock, planning_session)
    return adapter_module.GrokBotAdapter(
        surface, operator_session, args.repository,
        index_module.RequestIndex(args.state_dir), clock,
        local_command=_local_command(args), route=route,
        route_missing=missing, legacy_result=recover_legacy_result,
        **_delivery_scope(args))


def _arm(args, stdout, clock):
    """One LOCAL arming (``grok_bot.authorize``, loaded for these two
    commands alone): JSON out, the approval_code printed once."""
    from grok_bot import arming
    from grok_bot import authorize
    index = index_module.RequestIndex(args.state_dir)

    def operation():
        if args.command == "authorize-workflow":
            if args.workflow_store is None:
                return adapter_module.refused(
                    conforming.PROBLEM_NOT_CONFIGURED,
                    "authorize-workflow needs --workflow-store (the workflow"
                    " store directory); nothing was armed")
            return authorize.arm_workflow(
                conforming.WorkflowRoute(
                    conforming.workflow_store().WorkflowStore(args.workflow_store),
                    args.control_repo),
                index, clock, args.request_ref, args.workflow_id,
                args.revision, args.authorization_digest, args.expires_at,
                args.display_digest)
        if args.command == "authorize":
            # Retired: refuses with the remedy before reading or writing
            # anything.
            return authorize.arm_mission(
                None, index, clock, args.request_ref, args.mission_id,
                args.revision, args.proposal_digest, args.action_scope,
                args.delivery_target, args.expires_at, args.display_digest)
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
    except conforming.workflow_store().StoreError as exc:
        result = adapter_module.refused(
            conforming.PROBLEM_WORKFLOW_STORE_UNREADABLE, str(exc))
        result["status"] = "store_error"
    except adapter_module.surface_module.MISSION_CORE_ERRORS as exc:
        result = adapter_module.refused(getattr(exc, "problem", None), str(exc))
    stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result.get("ok"):
        return EXIT_OK
    return EXIT_STORE if result.get("status") == "store_error" else EXIT_REFUSED


def _serve(args, stdout, clock, operator_session, planning_session=None):
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
    adapter = _adapter(
        args, request_surface.build_bridgeless_surface(args.state_dir, clock),
        operator_session or RestrictedCodexOperatorSession(), clock,
        planning_session)
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


def main(argv=None, stdin=None, stdout=None, clock=None,
         operator_session=None, planning_session=None):
    """``operator_session`` and ``planning_session`` exist for hermetic tests
    to inject recorders; production uses the restricted Operator session for
    the initial turn and the restricted planning session for the planning
    turn. There is no run bridge to inject: Grok Bot builds none (I3)."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    tool = getattr(args, "tool", None)
    paths = [("--state-dir", args.state_dir)]
    if args.command == "serve":
        paths.append(("--control-repo", args.control_repo))
    # Explicit when given (no default, never inferred): absolute, always.
    for flag, value in (("--repository", args.repository),
                        ("--control-repo", args.control_repo),
                        ("--workflow-store", args.workflow_store)):
        if value is not None and (flag, value) not in paths:
            paths.append((flag, value))
    if tool in DELIVERY_TOOLS or args.command in ("serve",
                                                  "authorize-delivery"):
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
        return _serve(args, stdout, clock, operator_session, planning_session)
    if args.command in ARMING_COMMANDS:
        return _arm(args, stdout, clock)
    if operator_session is None and args.tool == "request":
        operator_session = RestrictedCodexOperatorSession()
    adapter = _adapter(
        args, request_surface.build_bridgeless_surface(args.state_dir, clock),
        operator_session, clock, planning_session)
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
