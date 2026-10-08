"""The adapter's own durable bookkeeping: the duplicate-request index and
the presentation receipts. Transport bookkeeping only.

One JSON document, ``grok_bot_requests.json``, in the same protected
state directory as the local request surface, written with the shared
``workflow_authority.atomic`` primitives under its own lock. Two maps:

- ``requests`` maps the digest of one request (its exact text and the
  caller's conversation reference) to the ONE local request that text
  produced, so a repeated tool call returns that request instead of
  asking the Operator again and proposing a second Mission.
- ``presentations`` holds, per request_ref, the receipt of what
  ``present`` last DISPLAYED: the exact binding and the digest of the
  display text. It is written before the display is returned, so nothing
  is shown that is not recorded. A later presentation of the same request
  replaces it: the latest display is the one a reply binds to.
- ``delivery_presentations`` (optional; slice 3) holds the delivery
  proposals ``present_delivery`` DISPLAYED, each keyed by its own digest
  (content-addressed: the key is the digest of exactly the proposal it
  holds, validated on every load), bounded at ``MAX_DELIVERY_RECEIPTS``.

``IN_FLIGHT`` is written BEFORE the Operator turn. It is removed when the
turn provably proposed nothing (the Operator failed, answered in prose,
or the surface refused its proposal), and becomes ``PROPOSED`` with the
request_ref once the surface recorded it. An ``IN_FLIGHT`` entry seen by a
later call means the outcome is unknown: that call is refused and nothing
is proposed again. The index holds no authority: it never approves,
dispatches or decides anything, and the surface's own records stay the
only truth about a request.

Cancel recovery (task d9e17d, optional per entry). The surface returns a
request's one-shot control capability exactly once, in the ``request``
reply; a reply lost in transit would leave the originating conversation
unable to withdraw its own pending proposal. So a ``PROPOSED`` entry may
carry ``recovery``: that capability SEALED under the request's origin,
written in the same atomic write that records ``PROPOSED``. The origin is
exactly what keys the entry: the request's text and the caller's
``conversation_ref``. The two are not equally private. The text is sent
to the Operator and may appear verbatim in the persisted proposal and in
``present`` output. The ``conversation_ref`` is never sent to the
Operator, never displayed or returned, and stored only inside the entry's
key digest. The protection therefore rests on the ``conversation_ref``
staying private and unguessable. The seal is a one-time pad,
HMAC-SHA256 under a key derived (domain-separated, so it is not the
stored key digest) from that origin, over the request_ref. The file
therefore holds no capability usable without the origin proof, and is only
as safe as that proof: with a weak (visible, sequential or guessable)
``conversation_ref``, anyone who also has the text can unseal it.
Recovery is kept only for a request that carried a ``conversation_ref``.
An entry written before recovery existed carries none, and nothing is
ever fabricated for it. No error message ever names an entry key.
"""

import hashlib
import hmac
import json
import os
import stat

from local_request import store as request_store
from workflow_authority.atomic import atomic_write_json, exclusive_store_lock
from workflow_authority.digest import DigestError, canonical_json_bytes, json_digest

SCHEMA_VERSION = 1
INDEX_FILE_NAME = "grok_bot_requests.json"
INDEX_LOCK_FILE_NAME = "grok_bot_requests.lock"
MAX_ENTRIES = request_store.MAX_REQUEST_RECORDS
STATE_IN_FLIGHT = "IN_FLIGHT"
STATE_PROPOSED = "PROPOSED"
ENTRY_KEYS = ("state", "request_ref", "recorded_at")
# Additive (task d9e17d): an entry written before it existed has none.
ENTRY_OPTIONAL_KEYS = ("recovery",)
RECOVERY_KEYS = ("seal", "sealed_capability")
SEAL_SCHEME = "hmac-sha256-xor-v1"
_SEAL_KEY_DOMAIN = b"grok_bot cancel-recovery seal key v1"
_SEAL_PAD_DOMAIN = b"grok_bot cancel-recovery pad v1\x00"
DOCUMENT_KEYS = ("schema_version", "requests", "presentations")
# Additive (slice 3): a document written before it existed has none.
DOCUMENT_OPTIONAL_KEYS = ("delivery_presentations",)
# Delivery presentation receipts, content-addressed by the proposal digest;
# beyond this many the oldest presentation is forgotten (it can then no
# longer be approved here: fail closed).
MAX_DELIVERY_RECEIPTS = 16
DELIVERY_RECEIPT_KEYS = ("proposal", "display_digest_sha256")
DELIVERY_PROPOSAL_KEYS = ("binding", "expires_at", "presented_at")
RECEIPT_KEYS = ("binding", "display_digest_sha256", "presented_at")
# Exactly the fields a reply must restate, as ``present`` displayed them.
DISPLAYED_BINDING_KEYS = (
    "request_ref", "mission_id", "revision", "proposal_digest_sha256",
    "approved_action_scope", "approved_delivery_targets", "expires_at",
)

PROBLEM_UNREADABLE = "grok_bot_index_unreadable"
PROBLEM_FULL = "grok_bot_index_full"
_HEX = frozenset("0123456789abcdef")


class RequestIndexError(Exception):
    def __init__(self, message, problem=PROBLEM_UNREADABLE):
        super(RequestIndexError, self).__init__(message)
        self.problem = problem


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and not set(value) - _HEX


def _strings(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


# -- cancel recovery: the capability sealed under the request's origin ----


def seal_key(origin):
    """The seal key for one origin (``{"text", "conversation_ref"}``).
    HMAC under a fixed domain label, so it is never the origin's plain
    SHA-256, which the index stores as the entry's key."""
    return hmac.new(_SEAL_KEY_DOMAIN, canonical_json_bytes(origin),
                    hashlib.sha256).digest()


def _pad(origin, request_ref):
    return hmac.new(seal_key(origin),
                    _SEAL_PAD_DOMAIN + request_ref.encode("ascii"),
                    hashlib.sha256).digest()


def seal_capability(origin, request_ref, token):
    """The ``recovery`` block for ``token``, or None when the token is not
    the surface's exact capability shape (nothing is kept then)."""
    if not isinstance(token, str) or not token.startswith(
        request_store.TOKEN_PREFIX
    ) or not _hex64(token[len(request_store.TOKEN_PREFIX):]):
        return None
    body = token[len(request_store.TOKEN_PREFIX):]
    sealed = bytes(a ^ b for a, b in zip(bytes.fromhex(body),
                                         _pad(origin, request_ref)))
    return {"seal": SEAL_SCHEME, "sealed_capability": sealed.hex()}


def unseal_capability(origin, request_ref, recovery):
    """The capability sealed in ``recovery``, given the SAME origin and
    request_ref; any other origin yields an unrelated value, which the
    surface's own capability check refuses."""
    opened = bytes(a ^ b for a, b in zip(
        bytes.fromhex(recovery["sealed_capability"]), _pad(origin, request_ref)))
    return request_store.TOKEN_PREFIX + opened.hex()


def _validate(document, where):
    def bad(message):
        raise RequestIndexError("%s %s" % (where, message))
    if not isinstance(document, dict) or sorted(
        k for k in document if k not in DOCUMENT_OPTIONAL_KEYS
    ) != sorted(DOCUMENT_KEYS) or document["schema_version"] != SCHEMA_VERSION:
        bad("must carry exactly %s (plus optional %s), schema_version %d"
            % (", ".join(DOCUMENT_KEYS), ", ".join(DOCUMENT_OPTIONAL_KEYS),
               SCHEMA_VERSION))
    deliveries = document.get("delivery_presentations", {})
    if not isinstance(deliveries, dict) or len(deliveries) > (
        MAX_DELIVERY_RECEIPTS
    ):
        bad("delivery_presentations must be a map of at most %d"
            % MAX_DELIVERY_RECEIPTS)
    for digest, receipt in deliveries.items():
        if not isinstance(receipt, dict) or sorted(receipt) != sorted(
            DELIVERY_RECEIPT_KEYS
        ) or not _hex64(receipt["display_digest_sha256"]):
            bad("delivery presentation %r is malformed" % (digest,))
        proposal = receipt["proposal"]
        try:
            matches = json_digest(proposal) == digest
        except DigestError:
            matches = False
        if not isinstance(proposal, dict) or sorted(proposal) != sorted(
            DELIVERY_PROPOSAL_KEYS
        ) or not matches:
            bad("delivery presentation %r is not the proposal its digest"
                " names" % (digest,))
    requests = document["requests"]
    if not isinstance(requests, dict) or len(requests) > MAX_ENTRIES:
        bad("requests must be a map of at most %d" % MAX_ENTRIES)
    # An entry key is the digest of a request's text AND conversation_ref,
    # the material cancel recovery rests on (task d9e17d): no message ever
    # names a key, only the entry's position in the file.
    for position, (key, entry) in enumerate(requests.items(), 1):
        if not _hex64(key):
            bad("request entry %d's key is not a sha256 digest" % position)
        if not isinstance(entry, dict) or sorted(
            k for k in entry if k not in ENTRY_OPTIONAL_KEYS
        ) != sorted(ENTRY_KEYS):
            bad("request entry %d must carry exactly %s (plus optional %s)"
                % (position, ", ".join(ENTRY_KEYS),
                   ", ".join(ENTRY_OPTIONAL_KEYS)))
        if entry["state"] == STATE_IN_FLIGHT:
            ok = entry["request_ref"] is None and "recovery" not in entry
        elif entry["state"] == STATE_PROPOSED:
            ok = request_store.request_ref_problem(entry["request_ref"]) is None
        else:
            ok = False
        if not ok or not _count(entry["recorded_at"]):
            bad("request entry %d is malformed" % position)
        if "recovery" in entry:
            recovery = entry["recovery"]
            if not isinstance(recovery, dict) or sorted(recovery) != sorted(
                RECOVERY_KEYS
            ) or recovery["seal"] != SEAL_SCHEME or not _hex64(
                recovery["sealed_capability"]
            ):
                bad("request entry %d carries malformed recovery material"
                    % position)
    presentations = document["presentations"]
    if not isinstance(presentations, dict) or len(presentations) > MAX_ENTRIES:
        bad("presentations must be a map of at most %d" % MAX_ENTRIES)
    for ref, receipt in presentations.items():
        if request_store.request_ref_problem(ref) is not None or not isinstance(
            receipt, dict
        ) or sorted(receipt) != sorted(RECEIPT_KEYS):
            bad("presentation %r must be an lr- key carrying exactly %s"
                % (ref, ", ".join(RECEIPT_KEYS)))
        binding = receipt["binding"]
        if not isinstance(binding, dict) or sorted(binding) != sorted(
            DISPLAYED_BINDING_KEYS
        ) or binding["request_ref"] != ref or not isinstance(
            binding["mission_id"], str
        ) or not _count(binding["revision"]) or not _hex64(
            binding["proposal_digest_sha256"]
        ) or not _strings(binding["approved_action_scope"]) or not _strings(
            binding["approved_delivery_targets"]
        ) or not _count(binding["expires_at"]) or not _hex64(
            receipt["display_digest_sha256"]
        ) or not _count(receipt["presented_at"]):
            bad("presentation %s is malformed" % ref)
    return document


class RequestIndex(object):

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, INDEX_FILE_NAME)

    def _refuse_open(self, path):
        try:
            mode = os.stat(path).st_mode
        except FileNotFoundError:
            return
        if mode & 0o077:
            raise RequestIndexError(
                "%s is accessible by group/other (mode %o); nothing is read"
                " or written" % (path, stat.S_IMODE(mode)))

    def load(self):
        self._refuse_open(self.directory)
        if not os.path.exists(self.path):
            return {"schema_version": SCHEMA_VERSION, "requests": {},
                    "presentations": {}}
        self._refuse_open(self.path)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RequestIndexError("%s could not be read (%s)" % (self.path, exc))
        return _validate(document, self.path)

    def _save(self, document):
        _validate(document, self.path)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".grok-bot-requests-")

    def _locked(self):
        self._refuse_open(self.directory)
        return exclusive_store_lock(self.directory, INDEX_LOCK_FILE_NAME)

    def begin(self, key, now):
        """The existing entry as ``(state, request_ref)``, or
        ``(None, None)`` after durably marking this key IN_FLIGHT."""
        with self._locked():
            document = self.load()
            entry = document["requests"].get(key)
            if entry is not None:
                return entry["state"], entry["request_ref"]
            if len(document["requests"]) >= MAX_ENTRIES:
                raise RequestIndexError(
                    "%d requests are indexed; the hard bound is %d"
                    % (MAX_ENTRIES, MAX_ENTRIES), PROBLEM_FULL)
            document["requests"][key] = {"state": STATE_IN_FLIGHT,
                                         "request_ref": None, "recorded_at": now}
            self._save(document)
        return None, None

    def abandon(self, key):
        """The turn provably proposed nothing: forget the IN_FLIGHT mark."""
        with self._locked():
            document = self.load()
            entry = document["requests"].get(key)
            if entry is not None and entry["state"] == STATE_IN_FLIGHT:
                del document["requests"][key]
                self._save(document)

    def record_proposed(self, key, request_ref, now, recovery=None):
        """PROPOSED, and the sealed cancel recovery (if any) in the SAME
        atomic write: the reply is returned only after both are durable."""
        with self._locked():
            document = self.load()
            entry = {"state": STATE_PROPOSED, "request_ref": request_ref,
                     "recorded_at": now}
            if recovery is not None:
                entry["recovery"] = dict(recovery)
            document["requests"][key] = entry
            self._save(document)

    def entry(self, key):
        """A copy of the entry for one request key, or None."""
        found = self.load()["requests"].get(key)
        return None if found is None else json.loads(json.dumps(found))

    def indexes_request(self, request_ref):
        """Whether any PROPOSED entry names ``request_ref``."""
        return any(entry["state"] == STATE_PROPOSED
                   and entry["request_ref"] == request_ref
                   for entry in self.load()["requests"].values())

    def serialized(self):
        """The one cross-process critical section over receipts. A
        presentation (the surface read AND its receipt write) and an
        approval (the receipt comparison AND the surface's application)
        each run wholly inside it, so a presentation can never replace a
        receipt between an approval's check and its application; a cancel
        recovery reads its entry and applies the surface's cancel inside it
        too. Not reentrant: ``begin``, ``abandon`` and ``record_proposed``
        take it themselves and are never called inside it."""
        return self._locked()

    def record_presentation(self, binding, display_digest, now):
        """Durably record what is about to be displayed for one request.
        The caller holds ``serialized()``."""
        document = self.load()
        ref = binding["request_ref"]
        if ref not in document["presentations"] and len(
            document["presentations"]
        ) >= MAX_ENTRIES:
            raise RequestIndexError(
                "%d presentations are held; the hard bound is %d"
                % (MAX_ENTRIES, MAX_ENTRIES), PROBLEM_FULL)
        document["presentations"][ref] = {
            "binding": json.loads(json.dumps(binding)),
            "display_digest_sha256": display_digest, "presented_at": now}
        self._save(document)

    def presentation(self, request_ref):
        """The receipt of the latest display for ``request_ref``, or None.
        An approval reads it inside ``serialized()``."""
        return self.load()["presentations"].get(request_ref)

    def record_delivery_presentation(self, digest, proposal, display_digest):
        """Durably record the delivery proposal about to be displayed, keyed
        by its own digest. The caller holds ``serialized()``. Beyond the
        bound the OLDEST presentation is forgotten."""
        document = self.load()
        receipts = document.setdefault("delivery_presentations", {})
        receipts[digest] = {"proposal": json.loads(json.dumps(proposal)),
                            "display_digest_sha256": display_digest}
        while len(receipts) > MAX_DELIVERY_RECEIPTS:
            oldest = min(receipts, key=lambda d: (
                receipts[d]["proposal"]["presented_at"], d))
            del receipts[oldest]
        self._save(document)

    def delivery_presentation(self, digest):
        """The receipt of the delivery proposal displayed under ``digest``,
        or None. An approval reads it inside ``serialized()``."""
        if not isinstance(digest, str):
            return None
        return self.load().get("delivery_presentations", {}).get(digest)
