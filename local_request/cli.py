"""Command line for the local operator request surface.

    direquest.py --state-dir DIR propose        < request.json
    direquest.py --state-dir DIR status  REQUEST_REF
    direquest.py --state-dir DIR approve REQUEST_REF --revision N
                                         --proposal-digest HEX [--expires-at T]
    direquest.py --state-dir DIR cancel  REQUEST_REF < control_capability
    direquest.py --state-dir DIR recover REQUEST_REF
    direquest.py --state-dir DIR present [REQUEST_REF]
    direquest.py --state-dir DIR attest-approval REQUEST_REF ... < reply

The run route (Task 8 increment 2b), for the request's OWN AUTHORIZED
Mission, each through the real run bridge
(``target_runtime.mission_bridge``) built for these commands alone:

    direquest.py --state-dir DIR dispatch  REQUEST_REF --control-repo C --workspace W
    direquest.py --state-dir DIR observe   REQUEST_REF --control-repo C
    direquest.py --state-dir DIR reconcile REQUEST_REF --control-repo C
    direquest.py --state-dir DIR prove     REQUEST_REF OPERATION --control-repo C < args.json
    direquest.py --state-dir DIR verify    REQUEST_REF --control-repo C < reported.json
    direquest.py --state-dir DIR result    REQUEST_REF --control-repo C
    direquest.py --state-dir DIR pause|resume|cancel-run REQUEST_REF --control-repo C

``prove`` calls ONE existing proof seam (submit_evidence, accept_evidence,
record_claim, record_artifact, bind_dependency, resolve_dependency,
observe_resource_readiness) with exactly its own arguments; a submission
is never accepted in the same step. ``cancel`` withdraws a pending
proposal; ``cancel-run`` cancels a run and reports its achieved state.

Every result is one JSON object on stdout. The control capability is
read from stdin, never from argv, so it does not appear in a process
listing. There is no flag, environment variable or field that names a
principal or grants approval: argparse refuses unknown flags, the
request is closed, and this module reads no environment.

Exit codes: 0 applied or read (a HOLD is a read, reported in the
result); 3 refused (every ``approve`` exits 3); 4 a store is unusable or
full; 2 usage.
"""

import argparse
import json
import os
import sys
import time

from mission import record as mission_record
from mission import service as mission_service
from mission import store as mission_store

from local_request import store as store_module
from local_request import surface as surface_module

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_STORE = 4
# Hard input bounds, never derived from input.
MAX_REQUEST_CHARS = 65536
MAX_CAPABILITY_CHARS = 128


RUN_COMMANDS = ("dispatch", "observe", "reconcile", "prove", "verify",
                "result", "pause", "resume", "cancel-run")


def _parser():
    parser = argparse.ArgumentParser(
        prog="direquest",
        description="Local operator request surface: propose, read status,"
                    " cancel your own pending proposal. Ordinary approval is"
                    " always refused; attest-approval relays an operator-attested"
                    " approval (not independently verified). The run commands"
                    " drive the request's own AUTHORIZED Mission through the"
                    " real run bridge; they confer no delivery authority.")
    parser.add_argument("--state-dir", required=True,
                        help="absolute path of the protected state directory")
    commands = parser.add_subparsers(dest="command")
    commands.required = True
    commands.add_parser("propose", help="read a request object from stdin")
    for name in ("status", "recover"):
        commands.add_parser(name).add_argument("request_ref")
    approve = commands.add_parser("approve", help="always refused")
    approve.add_argument("request_ref")
    approve.add_argument("--revision", type=int, required=True)
    approve.add_argument("--proposal-digest", required=True)
    approve.add_argument("--expires-at", type=int, default=None)
    cancel = commands.add_parser(
        "cancel", help="read your control capability from stdin")
    cancel.add_argument("request_ref")
    present = commands.add_parser(
        "present", help="show the one pending proposal (or name one)")
    present.add_argument("request_ref", nargs="?", default=None)
    attest = commands.add_parser(
        "attest-approval",
        help="relay the human's explicit reply (read from stdin) as an"
             " operator-attested approval of exactly this binding")
    attest.add_argument("request_ref")
    attest.add_argument("--mission-id", required=True)
    attest.add_argument("--revision", type=int, required=True)
    attest.add_argument("--proposal-digest", required=True)
    attest.add_argument("--action-scope", action="append", required=True)
    attest.add_argument("--delivery-target", action="append", default=[])
    attest.add_argument("--expires-at", type=int, required=True)
    attest.add_argument("--relay-ref", required=True)
    for name in RUN_COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("request_ref")
        command.add_argument("--control-repo", required=True,
                             help="absolute path of the control repository")
        if name == "dispatch":
            command.add_argument("--workspace", required=True,
                                 help="absolute path of a clean checkout of the"
                                      " approved repository")
        if name == "prove":
            command.add_argument("operation")
    return parser


def _read_bounded(stdin, limit, what):
    text = stdin.read(limit + 1)
    if len(text) > limit:
        raise surface_module.LocalRequestRefusal(
            surface_module.PROBLEM_BAD_REQUEST,
            "%s is longer than %d characters" % (what, limit))
    return text


def build_bridge(missions, control_repo, clock):
    """The REAL run bridge with its production defaults (the real spawn
    bridge, the read-only observers, the zero-argument git transport, the
    process-ownership seam). Imported here, lazily, for the run commands
    alone: no other command loads anything that can start work."""
    from target_runtime import mission_bridge
    return mission_bridge.MissionBridge(missions, control_repo, clock)


def build_surface(state_dir, clock=None, control_repo=None, bridge_factory=None):
    clock = clock or (lambda: int(time.time()))
    missions = mission_service.MissionService(
        mission_store.MissionStore(state_dir), clock)
    bridge = None
    if bridge_factory is not None:
        bridge = bridge_factory(missions, control_repo, clock)
    return surface_module.LocalRequestSurface(
        missions, store_module.LocalRequestStore(state_dir), clock,
        bridge=bridge)


def _read_json(stdin, what):
    text = _read_bounded(stdin, MAX_REQUEST_CHARS, what)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise surface_module.LocalRequestRefusal(
            surface_module.PROBLEM_BAD_REQUEST,
            "%s is not JSON (%s)" % (what, exc))


def run(args, stdin, surface):
    if args.command in RUN_COMMANDS:
        arguments = {}
        if args.command == "dispatch":
            arguments["workspace_path"] = args.workspace
        elif args.command == "verify":
            arguments["reported_result"] = _read_json(stdin, "the reported result")
        elif args.command == "prove":
            arguments.update(operation=args.operation, arguments=_read_json(
                stdin, "the proof operation arguments"))
        command = "cancel" if args.command == "cancel-run" else args.command
        return surface.run_command(args.request_ref, command, **arguments)
    if args.command == "propose":
        text = _read_bounded(stdin, MAX_REQUEST_CHARS, "the request")
        try:
            request = json.loads(text)
        except ValueError as exc:
            raise surface_module.LocalRequestRefusal(
                surface_module.PROBLEM_BAD_REQUEST,
                "the request is not JSON (%s)" % exc)
        return surface.submit(request)
    if args.command == "status":
        return surface.status(args.request_ref)
    if args.command == "recover":
        return surface.recover(args.request_ref)
    if args.command == "approve":
        return surface.approve(args.request_ref, args.revision,
                               args.proposal_digest, args.expires_at)
    if args.command == "present":
        return surface.present(args.request_ref)
    if args.command == "attest-approval":
        reply = _read_bounded(stdin, store_module.MAX_RELAYED_REPLY_CHARS,
                              "the relayed reply").strip()
        return surface.attest_approval(
            args.request_ref, args.mission_id, args.revision,
            args.proposal_digest, args.action_scope, args.delivery_target,
            args.expires_at, reply, args.relay_ref)
    token = _read_bounded(stdin, MAX_CAPABILITY_CHARS,
                          "the control capability").strip()
    return surface.cancel(args.request_ref, token)


def main(argv=None, stdin=None, stdout=None, clock=None, bridge_factory=None):
    """``bridge_factory`` exists for hermetic tests to inject recorders;
    production uses ``build_bridge``, the real bridge."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    paths = [("--state-dir", args.state_dir)]
    if args.command in RUN_COMMANDS:
        paths.append(("--control-repo", args.control_repo))
    for flag, value in paths:
        if not os.path.isabs(value):
            stdout.write(surface_module.dumps({
                "ok": False, "status": "refused",
                "problem": surface_module.PROBLEM_BAD_REQUEST,
                "reason": "%s must be an absolute path" % flag}) + "\n")
            return EXIT_USAGE
    if args.command in RUN_COMMANDS:
        surface = build_surface(args.state_dir, clock, args.control_repo,
                                bridge_factory or build_bridge)
    else:
        surface = build_surface(args.state_dir, clock)
    try:
        result = run(args, stdin, surface)
        code = EXIT_OK if result.get("ok") else EXIT_REFUSED
    except surface_module.LocalRequestRefusal as refusal:
        result, code = refusal.as_dict(), EXIT_REFUSED
    except (store_module.LocalRequestStoreError,
            mission_store.MissionStoreError) as exc:
        result = {"ok": False, "status": "store_error",
                  "problem": exc.problem, "reason": str(exc),
                  "delivery_authority": surface_module.DELIVERY_AUTHORITY,
                  "dispatch": surface_module.DISPATCH}
        code = EXIT_STORE
    except mission_record.MissionError as exc:
        result = {"ok": False, "status": "refused", "problem": exc.problem,
                  "reason": str(exc),
                  "delivery_authority": surface_module.DELIVERY_AUTHORITY,
                  "dispatch": surface_module.DISPATCH}
        code = EXIT_REFUSED
    stdout.write(surface_module.dumps(result) + "\n")
    return code
