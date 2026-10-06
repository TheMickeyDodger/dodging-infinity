"""``python -m pr_delivery``: the human authorization ceremony and the drive.

This is the ONLY production module that constructs the real
``DeliveryTransport()`` (zero arguments, no override) and the
``DeliveryMachine``, and the ONLY place a PR Delivery Authorization is
minted (one construction site, ``_mint``) — after one of two ceremonies:

- ``authorize`` (``local_terminal``): the human sees every binding and
  TYPES the first twelve hex characters of the exact candidate identity at
  an interactive terminal. No model, transport, Worker, Capability,
  OperatorSession, or Herdr role can drive it: it reads the confirmation
  from the terminal.
- ``present-dots`` then ``attest-dots`` (``dots_operator_attested``, Task 8,
  user decision): the FULL binding is presented for the phone to display,
  and the human's simple whole-value affirmative is RELAYED by the Outer
  Operator, linked to that exact proposal by its digest and a reply-to
  reference. It is Operator-attested and NOT independently verified: every
  digest and reference is Operator attestation, not verified authorship,
  and a same-user operator or local process could fabricate the relay. The
  live candidate must still be exactly the presented one. It never records
  or claims ``local_terminal``, and an engineering approval alone never
  produces it.

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


def build_machine(store_dir=None):
    directory = store_dir or store_directory()
    return DeliveryMachine(DeliveryStore(directory), DeliveryTransport(),
                           time.time)


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


def _live_repository(transport, repo_path, base_branch, remote_name):
    """The LIVE repository facts every ceremony binds, read now: the
    repository, its remote, the source and base refs, the baseline, and the
    exact staged candidate with its identity digest."""
    repo = os.path.realpath(repo_path)
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
    source_branch = head_ref[len("refs/heads/"):]
    base_ref = "refs/heads/" + base_branch
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
    remote_base = transport.ls_remote(repo, remote_name, base_ref)
    if remote_base is None:
        raise CeremonyError("remote %r has no %r" % (remote_name, base_ref))
    if remote_base != head:
        transport.fetch_ref(repo, remote_name, base_ref)
    if remote_base != head and not transport.is_ancestor(repo, head,
                                                          remote_base):
        raise CeremonyError(
            "HEAD %s is not on the base branch %s (remote at %s); the"
            " candidate must sit directly on the base"
            % (head, base_branch, remote_base)
        )
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
    digest = candidate_module.identity_digest(entries)
    committer_name = transport.config_get(repo, "user.name")
    committer_email = transport.config_get(repo, "user.email")
    if not committer_name or not committer_email:
        raise CeremonyError(
            "git user.name and user.email must be configured for this"
            " repository; the delivery commits under them"
        )
    return {
        "repo": repo, "git_dir": git_dir, "head_ref": head_ref,
        "source_branch": source_branch, "base_branch": base_branch,
        "base_ref": base_ref, "remote_name": remote_name,
        "url_exact": url_exact, "url_fetch": url_fetch, "url_push": url_push,
        "target": target, "head": head, "remote_base": remote_base,
        "entries": entries, "digest": digest,
        "committer_name": committer_name, "committer_email": committer_email,
    }


def _gather(transport, args, now):
    """Every binding from the live repository and the human's inputs, with
    the display lines that present them: the FULL binding, before any
    ceremony. Returns ``(binding, lines, digest, validity)``, where
    ``binding`` holds exactly ``auth.DELIVERY_PROPOSAL_BINDING_KEYS``."""
    live = _live_repository(transport, args.repo, args.base_branch, args.remote)
    repo, git_dir, head_ref = live["repo"], live["git_dir"], live["head_ref"]
    source_branch, base_branch = live["source_branch"], live["base_branch"]
    base_ref, remote_name = live["base_ref"], live["remote_name"]
    url_exact, url_fetch = live["url_exact"], live["url_fetch"]
    url_push, target, head = live["url_push"], live["target"], live["head"]
    remote_base, entries, digest = (live["remote_base"], live["entries"],
                                    live["digest"])
    committer_name = live["committer_name"]
    committer_email = live["committer_email"]
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
    ])
    binding = {
        "revision": 1,
        "previous_delivery_id": None,
        "workflow_identity": {
            "workflow_id": args.workflow_id,
            "engineering_task_id": str(
                herd["engineering_complete"].get("task_id")
            ),
        },
        "mission": mission,
        "repository": {
            "realpath": repo,
            "git_dir_realpath": git_dir,
            "canonical_host": target.host,
            "owner": target.owner,
            "repo": target.repo,
            "repository_url": target.repository_url,
        },
        "remote": {
            "name": remote_name,
            "url_exact": url_exact,
            "url_fetch": url_fetch,
            "url_push": url_push,
            "repository_url": target.repository_url,
        },
        "mode": auth.MODE_PULL_REQUEST,
        "source": {"branch": source_branch, "ref": head_ref},
        "target_base": {"branch": base_branch, "ref": base_ref},
        "original_baseline": {"ref": base_ref, "commit_sha": head},
        "candidate": {
            "identity_digest_sha256": digest,
            "entry_count": len(entries),
            "entries": entries,
        },
        "evidence": evidence,
        "allowed_actions": list(auth.STEPS),
        "committer": {"name": committer_name, "email": committer_email},
        "reverification": {"argv": reverify_argv},
        "pr_content": {
            "title": args.title,
            "objective": _read_text(args.objective),
            "architecture_notes": _read_text(args.architecture_notes),
            "nonblocking_risks": _read_text(args.nonblocking_risks),
        },
    }
    return binding, lines, digest, validity


def assemble_authority(transport, args, now, human_identity,
                       confirmation_reader, out=None):
    """Gather every binding from the live repository and the human's
    inputs, run the LOCAL TERMINAL ceremony, and return the AUTHORITY
    dictionary. The human TYPES the first characters of the candidate
    identity; the interactive-terminal requirement is enforced by the
    reader ``authorize_cmd`` passes."""
    out = out if out is not None else sys.stdout
    binding, lines, digest, validity = _gather(transport, args, now)
    lines = lines + [
        "Human         : %s (local terminal)" % human_identity,
        "",
    ]
    out.write("\n".join(lines) + "\n")
    typed = confirmation_reader(
        "Type the first %d characters of the candidate identity to"
        " authorize exactly this delivery: " % CONFIRMATION_CHARS
    ).strip()
    if typed != digest[:CONFIRMATION_CHARS]:
        raise CeremonyError("Not authorized. No delivery record created.")
    authority = dict(binding)
    authority["human_authorization"] = {
        "identity": human_identity,
        "source": auth.AUTHORIZATION_SOURCE_LOCAL_TERMINAL,
        "authorized_at": now,
        "confirmation_digest_sha256": text_digest(typed),
    }
    authority["expiration"] = {
        "policy": auth.EXPIRATION_POLICY_ABSOLUTE,
        "expires_at": now + validity,
    }
    return authority


def _mint(machine, authority, now, out, one_shot_proposal_digest=None):
    """The ONE place a PR Delivery Authorization is constructed, for both
    ceremonies. ``one_shot_proposal_digest`` (the Dots ceremony) refuses,
    under the store lock, a second authorization of the same presented
    proposal."""
    delivery_id = "prd-" + secrets.token_hex(12)
    record = auth.new_authorization(delivery_id, authority, now)
    store = machine.store
    with store.lock():
        document = store.load()
        if one_shot_proposal_digest is not None:
            for existing_id, existing in document["deliveries"].items():
                attestation = existing["human_authorization"].get("attestation")
                if attestation and attestation["proposal_digest_sha256"] == (
                    one_shot_proposal_digest
                ):
                    raise CeremonyError(
                        "delivery proposal %s was already attested as %s; a"
                        " proposal authorizes once. No delivery record created."
                        % (one_shot_proposal_digest, existing_id))
        ok, problem, pruned = add_delivery(document, record)
        if not ok:
            raise CeremonyError("store refused the record: %s" % problem)
        store.save(document)
    (out if out is not None else sys.stdout).write(
        "Authorized PR delivery %s (pruned %d terminal record(s)).\n"
        % (delivery_id, pruned)
    )
    return delivery_id


def authorize_cmd(args, store_dir=None, confirmation_reader=None, out=None):
    # The ONE construction site of the real transport is build_machine.
    machine = build_machine(store_dir)
    now = time.time()
    authority = assemble_authority(
        machine.transport, args, now, getpass.getuser(),
        confirmation_reader or _terminal_confirmation, out=out,
    )
    return _mint(machine, authority, now, out)


# -- the Dots operator-attested ceremony (Task 8, user decision) ----------
#
# PRESENT shows the FULL binding (candidate, Mission, revision, scope,
# targets, deadline) for the phone to display; nothing is written. ATTEST
# relays the human's simple affirmative ("approved" or "approve", the WHOLE
# reply) for exactly that presented proposal, linked by its digest and the
# chat reference it replies to. The human never types a digest: every
# digest and reference here is supplied by the Operator and recorded as
# Operator attestation, not verified authorship. The live candidate is
# re-read and must still be exactly the presented one; any mismatch,
# ambiguity, staleness or repeat refuses with nothing written.

MAX_DOTS_INPUT_CHARS = 4194304


def present_dots_cmd(args, store_dir=None, out=None):
    machine = build_machine(store_dir)
    now = time.time()
    binding, lines, digest, validity = _gather(machine.transport, args, now)
    proposal = auth.delivery_proposal(binding, now, now + validity)
    # The Dots deadline is fixed AT PRESENTATION, not at approval: show the
    # absolute deadline (the terminal ceremony's wording is unchanged).
    terminal_expiry = "Expires       : %d seconds from authorization" % validity
    lines = [
        "Expires       : %s (absolute; %d seconds from presentation, not from"
        " approval)" % (time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(proposal["expires_at"])),
                        validity)
        if line == terminal_expiry else line
        for line in lines
    ]
    document = {
        "delivery_proposal": proposal,
        "proposal_digest_sha256": auth.delivery_proposal_digest(proposal),
        "display": "\n".join(lines + [
            "Approver      : the human, by a simple reply relayed by the Outer"
            " Operator (operator-attested, not independently verified)",
        ]),
        "reply": "Reply exactly 'approved' or 'approve' to authorize THIS"
                 " delivery; no digest is ever typed",
        "source": auth.AUTHORIZATION_SOURCE_DOTS_OPERATOR_ATTESTED,
        "residual_risk": auth.DOTS_RESIDUAL_RISK,
    }
    (out if out is not None else sys.stdout).write(
        json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def _live_matches(live, binding):
    """Which presented binding the LIVE repository no longer matches, or
    None: a substituted or different candidate is never authorized."""
    expected = {
        "repository": binding["repository"]["realpath"],
        "git_dir": binding["repository"]["git_dir_realpath"],
        "remote_exact": binding["remote"]["url_exact"],
        "remote_fetch": binding["remote"]["url_fetch"],
        "remote_push": binding["remote"]["url_push"],
        "source_ref": binding["source"]["ref"],
        "base_ref": binding["target_base"]["ref"],
        "baseline": binding["original_baseline"]["commit_sha"],
        "candidate": binding["candidate"]["identity_digest_sha256"],
        "entries": binding["candidate"]["entries"],
        "committer": [binding["committer"]["name"],
                      binding["committer"]["email"]],
    }
    actual = {
        "repository": live["repo"], "git_dir": live["git_dir"],
        "remote_exact": live["url_exact"], "remote_fetch": live["url_fetch"],
        "remote_push": live["url_push"], "source_ref": live["head_ref"],
        "base_ref": live["base_ref"], "baseline": live["head"],
        "candidate": live["digest"], "entries": live["entries"],
        "committer": [live["committer_name"], live["committer_email"]],
    }
    for key in sorted(expected):
        if expected[key] != actual[key]:
            return key
    return None


def attest_dots_cmd(args, stdin_text, store_dir=None, out=None):
    """Relay the human's affirmative for exactly the presented proposal.
    ``stdin_text`` carries ``{"delivery_proposal": ..., "relayed_reply": ...}``
    (both supplied by the Operator)."""
    if len(stdin_text) > MAX_DOTS_INPUT_CHARS:
        raise CeremonyError("the attestation input is too large")
    try:
        document = json.loads(stdin_text)
    except ValueError as exc:
        raise CeremonyError("the attestation input is not JSON (%s)" % exc)
    if not isinstance(document, dict) or sorted(document) != [
        "delivery_proposal", "relayed_reply"
    ]:
        raise CeremonyError(
            "the attestation input must carry exactly delivery_proposal and"
            " relayed_reply")
    # The WHOLE reply, checked before anything is read or written.
    if not auth.is_dots_affirmative(document["relayed_reply"]):
        raise CeremonyError(
            "the relayed reply is not an exact affirmative: only the whole"
            " reply 'approved' or 'approve' counts. No delivery record"
            " created.")
    for value, name in ((args.reply_to, "--reply-to"),
                        (args.relay_ref, "--relay-ref"),
                        (args.proposal_digest, "--proposal-digest")):
        if not value or "\n" in value or len(value) > auth.MAX_RELAY_REF_CHARS:
            raise CeremonyError(
                "%s is required (a reply is never applied to a guessed"
                " proposal). No delivery record created." % name)
    proposal = document["delivery_proposal"]
    if not isinstance(proposal, dict) or sorted(proposal) != [
        "binding", "expires_at", "presented_at"
    ] or not isinstance(proposal["binding"], dict) or sorted(
        proposal["binding"]
    ) != sorted(auth.DELIVERY_PROPOSAL_BINDING_KEYS):
        raise CeremonyError("the delivery proposal is not a presented proposal")
    digest = auth.delivery_proposal_digest(proposal)
    if digest != args.proposal_digest:
        raise CeremonyError(
            "the reply links to proposal %s, not to this presented proposal"
            " (%s): ambiguous or substituted, refused. No delivery record"
            " created." % (args.proposal_digest, digest))
    machine = build_machine(store_dir)
    now = time.time()
    expires_at = proposal["expires_at"]
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool) or (
        now >= expires_at
    ):
        raise CeremonyError(
            "the presented proposal expired; present it again. No delivery"
            " record created.")
    binding = proposal["binding"]
    live = _live_repository(machine.transport,
                            binding["repository"]["realpath"],
                            binding["target_base"]["branch"],
                            binding["remote"]["name"])
    mismatch = _live_matches(live, binding)
    if mismatch is not None:
        raise CeremonyError(
            "the live repository no longer matches the presented %s: a"
            " substituted or different candidate is never authorized. No"
            " delivery record created." % mismatch)
    attestation = {
        "proposal_digest_sha256": digest,
        "presented_at": proposal["presented_at"],
        "reply_to": args.reply_to, "relayed_reply": document["relayed_reply"],
        "relay_ref": args.relay_ref,
        "confirmation": auth.DOTS_CONFIRMATION,
        "provenance": auth.DOTS_PROVENANCE,
        "residual_risk": auth.DOTS_RESIDUAL_RISK,
    }
    authority = dict(binding)
    authority["human_authorization"] = {
        "identity": auth.DOTS_ATTESTED_IDENTITY,
        "source": auth.AUTHORIZATION_SOURCE_DOTS_OPERATOR_ATTESTED,
        "authorized_at": now,
        "confirmation_digest_sha256": auth.dots_confirmation_digest(attestation),
        "attestation": attestation,
    }
    authority["expiration"] = {
        "policy": auth.EXPIRATION_POLICY_ABSOLUTE,
        "expires_at": expires_at,
    }
    return _mint(machine, authority, now, out, one_shot_proposal_digest=digest)


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


def _attest_dots_from_stdin(args):
    return attest_dots_cmd(args, sys.stdin.read(MAX_DOTS_INPUT_CHARS + 1))


def build_parser():
    parser = argparse.ArgumentParser(prog="pr_delivery")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn in (("authorize", authorize_cmd),
                     ("present-dots", present_dots_cmd)):
        _ceremony_arguments(sub.add_parser(name), fn)

    q = sub.add_parser("attest-dots")
    q.add_argument("--proposal-digest", required=True,
                   help="the presented proposal the human's reply answers")
    q.add_argument("--reply-to", required=True,
                   help="the chat reference the reply is linked to")
    q.add_argument("--relay-ref", required=True)
    q.set_defaults(fn=_attest_dots_from_stdin)

    for name, fn in (("status", status_cmd), ("advance", advance_cmd),
                     ("revoke", revoke_cmd)):
        q = sub.add_parser(name)
        q.add_argument("--delivery-id", required=True)
        if name == "revoke":
            q.add_argument("--reason", default="")
        q.set_defaults(fn=fn)
    return parser


def _ceremony_arguments(q, fn):
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
    q.set_defaults(fn=fn)


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
