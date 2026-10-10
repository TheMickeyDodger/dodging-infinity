"""The SEPARATE delivery ceremony, relayed from Grok Bot: pr_delivery's own
``present-dots`` then ``attest-dots``, unchanged, and its read-only status.

What this module does, and nothing more:

- ``present`` validates the caller's ceremony arguments (exactly
  ``present-dots``' own, ``grok_bot.adapter.DELIVERY_PRESENT_FIELDS``) and
  hands them to ``pr_delivery.cli.present_dots_cmd``, which reads the live
  repository and returns the delivery proposal and its digest.
- ``display_text`` renders that proposal COMPLETE: every field, every
  candidate entry, the whole evidence and pull-request content, with
  nothing truncated or elided (pr_delivery's own summary lists only the
  first 50 entries; this text does not). Line boundaries and lone
  surrogates are escaped losslessly, as for Mission proposals.
- ``attest`` hands the presented proposal named by the human-approved
  digest and the relayed reply to ``pr_delivery.cli.attest_dots_cmd``. That
  ceremony checks the whole-reply affirmative, the digest link, the expiry
  and the LIVE candidate again, and records an operator-attested
  (``dots_operator_attested``) PR Delivery Authorization once.
- ``status`` reads a delivery's status through ``PrDeliveryBoundary``.

Construction: this module never constructs a delivery transport, machine
or store DIRECTLY. Construction is core-owned, by pr_delivery's own
``pr_delivery.cli.build_machine()`` (``pr_delivery/cli.py`` is the only
production site constructing the real zero-argument
``DeliveryTransport()``). That runs inside the ceremony's
``present_dots_cmd`` and ``attest_dots_cmd`` and, called from here, solely
for the read-only status projection (``status``), never to advance a step.

What it never does: mint (only pr_delivery's ceremony does, inside
``attest_dots_cmd``), or advance, perform or revoke a step. Nothing here
commits, pushes, opens a pull request, merges, tags, releases or deploys.
An engineering approval never reaches it. This is the
only module outside ``pr_delivery/`` and ``herdr/guards.py`` that imports
``pr_delivery`` (pinned in ``tests/test_static.py``), and the adapter imports
it lazily, only when a delivery tool is called.

Network and local effects: this module's own code opens no connection, and
nothing here suppresses the ceremony's. ``present`` and ``attest`` both run
pr_delivery's live-repository read through its real git transport: local
reads (such as ``status --no-optional-locks``, ``diff-index``, ``config``),
``git ls-remote`` against the configured remote, and, when the remote base
differs from HEAD, ``git fetch --no-tags`` of the base branch. For a
``pr_update`` proposal (``pr_number`` and ``head_branch`` given) the read
instead runs ``gh pr view`` for that one pull request, ``git ls-remote`` of
its head branch, NUL-separated ``git status`` and ``git diff --cached`` (the
staged hash), and fetches nothing. With a real remote those can reach an
external host, and a fetch writes local repository data. ``status`` reads
pr_delivery's store only.

Provenance: the relayed reply is OPERATOR-ATTESTED, not cryptographically
authenticated; pr_delivery records its residual risk on the authorization.
"""

import io
import json
import math
import os
import sys
from types import SimpleNamespace

from pr_delivery import (
    authorization as delivery_auth,
    boundary as delivery_boundary,
    candidate as delivery_candidate,
    cli as delivery_cli,
    machine as delivery_machine,
    store as delivery_store,
    transport as delivery_transport,
)

from grok_bot import adapter as adapter_module

PROBLEM_REFUSED = "grok_bot_delivery_refused"
PROBLEM_UNREADABLE = "grok_bot_delivery_input_unreadable"
# Exactly the refusals pr_delivery's own command line reports.
CEREMONY_ERRORS = (
    delivery_cli.CeremonyError, delivery_store.StoreError,
    delivery_auth.AuthorizationError, delivery_candidate.CandidateError,
    delivery_transport.DeliveryTransportError, delivery_machine.MachineError,
)
# pr_delivery reads these three as ``@path`` file references; over this
# transport they must be literal text, so no local file is read into a
# proposal the phone displays.
LITERAL_TEXT_FIELDS = ("objective", "architecture_notes", "nonblocking_risks")
# The authorization span pr_delivery enforces at mint (expiry after the
# authorization, at most this far after it), applied before arithmetic.
VALIDITY_SPAN = delivery_auth.MAX_AUTHORIZATION_VALIDITY_SECONDS
NEVER_AUTHORIZED = ("merge, auto-merge, tag, release, deploy, publish,"
                    " force push")
# A ``pr_update`` proposal additionally never authorizes these two steps.
PR_UPDATE_NEVER_AUTHORIZED = ("base refresh, pull request creation, "
                              + NEVER_AUTHORIZED)


def never_authorized(binding):
    """What the proposal's own kind never authorizes."""
    if binding.get("mode") == delivery_auth.MODE_PR_UPDATE:
        return PR_UPDATE_NEVER_AUTHORIZED
    return NEVER_AUTHORIZED


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def fits_finite_float(value):
    """True for a number present-dots' ``--verification-ran-at`` (argparse
    ``type=float``) could hold. ``math.isfinite`` asks a FLOAT question and
    raises OverflowError on an int too large to convert (S3-N1), so it is
    never asked of an int: an int is compared, exactly, with the largest
    finite float instead, which never converts it."""
    if _is_int(value):
        return -sys.float_info.max <= value <= sys.float_info.max
    return isinstance(value, float) and math.isfinite(value)


def _refuse(problem, reason, **details):
    raise adapter_module.surface_module.LocalRequestRefusal(problem, reason,
                                                            **details)


def _ceremony(operation):
    try:
        return operation()
    except CEREMONY_ERRORS as exc:
        _refuse(PROBLEM_REFUSED, str(exc), ceremony_problem=type(exc).__name__)
    except (OSError, ValueError) as exc:
        _refuse(PROBLEM_UNREADABLE, "a ceremony input could not be read (%s:"
                " %s)" % (type(exc).__name__, exc))


def ceremony_arguments(arguments):
    """The caller's arguments as ``present-dots`` receives them, checked
    for type before anything is read. Absent optional fields take
    ``present-dots``' own defaults."""
    values = {}
    for name, kind, required, default in adapter_module.DELIVERY_PRESENT_FIELDS:
        value = arguments.get(name)
        if value is None:
            if required:
                _refuse(adapter_module.PROBLEM_BAD_REQUEST,
                        "present_delivery needs %s" % name)
            values[name] = (delivery_auth.DEFAULT_AUTHORIZATION_VALIDITY_SECONDS
                            if name == "validity_seconds" else default)
            continue
        requirement = {
            "path": "an absolute path",
            "text": "literal text (no @file reference)",
            "integer": "an integer",
            "number": "a finite number"}[kind]
        if kind == "path":
            ok = isinstance(value, str) and os.path.isabs(value)
        elif kind == "text":
            ok = isinstance(value, str) and not (
                name in LITERAL_TEXT_FIELDS and value.startswith("@"))
        elif name == "validity_seconds":
            # present-dots computes ``now + validity`` and formats that
            # expiry before pr_delivery checks the span (at mint), so an
            # unbounded int overflows inside the ceremony. pr_delivery's
            # own span is applied here instead: a value outside it could
            # never be authorized.
            ok = _is_int(value) and 1 <= value <= VALIDITY_SPAN
            requirement = ("an integer from 1 to %d (pr_delivery's own"
                           " authorization span)" % VALIDITY_SPAN)
        elif kind == "integer":
            ok = _is_int(value)
        else:  # number
            ok = fits_finite_float(value)
        if not ok:
            _refuse(adapter_module.PROBLEM_BAD_REQUEST,
                    "present_delivery %s must be %s" % (name, requirement))
        values[name] = value
    return SimpleNamespace(**values)


def present(args):
    """``present-dots``' own document for ``args``."""
    return _ceremony(lambda: delivery_cli.present_dots_cmd(args, out=io.StringIO()))


def display_text(document):
    """The human-facing text: what the approval authorizes and refuses,
    then the COMPLETE presented delivery proposal. Never shortened."""
    proposal = document["delivery_proposal"]
    binding = proposal["binding"]
    lines = [
        "DODGING INFINITY DELIVERY PROPOSAL (separate from any engineering"
        " approval)",
        "Proposal digest (sha256): %s" % document["proposal_digest_sha256"],
        "Presented at (unix seconds): %s" % json.dumps(proposal["presented_at"]),
        "Approval expires at (unix seconds): %s"
        % json.dumps(proposal["expires_at"]),
    ] + update_summary_lines(binding) + [
        # Derived from the proposal's OWN allowed actions, which the minted
        # record carries exactly (its proposal digest binds them).
        "Authorizes only: %s, for exactly this candidate"
        % ", ".join(binding["allowed_actions"]),
        "Never authorized: %s" % never_authorized(binding),
        "",
        "The complete delivery proposal, every field exactly as recorded"
        " (nothing is omitted):",
    ] + adapter_module.rendered_lines("delivery_proposal", proposal) + [
        "",
        "To approve exactly this delivery, reply with a separate message"
        " containing only: approved",
        "An engineering approval never authorizes delivery. Approving this"
        " records a delivery authorization and performs no delivery step:"
        " this transport runs none of them. (Presenting and approving re-read"
        " the live repository, including the configured remote.)",
        "Approval is operator-attested, not cryptographically authenticated.",
    ]
    return "\n".join(adapter_module.one_line(line) for line in lines)


def update_summary_lines(binding):
    """For a ``pr_update`` proposal, the existing pull request it updates,
    named up front (every value is also in the complete proposal below);
    nothing for a ``pull_request`` proposal, whose display is unchanged."""
    if binding.get("mode") != delivery_auth.MODE_PR_UPDATE:
        return []
    candidate = binding["candidate"]
    return [
        "Delivery kind: %s (one new commit on existing open pull request #%s,"
        " a strict fast-forward of its head)"
        % (binding["mode"], json.dumps(binding["pull_request_number"])),
        "Delivery id (minted exactly on approval): %s"
        % binding["delivery_id"],
        "Pull request: #%s, head branch %s -> base branch %s"
        % (json.dumps(binding["pull_request_number"]),
           json.dumps(binding["source"]["branch"]),
           json.dumps(binding["target_base"]["branch"])),
        "Expected head SHA (the one new commit's parent): %s"
        % binding["original_baseline"]["commit_sha"],
        "Staged hash (sha256 of git diff --cached --binary against the"
        " expected head): %s" % binding["staged_sha256"],
        "Candidate identity (status, mode, blob and path of every entry; a"
        " separate binding): %s, %s entries"
        % (candidate["identity_digest_sha256"],
           json.dumps(candidate["entry_count"])),
    ]


def attest(proposal, digest, reply, reply_to, relay_ref):
    """``attest-dots`` for exactly ``proposal`` (the presented one named by
    ``digest``) and the relayed reply; returns the delivery id."""
    args = SimpleNamespace(proposal_digest=digest, reply_to=reply_to,
                           relay_ref=relay_ref)
    stdin_text = json.dumps({"delivery_proposal": proposal,
                             "relayed_reply": reply})
    return _ceremony(lambda: delivery_cli.attest_dots_cmd(
        args, stdin_text, out=io.StringIO()))


def grant(delivery_id, proposal):
    """What the ceremony recorded, stated plainly: an authorization granted
    by pr_delivery, never by this transport, with no step performed. The
    authorized steps are the attested proposal's own allowed actions, which
    the minted record carries exactly: pr_delivery copies them from that
    binding and its validator re-proves the proposal digest over them."""
    binding = proposal["binding"]
    return {
        "delivery_id": delivery_id,
        "granted_by": "pr_delivery attest-dots: an operator-attested relay of"
                      " the human's separate reply",
        "granted_by_this_transport": False,
        "source": delivery_auth.AUTHORIZATION_SOURCE_DOTS_OPERATOR_ATTESTED,
        "provenance": delivery_auth.DOTS_PROVENANCE,
        "residual_risk": delivery_auth.DOTS_RESIDUAL_RISK,
        "authorized_steps": list(binding["allowed_actions"]),
        "performed_steps": [],
        "never_authorized": never_authorized(binding),
        "performance": "this transport performs no step; the authorized steps"
                       " run only through pr_delivery's own drive, outside"
                       " this adapter",
    }


def status(delivery_id):
    """Read-only: pr_delivery's status projection of one delivery."""
    return _ceremony(lambda: delivery_boundary.PrDeliveryBoundary(
        delivery_cli.build_machine()).status(delivery_id))
