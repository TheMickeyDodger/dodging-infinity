"""Local arming of an approval: the pure half.

An approval relayed through Grok Bot is accepted only when the human first
ARMED it by a local action on this Mac: the arming command
(``grokbot.py ... authorize`` or ``authorize-delivery``), run in Grok Bot's
per-command, user-approved local shell, carries the FULL displayed binding.
The human approves that exact command string. ``grok_bot.authorize``, the
ONLY writer, checks every value against the latest presentation receipt
before anything takes effect, then stores a one-way COMMITMENT and prints a
one-time code. The ``approve`` / ``approve_delivery`` tools then fire it:
the code must reproduce the commitment over exactly the displayed binding.

This module holds what both halves share, and writes nothing:

- the commitment preimage, ``P``, for a Mission approval or a delivery
  approval (``kind`` separates the two). ``C = json_digest(P)`` is SHA-256
  over ``workflow_authority.digest.canonical_json_bytes``. ``P`` binds the
  request_ref, mission id, revision, proposal digest, approved action scope,
  approved delivery targets (order-exact, as displayed), expiry and the
  digest of the exact display text, plus a 128-bit random nonce. The code
  IS that nonce; it is never stored by DI;
- the read-only loader of the commitments file, which holds ``C`` and
  metadata, never the nonce or the code;
- the arming command's argv, built from the full displayed values.

What this rests on, and what it does not. Possession of the MCP bearer
token alone cannot produce an accepted approval: minting requires a local
arming action on this Mac, and two things carry that, neither sufficient
alone.

(i) Hash-only persistence, a property of this design: the commitments file
    holds ``C`` and metadata, never the nonce or the code, so read access to
    it (or to anything else DI stores) yields no mint material. Inverting
    ``C`` means guessing 128 random bits, and ``MAX_CODE_FAILURES`` wrong
    codes kill a commitment.
(ii) A DEPENDENCY, not a property of the commitment scheme: the
    request-path Operator, which the token holder's ``request`` text
    instructs, runs under the pinned fail-closed sandbox posture
    (``codex_gateway.role_turn.run_operator_turn``: read-only sandbox,
    ``approval_policy=never``, verified in argv before any spawn, refused
    otherwise). That posture is what is relied on to stop it writing the
    commitments file or spawning the arming command. DI cannot enforce the
    provider's sandbox itself: if that confinement does not hold on some host
    or provider version, a request-controlled Operator could arm an approval
    (it would see the code on its own output) and this separation fails.

Local arming is the boundary; the code relay is not. A code that the local
shell's output persisted somewhere readable (terminal saved state, for
example) fires only the exact binding the human armed, once, before its
expiry: it completes the human's own authorization and creates no new one.
Like the Git gates, local arming is a workflow guardrail: it is not designed
to contain processes running with the user's own privileges. DI cannot see
or enforce the vendor's local-shell approval policy: under "Always allow" no
per-command human approval occurs.
"""

import hmac
import json
import os
import shlex
import stat

from workflow_authority.digest import json_digest

SCHEME = "grok_bot.approval-commitment.v1"
KIND_MISSION = "mission"
KIND_DELIVERY = "delivery"
COMMITMENTS_FILE_NAME = "grok_bot_approval_commitments.json"
SCHEMA_VERSION = 1
NONCE_BYTES = 16
CODE_HEX_CHARS = 32
MAX_CODE_FAILURES = 5
MAX_COMMITMENTS = 1024

PROBLEM_NOT_ARMED = "grok_bot_approval_not_armed"
PROBLEM_CONSUMED = "grok_bot_approval_consumed"
PROBLEM_CODE_MISMATCH = "grok_bot_approval_code_mismatch"
PROBLEM_ARMING_NOT_DISPLAYED = "grok_bot_arming_not_displayed"
PROBLEM_ARMING_EXPIRED = "grok_bot_arming_expired"
PROBLEM_COMMITMENTS_UNREADABLE = "grok_bot_approval_commitments_unreadable"
PROBLEM_COMMITMENTS_FULL = "grok_bot_approval_commitments_full"

RECORD_KEYS = ("kind", "commitment_sha256", "expires_at", "armed_at")
_HEX = frozenset("0123456789abcdef")


class CommitmentsError(Exception):
    def __init__(self, message, problem=PROBLEM_COMMITMENTS_UNREADABLE):
        super(CommitmentsError, self).__init__(message)
        self.problem = problem


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and not set(value) - _HEX


def is_code(value):
    """A code is exactly the nonce's 32 lowercase hex characters."""
    return _hex(value, CODE_HEX_CHARS)


def mission_key(request_ref):
    return "mission:" + request_ref


def delivery_key(proposal_digest):
    return "delivery:" + proposal_digest


def mission_preimage(binding, display_digest, nonce):
    """``P`` for a Mission approval: every displayed binding field, the
    display digest and the nonce. Lists keep their displayed order."""
    return {
        "scheme": SCHEME, "kind": KIND_MISSION,
        "request_ref": binding["request_ref"],
        "mission_id": binding["mission_id"],
        "revision": binding["revision"],
        "proposal_digest_sha256": binding["proposal_digest_sha256"],
        "approved_action_scope": list(binding["approved_action_scope"]),
        "approved_delivery_targets": list(binding["approved_delivery_targets"]),
        "expires_at": binding["expires_at"],
        "display_digest_sha256": display_digest,
        "nonce": nonce,
    }


def delivery_preimage(proposal_digest, expires_at, display_digest,
                      repository_realpath, nonce):
    """``P`` for a delivery approval. The proposal digest covers
    pr_delivery's whole proposal (candidate, remote, steps, evidence)."""
    return {
        "scheme": SCHEME, "kind": KIND_DELIVERY,
        "proposal_digest_sha256": proposal_digest,
        "expires_at": expires_at,
        "display_digest_sha256": display_digest,
        "repository_realpath": repository_realpath,
        "nonce": nonce,
    }


def commitment(preimage):
    return json_digest(preimage)


def reproduces(stored_commitment, preimage):
    """Constant-time: whether ``preimage`` reproduces the stored ``C``."""
    return hmac.compare_digest(commitment(preimage).encode("ascii"),
                               stored_commitment.encode("ascii"))


def validate(document, where):
    def bad(message):
        raise CommitmentsError("%s %s" % (where, message))
    if not isinstance(document, dict) or sorted(document) != [
        "commitments", "schema_version"
    ] or document["schema_version"] != SCHEMA_VERSION:
        bad("must carry exactly commitments and schema_version %d"
            % SCHEMA_VERSION)
    records = document["commitments"]
    if not isinstance(records, dict) or len(records) > MAX_COMMITMENTS:
        bad("commitments must be a map of at most %d" % MAX_COMMITMENTS)
    for position, (key, record) in enumerate(records.items(), 1):
        kind = key.split(":", 1)[0] if isinstance(key, str) else None
        if kind not in (KIND_MISSION, KIND_DELIVERY) or not isinstance(
            record, dict
        ) or sorted(record) != sorted(RECORD_KEYS) or record["kind"] != kind or (
            not _hex(record["commitment_sha256"], 64)
        ) or isinstance(record["expires_at"], bool) or not isinstance(
            record["expires_at"], (int, float)
        ) or isinstance(record["armed_at"], bool) or not isinstance(
            record["armed_at"], int
        ):
            bad("commitment %d is malformed" % position)
    return document


def empty():
    return {"schema_version": SCHEMA_VERSION, "commitments": {}}


def commitments_path(directory):
    return os.path.join(directory, COMMITMENTS_FILE_NAME)


def refuse_shared(path):
    """Nothing is read from (or written to) a path group/other can reach."""
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return
    if mode & 0o077:
        raise CommitmentsError("%s is accessible by group/other (mode %o);"
                               " nothing is read or written"
                               % (path, stat.S_IMODE(mode)))


def load_commitments(directory):
    """The commitments document, validated; empty when there is none.
    Read-only: the MCP fire path uses this and never writes the file."""
    refuse_shared(directory)
    path = commitments_path(directory)
    if not os.path.exists(path):
        return empty()
    refuse_shared(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CommitmentsError("%s could not be read (%s)" % (path, exc))
    return validate(document, path)


def _json_arg(value):
    return json.dumps(value)


def mission_arming_argv(prefix, binding, display_digest):
    """The exact local command the human approves: every displayed value,
    the FULL digests, nothing shortened."""
    argv = list(prefix) + [
        "authorize",
        "--request-ref", binding["request_ref"],
        "--mission-id", binding["mission_id"],
        "--revision", _json_arg(binding["revision"]),
        "--proposal-digest", binding["proposal_digest_sha256"],
    ]
    for scope in binding["approved_action_scope"]:
        argv += ["--action-scope", scope]
    for target in binding["approved_delivery_targets"]:
        argv += ["--delivery-target", target]
    return argv + ["--expires-at", _json_arg(binding["expires_at"]),
                   "--display-digest", display_digest]


def delivery_arming_argv(prefix, proposal_digest, expires_at, display_digest,
                         repository_realpath):
    return list(prefix) + [
        "authorize-delivery",
        "--proposal-digest", proposal_digest,
        "--expires-at", _json_arg(expires_at),
        "--display-digest", display_digest,
        "--repository", repository_realpath,
    ]


def arming(argv):
    """What a presentation hands Grok Bot: the argv and its one shell-quoted
    command line, which is what the human approves in the local shell."""
    return {"argv": list(argv), "command": shlex.join(argv),
            "instructions": "Run this exact command in your local shell on"
                            " the DI machine and approve it only if every"
                            " value matches the proposal you were shown. It"
                            " prints a one-time approval_code; then relay the"
                            " human's separate 'approved' reply with that"
                            " code. Local arming is the approval boundary;"
                            " the code only completes it."}
