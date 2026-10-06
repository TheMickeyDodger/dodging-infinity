"""``python -m pr_delivery``: the human authorization ceremony and the drive.

This is the ONLY production module that constructs the real
``DeliveryTransport()`` (zero arguments, no override) and the
``DeliveryMachine``, and the ONLY place a PR Delivery Authorization is
minted — after a ceremony in which the human sees every binding and
TYPES the first twelve hex characters of the exact candidate identity.
No model, transport, Worker, Capability, OperatorSession, or Herdr role
can drive this ceremony: it reads the confirmation from the terminal.

Evidence transport residual (Lead S1), stated plainly: the engineering
and reviewer evidence arrives as a JSON document produced by
``herdctl delivery-evidence`` and carried by the human, and the
verification log and its exit status are supplied by the human. A
hand-edited document is representable. The mitigations are that this
ceremony recomputes the LIVE candidate identity itself and binds every
evidence reference to it, and that the human — the root of authority —
confirms the exact candidate. The evidence is human-attested, not
machine-attested, and nothing here claims otherwise.
"""

import argparse
import getpass
import json
import os
import secrets
import shlex
import sys
import time

from workflow_authority.digest import sha256_hex, text_digest

from pr_delivery import authorization as auth
from pr_delivery import candidate as candidate_module
from pr_delivery.boundary import PrDeliveryBoundary
from pr_delivery.machine import DeliveryMachine, MachineError
from pr_delivery.store import (
    DeliveryStore,
    StoreError,
    add_delivery,
    store_directory,
)
from pr_delivery.transport import DeliveryTransport, DeliveryTransportError

CONFIRMATION_CHARS = 12

_HERD_EVIDENCE_KEYS = ("engineering_complete", "reviewer_approve")


class CeremonyError(Exception):
    """The ceremony cannot proceed; message actionable."""


def _read_text(value):
    if value is None:
        return ""
    if value.startswith("@"):
        with open(value[1:], "r", encoding="utf-8") as handle:
            return handle.read()
    return value


def _argv(text):
    try:
        argv = shlex.split(text, posix=True)
    except ValueError as exc:
        raise CeremonyError("command could not be parsed: %s" % exc)
    if not argv:
        raise CeremonyError("command is empty")
    return argv


def build_machine(store_dir=None, effect_gate=None, clock=None):
    """The ONE construction site of the real transport. ``effect_gate``
    is the Mission gate a Mission-bound (client-confirmed) record is
    driven through; without it such a record is refused by the machine."""
    directory = store_dir or store_directory()
    return DeliveryMachine(DeliveryStore(directory), DeliveryTransport(),
                           clock or time.time, effect_gate=effect_gate)


def live_bindings(transport, repo, remote_name, base_branch):
    """The live, READ-ONLY facts of a delivery candidate (Task 8 S-VI):
    repository and git-dir realpaths, the checked-out source branch, the
    configured and expanded remote URLs, HEAD, the remote base, the staged
    candidate (native entries + identity against HEAD, the working tree
    required to be exactly the staged candidate) and the configured
    committer. Nothing is fetched or written; refuses ``CeremonyError``
    naming the first condition that does not hold."""
    repo = os.path.realpath(repo)
    try:
        toplevel = os.path.realpath(transport.toplevel(repo))
    except DeliveryTransportError as exc:
        raise CeremonyError("not a git repository: %s" % exc)
    if toplevel != repo:
        raise CeremonyError(
            "%s is not a repository toplevel (toplevel is %s)"
            % (repo, toplevel)
        )
    git_dir = os.path.realpath(transport.git_dir(repo))
    head_ref = transport.symbolic_ref_head(repo)
    if not head_ref or not head_ref.startswith("refs/heads/"):
        raise CeremonyError("HEAD is not on a named branch")
    url_exact = transport.remote_url(repo, remote_name)
    if not url_exact:
        raise CeremonyError("remote %r has no URL" % remote_name)
    target = auth.parse_exact_remote_url(url_exact)
    url_fetch = transport.remote_fetch_url(repo, remote_name)
    url_push = transport.remote_push_url(repo, remote_name)
    if not url_fetch or not url_push:
        raise CeremonyError("remote %r does not resolve" % remote_name)
    head = transport.head_oid(repo)
    if not head:
        raise CeremonyError("HEAD has no commit")
    base_ref = "refs/heads/" + base_branch
    remote_base = transport.ls_remote(repo, remote_name, base_ref)
    if remote_base is None:
        raise CeremonyError("remote %r has no %r" % (remote_name, base_ref))
    porcelain = transport.status_porcelain(repo)
    for line in porcelain.splitlines():
        if len(line) >= 3 and (
            line.startswith("??") or line[0] not in "AMD" or line[1] != " "
        ):
            raise CeremonyError(
                "the working tree is not exactly the staged candidate"
                " (porcelain %r); stage the exact candidate first" % line
            )
    entries = candidate_module.parse_raw_z(transport.diff_index_raw(repo,
                                                                     head))
    committer_name = transport.config_get(repo, "user.name")
    committer_email = transport.config_get(repo, "user.email")
    if not committer_name or not committer_email:
        raise CeremonyError(
            "git user.name and user.email must be configured for this"
            " repository; the delivery commits under them"
        )
    return {
        "repo": repo, "git_dir": git_dir, "head_ref": head_ref,
        "source_branch": head_ref[len("refs/heads/"):],
        "base_branch": base_branch, "base_ref": base_ref,
        "remote_name": remote_name, "url_exact": url_exact, "target": target,
        "url_fetch": url_fetch, "url_push": url_push, "head": head,
        "remote_base": remote_base, "entries": entries,
        "digest": candidate_module.identity_digest(entries),
        "committer_name": committer_name, "committer_email": committer_email,
    }


def authorize_client_confirmed(store_dir, delivery_id, authority, now):
    """Insert ONE client-confirmed (Mission-bound) delivery record —
    ``authority`` is a proposal's template plus the client-confirmed human
    half, assembled by the Mission-side driver after it verified the
    accepted decision evidence. Idempotent on the deterministic delivery id:
    an existing record for that id is returned untouched (never a second
    record, never a replacement). Returns ``(record, inserted)``."""
    if authority.get("human_authorization", {}).get("source") != (
        auth.AUTHORIZATION_SOURCE_CLIENT_CONFIRMATION
    ):
        raise CeremonyError("only a client-confirmed authority is minted here")
    store = DeliveryStore(store_dir or store_directory())
    record, inserted, _pruned = _mint(store, delivery_id, authority, now,
                                      reuse_existing=True)
    return record, inserted


def _mint(store, delivery_id, authority, now, reuse_existing):
    """The ONE minting site of a PR Delivery Authorization: validate the
    authority into a record and insert it under the store lock. With
    ``reuse_existing`` an existing record for ``delivery_id`` is returned
    untouched instead of being replaced. Returns ``(record, inserted,
    pruned)``."""
    record = auth.new_authorization(delivery_id, authority, now)
    with store.lock():
        document = store.load()
        existing = document["deliveries"].get(delivery_id)
        if existing is not None:
            if reuse_existing:
                return existing, False, 0
            raise CeremonyError("delivery %s already exists" % delivery_id)
        ok, problem, pruned = add_delivery(document, record)
        if not ok:
            raise CeremonyError("store refused the record: %s" % problem)
        store.save(document)
    return record, True, pruned


def _load_herd_evidence(path):
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or set(document) != set(
        _HERD_EVIDENCE_KEYS
    ):
        raise CeremonyError(
            "evidence document must carry exactly %s"
            % ", ".join(_HERD_EVIDENCE_KEYS)
        )
    return document


def assemble_authority(transport, args, now, human_identity,
                       confirmation_reader, out=None):
    """Gather every binding from the live repository and the human's
    inputs, run the ceremony, and return the AUTHORITY dictionary."""
    out = out if out is not None else sys.stdout
    bindings = live_bindings(transport, args.repo, args.remote,
                             args.base_branch)
    repo = bindings["repo"]
    head_ref = bindings["head_ref"]
    source_branch = bindings["source_branch"]
    base_branch = bindings["base_branch"]
    base_ref = bindings["base_ref"]
    remote_name = bindings["remote_name"]
    url_exact = bindings["url_exact"]
    url_fetch = bindings["url_fetch"]
    url_push = bindings["url_push"]
    head = bindings["head"]
    remote_base = bindings["remote_base"]
    if remote_base != head:
        transport.fetch_ref(repo, remote_name, base_ref)
    if remote_base != head and not transport.is_ancestor(repo, head,
                                                          remote_base):
        raise CeremonyError(
            "HEAD %s is not on the base branch %s (remote at %s); the"
            " candidate must sit directly on the base"
            % (head, base_branch, remote_base)
        )
    entries = bindings["entries"]
    digest = bindings["digest"]
    committer_name = bindings["committer_name"]
    committer_email = bindings["committer_email"]
    herd = _load_herd_evidence(args.herd_evidence)
    with open(args.verification_log, "rb") as handle:
        log = handle.read()
    if args.verification_exit_status != 0:
        raise CeremonyError(
            "the recorded verification exit status is %d; only a green"
            " (0) verification can be recorded" % args.verification_exit_status
        )
    verification_argv = _argv(args.verification_command)
    reverify_argv = _argv(args.reverify_command or args.verification_command)
    stamp = {"candidate_identity_digest_sha256": digest, "base_oid": head}
    evidence = {
        "engineering_complete": dict(herd["engineering_complete"], **stamp),
        "reviewer_approve": dict(herd["reviewer_approve"], **stamp),
        "independent_verification": dict({
            "command_argv": verification_argv,
            "exit_status": args.verification_exit_status,
            "log_sha256": sha256_hex(log),
            "log_bytes": len(log),
            "ran_at": args.verification_ran_at
            if args.verification_ran_at is not None else now,
            "recorded_at": now,
        }, **stamp),
    }
    validity = args.validity_seconds
    mission = None
    if args.mission_workflow_id or args.mission_authorization_digest:
        mission = {
            "workflow_id": args.mission_workflow_id,
            "mission_authorization_digest_sha256":
                args.mission_authorization_digest,
        }
    lines = [
        "",
        "PR DELIVERY AUTHORIZATION REQUEST",
        "---------------------------------",
        "Repository    : %s" % repo,
        "Remote        : %s = %s" % (remote_name, url_exact),
        "  fetches from: %s" % url_fetch,
        "  pushes to   : %s" % url_push,
        "Source branch : %s (%s)" % (source_branch, head_ref),
        "Target base   : %s (remote at %s)" % (base_branch, remote_base),
        "Baseline      : %s" % head,
        "Candidate     : %d entries, identity %s"
        % (len(entries), digest),
    ]
    for entry in entries[:50]:
        lines.append("  %s %s %s" % (entry["status"], entry["mode"],
                                     entry["path"]))
    if len(entries) > 50:
        lines.append("  ... and %d more" % (len(entries) - 50))
    lines.extend([
        "Engineering   : task %s %s" % (
            herd["engineering_complete"].get("task_id"),
            herd["engineering_complete"].get("status"),
        ),
        "Reviewer      : %s round %s (%s)" % (
            herd["reviewer_approve"].get("decision"),
            herd["reviewer_approve"].get("round"),
            herd["reviewer_approve"].get("review_file_name"),
        ),
        "Verification  : %s -> exit %d, log sha256 %s"
        % (" ".join(verification_argv), args.verification_exit_status,
           sha256_hex(log)),
        "Reverify with : %s" % " ".join(reverify_argv),
        "Committer     : %s <%s> (unsigned: commit.gpgsign=false on the"
        " argv)" % (committer_name, committer_email),
        "Allowed       : %s" % ", ".join(auth.STEPS),
        "Not allowed   : merge, auto-merge, tag, release, deploy, publish,"
        " force push",
        "Expires       : %d seconds from authorization" % validity,
        "Human         : %s (local terminal)" % human_identity,
        "",
    ])
    out.write("\n".join(lines) + "\n")
    typed = confirmation_reader(
        "Type the first %d characters of the candidate identity to"
        " authorize exactly this delivery: " % CONFIRMATION_CHARS
    ).strip()
    if typed != digest[:CONFIRMATION_CHARS]:
        raise CeremonyError("Not authorized. No delivery record created.")
    authority = auth.authority_template(
        bindings, evidence, reverify_argv,
        {
            "workflow_id": args.workflow_id,
            "engineering_task_id": str(
                herd["engineering_complete"].get("task_id")
            ),
        },
        mission,
        {
            "title": args.title,
            "objective": _read_text(args.objective),
            "architecture_notes": _read_text(args.architecture_notes),
            "nonblocking_risks": _read_text(args.nonblocking_risks),
        },
    )
    authority.update({
        "human_authorization": {
            "identity": human_identity,
            "source": auth.AUTHORIZATION_SOURCE_LOCAL_TERMINAL,
            "authorized_at": now,
            "confirmation_digest_sha256": text_digest(typed),
        },
        "expiration": {
            "policy": auth.EXPIRATION_POLICY_ABSOLUTE,
            "expires_at": now + validity,
        },
    })
    return authority


def authorize_cmd(args, store_dir=None, confirmation_reader=None, out=None):
    # The ONE construction site of the real transport is build_machine.
    machine = build_machine(store_dir)
    now = time.time()
    authority = assemble_authority(
        machine.transport, args, now, getpass.getuser(),
        confirmation_reader or _terminal_confirmation, out=out,
    )
    delivery_id = "prd-" + secrets.token_hex(12)
    _record, _inserted, pruned = _mint(machine.store, delivery_id, authority,
                                       now, reuse_existing=False)
    (out if out is not None else sys.stdout).write(
        "Authorized PR delivery %s (pruned %d terminal record(s)).\n"
        % (delivery_id, pruned)
    )
    return delivery_id


def _terminal_confirmation(prompt):
    if not sys.stdin.isatty():
        raise CeremonyError(
            "the authorization ceremony requires an interactive terminal"
        )
    return input(prompt)


def _emit(document):
    sys.stdout.write(json.dumps(document, indent=2, sort_keys=True) + "\n")


def status_cmd(args, store_dir=None):
    boundary = PrDeliveryBoundary(build_machine(store_dir))
    _emit(boundary.status(args.delivery_id))


def advance_cmd(args, store_dir=None):
    boundary = PrDeliveryBoundary(build_machine(store_dir))
    _emit(boundary.advance(args.delivery_id))


def revoke_cmd(args, store_dir=None):
    boundary = PrDeliveryBoundary(build_machine(store_dir))
    _emit(boundary.revoke(args.delivery_id, getpass.getuser(),
                          args.reason or ""))


def build_parser():
    parser = argparse.ArgumentParser(prog="pr_delivery")
    sub = parser.add_subparsers(dest="command", required=True)

    q = sub.add_parser("authorize")
    q.add_argument("--repo", default=os.getcwd())
    q.add_argument("--workflow-id", required=True)
    q.add_argument("--herd-evidence", required=True,
                   help="JSON from `herdctl delivery-evidence`")
    q.add_argument("--verification-log", required=True)
    q.add_argument("--verification-command", required=True)
    q.add_argument("--verification-exit-status", type=int, required=True)
    q.add_argument("--verification-ran-at", type=float, default=None)
    q.add_argument("--reverify-command", default=None)
    q.add_argument("--title", required=True)
    q.add_argument("--objective", default="")
    q.add_argument("--architecture-notes", default="")
    q.add_argument("--nonblocking-risks", default="")
    q.add_argument("--base-branch", default="main")
    q.add_argument("--remote", default="origin")
    q.add_argument("--validity-seconds", type=int,
                   default=auth.DEFAULT_AUTHORIZATION_VALIDITY_SECONDS)
    q.add_argument("--mission-workflow-id", default=None)
    q.add_argument("--mission-authorization-digest", default=None)
    q.set_defaults(fn=authorize_cmd)

    for name, fn in (("status", status_cmd), ("advance", advance_cmd),
                     ("revoke", revoke_cmd)):
        q = sub.add_parser(name)
        q.add_argument("--delivery-id", required=True)
        if name == "revoke":
            q.add_argument("--reason", default="")
        q.set_defaults(fn=fn)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (CeremonyError, StoreError, auth.AuthorizationError,
            candidate_module.CandidateError, DeliveryTransportError,
            MachineError) as exc:
        sys.stderr.write("%s\n" % exc)
        return 1
    return 0
