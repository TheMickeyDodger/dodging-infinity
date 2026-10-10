"""The Grok Bot transport adapter: one method per tool, each a thin call
into an existing DI seam. It decides nothing.

- ``request``: the human's plain text goes through the operator session
  seam (``prepare`` then ``execute``, source ``grok_bot``); the Operator
  authors the Mission proposal (``grok_bot.framing``) and the local
  request surface's ``submit`` records it unchanged. A repeated request
  returns its first request (``grok_bot.index``).
- ``present``: the surface's exact presentation, plus the binding to
  relay (exactly the presented values, with the surface's own
  ``latest_expires_at`` as the expiry the human sees) and the complete
  rendering of it and of the whole recorded proposal. What is displayed
  is durably recorded first, as a presentation receipt.
- ``approve``: compare, then refuse. The caller's binding must equal,
  field by field and type-exactly, the one last displayed for its
  request (``grok_bot_not_presented`` / ``grok_bot_binding_not_displayed``
  otherwise, naming the fields). Nothing is filled in or corrected from
  the receipt: when it matches, the caller's own values and relayed reply
  go to the surface's ``attest_approval`` UNMODIFIED, and the surface
  refuses a missing, stale, expired or non-affirmative one with its own
  existing code. The receipt is an additional, narrower gate; the
  surface's own checks are unchanged.
- ``status``, ``recover``, ``cancel``, ``run``: the surface's own
  operations; ``run`` reaches the run bridge only through the surface
  (``LocalRequestSurface.run_command``). ``run`` ``dispatch`` takes no
  path: the bridge prepares the Mission's own isolated workspace. Its one
  optional argument, ``workspace_path``, is the operator recovery entry
  point: it may only re-name that same, already bound workspace, and the
  bridge applies every same check to it.
- ``cancel`` after a LOST ``request`` reply (task d9e17d): instead of the
  control capability, the originating conversation restates its origin
  (the request's exact ``text`` and ``conversation_ref``). The adapter
  unseals the capability it sealed under that origin (``grok_bot.index``)
  and hands it to the surface's own ``cancel``, whose preconditions and
  refusal codes are unchanged: an added gate, never a removed one. A proof
  binds exactly one request_ref; a request_ref or Mission id alone is never
  enough. Interrupted local persistence
  (``grok_bot_cancel_recovery_interrupted``) and a record with no
  recoverable capability (``grok_bot_cancel_recovery_unavailable``) are
  refused as what they are;
  no capability is ever fabricated, and none is ever returned except in
  the ``request`` reply itself.

- ``present_delivery``, ``approve_delivery``, ``delivery_status`` (slice
  3): the SEPARATE delivery ceremony, relayed to pr_delivery's own
  ``present-dots`` and ``attest-dots`` (``grok_bot.delivery``), with the
  same complete display and compare-then-refuse receipt discipline as a
  Mission proposal. pr_delivery's ceremony alone records a delivery
  authorization; nothing here performs a step of it.

Every result is the surface's (or the ceremony's) result, labelled with
``transport`` (``grok_bot``, a transport only), ``delivery_authority``
("none": what THIS transport grants, always nothing) and the relay's
``evidence_status`` (``operator_attestation_only_not_sender_evidence``).
The adapter cannot grant delivery authority itself: it never mints, never
performs a step, and never constructs a delivery transport DIRECTLY
(construction is core-owned, by pr_delivery's own ``build_machine()``; see
``grok_bot.delivery``). It has no merge, release, deploy or step-performing
tool, and an engineering approval stays an engineering approval (it never
reaches the delivery ceremony).
"""

import json

from codex_gateway import contract as gateway_contract
from local_request import store as request_store
from local_request import surface as surface_module
from workflow_authority.digest import json_digest, text_digest

from grok_bot import framing
from grok_bot import index as index_module

TRANSPORT = framing.TRANSPORT
DELIVERY_AUTHORITY = surface_module.DELIVERY_AUTHORITY
EVIDENCE_STATUS = request_store.EVIDENCE_STATUS

PROBLEM_BAD_REQUEST = "grok_bot_bad_request"
PROBLEM_UNKNOWN_TOOL = "grok_bot_unknown_tool"
PROBLEM_UNKNOWN_FIELD = "grok_bot_unknown_field"
PROBLEM_OPERATOR_FAILED = "grok_bot_operator_failed"
PROBLEM_IN_FLIGHT = "grok_bot_request_in_flight"
PROBLEM_NOT_PRESENTED = "grok_bot_not_presented"
PROBLEM_NOT_DISPLAYED = "grok_bot_binding_not_displayed"
# Cancel recovery after a lost request reply (task d9e17d).
PROBLEM_ORIGIN_MISMATCH = "grok_bot_cancel_origin_mismatch"
PROBLEM_RECOVERY_INTERRUPTED = "grok_bot_cancel_recovery_interrupted"
PROBLEM_RECOVERY_UNAVAILABLE = "grok_bot_cancel_recovery_unavailable"
MAX_REF_CHARS = 128

RECOVERY_KEPT = (
    "kept sealed under this request's exact text and conversation_ref: if"
    " this reply is lost, cancel with the request_ref, that text and that"
    " conversation_ref instead of the capability (repeat the identical"
    " request to read the request_ref again)")
RECOVERY_NOT_KEPT = (
    "not kept: no recovery material is held for this request (it is kept"
    " only for a request that carries a conversation_ref), so this"
    " capability is the only way to cancel this pending proposal")

LIMITATION = (
    "Approval through Grok Bot is an operator-attested relay of a separate"
    " plain-text reply. It is not cryptographically authenticated: Grok Bot"
    " gives DI no signed sender attribution, so DI does not establish who"
    " sent the reply.")

APPROVAL_FIELDS = (
    "request_ref", "mission_id", "revision", "proposal_digest_sha256",
    "approved_action_scope", "approved_delivery_targets", "expires_at",
    "relayed_reply", "relay_ref",
)
# The arguments each run command's bridge operation takes, exactly, with
# the type each must have (None: any value; the bridge validates it).
RUN_ARGUMENTS = {"dispatch": {},
                 "verify": {"reported_result": None},
                 "prove": {"operation": str, "arguments": dict}}
# The OPTIONAL arguments, typed the same way (Task 8 final). An ordinary
# dispatch takes none: DI prepares the Mission's own isolated workspace.
# ``workspace_path`` is OPERATOR RECOVERY only: it may name nothing but that
# same, already bound workspace, under every same check; never needed
# otherwise, and never a way to bind another path.
RUN_OPTIONAL_ARGUMENTS = {"dispatch": {"workspace_path": str}}
# Slice 3, the SEPARATE delivery ceremony: exactly pr_delivery present-dots'
# own arguments (pinned by test against its parser), as
# (name, kind, required, default when absent).
DELIVERY_PRESENT_FIELDS = (
    ("repo", "path", True, None),
    ("workflow_id", "text", True, None),
    ("herd_evidence", "path", True, None),
    ("verification_log", "path", True, None),
    ("verification_command", "text", True, None),
    ("verification_exit_status", "integer", True, None),
    ("verification_ran_at", "number", False, None),
    ("reverify_command", "text", False, None),
    ("title", "text", True, None),
    ("objective", "text", False, ""),
    ("architecture_notes", "text", False, ""),
    ("nonblocking_risks", "text", False, ""),
    ("base_branch", "text", False, "main"),
    ("remote", "text", False, "origin"),
    ("validity_seconds", "integer", False, None),
    ("mission_workflow_id", "text", False, None),
    ("mission_authorization_digest", "text", False, None),
    # Both or neither (present-dots refuses one alone): an EXISTING open
    # pull request, which selects pr_delivery's ``pr_update`` kind.
    ("pr_number", "integer", False, None),
    ("head_branch", "text", False, None),
)
# The delivery binding a reply must restate (as displayed) and the relay.
DELIVERY_APPROVAL_FIELDS = ("proposal_digest_sha256", "expires_at",
                            "relayed_reply", "reply_to", "relay_ref")
TOOLS = {
    "request": ("text", "conversation_ref", "operator_session_id"),
    "present": ("request_ref",),
    "approve": APPROVAL_FIELDS,
    "status": ("request_ref",),
    "recover": ("request_ref",),
    "cancel": ("request_ref", "control_capability", "text", "conversation_ref"),
    "run": ("request_ref", "command", "arguments"),
    "present_delivery": tuple(field[0] for field in DELIVERY_PRESENT_FIELDS),
    "approve_delivery": DELIVERY_APPROVAL_FIELDS,
    "delivery_status": ("delivery_id",),
}


def _refuse(problem, reason, **details):
    raise surface_module.LocalRequestRefusal(problem, reason, **details)


def _label(result):
    labelled = dict(result)
    labelled.setdefault("delivery_authority", DELIVERY_AUTHORITY)
    labelled.setdefault("evidence_status", EVIDENCE_STATUS)
    labelled["transport"] = TRANSPORT
    return labelled


def refused(problem, reason):
    """A labelled refusal for a caller that never reached a tool."""
    return _label(surface_module.LocalRequestRefusal(problem, reason).as_dict())


# Every code point ``str.splitlines`` treats as a line boundary (pinned by
# test against all of Unicode). JSON escapes those below U+0020 itself; with
# ``ensure_ascii=False`` it would leave U+0085, U+2028 and U+2029 raw.
LINE_BOUNDARIES = "\n\r\x0b\x0c\x1c\x1d\x1e\x85  "


def _escaped(char):
    return char in LINE_BOUNDARIES or "\ud800" <= char <= "\udfff"


def one_line(text):
    """``text`` with every line boundary AND every lone surrogate written
    as its ``\\uXXXX`` escape. Nothing is dropped: inside a JSON value each
    escape decodes back to the exact original code point. Boundaries could
    forge lines; a surrogate (which Mission Core accepts from an escaped
    ``\\ud800`` in JSON) has no UTF-8 form, so left raw it would make an
    accepted proposal impossible to display or digest. The result is one
    line of valid UTF-8."""
    if not any(_escaped(char) for char in text):
        return text
    return "".join("\\u%04x" % ord(char) if _escaped(char) else char
                   for char in text)


def rendered_lines(prefix, value):
    """Every leaf of ``value`` as one ``path: json`` line, empty containers
    included, keys sorted. Generic over the record, so a field Mission Core
    adds is displayed without a change here. Values are JSON-encoded and
    every line boundary is escaped, so no field can start a line of its
    own."""
    if isinstance(value, dict):
        if not value:
            return [one_line("%s: {}" % prefix)]
        return [line for key in sorted(value)
                for line in rendered_lines("%s.%s" % (prefix, key), value[key])]
    if isinstance(value, list):
        if not value:
            return [one_line("%s: []" % prefix)]
        return [line for index, item in enumerate(value)
                for line in rendered_lines("%s[%d]" % (prefix, index), item)]
    return [one_line("%s: %s" % (prefix, json.dumps(value, ensure_ascii=False)))]


def display_text(presented, binding):
    """The human-facing text: the binding a reply must match, then the
    COMPLETE recorded proposal (its proof_contract lines are the
    constraints, evidence requirements and budget). Never shortened, and
    every line is one line: ``splitlines`` finds exactly the joins."""
    return "\n".join(one_line(line) for line in [
        "DODGING INFINITY MISSION PROPOSAL",
        "Mission: %s" % binding["mission_id"],
        "Revision: %d" % binding["revision"],
        "Proposal digest (sha256): %s" % binding["proposal_digest_sha256"],
        "Approved action scope: %s" % json.dumps(binding["approved_action_scope"]),
        "Approved delivery targets: %s" % json.dumps(
            binding["approved_delivery_targets"]),
        "Approval expires at (unix seconds): %d" % binding["expires_at"],
        "",
        "The complete proposal, every field exactly as recorded (nothing is"
        " omitted; the proof_contract lines are its constraints, evidence"
        " requirements and budget):",
    ] + rendered_lines("proposal", presented["proposal"]) + [
        "",
        "To approve exactly this proposal, reply with a separate message"
        " containing only: approved",
        "This approves engineering only: no commit, push, pull request,"
        " merge, release or deploy authority (delivery authority: none).",
        "Approval is operator-attested, not cryptographically authenticated.",
    ])


def _same_as_shown(given, shown):
    """Type-exact, order-exact equality with a displayed value, which is a
    string, an integer or a list of strings (the receipt's validated
    shapes). Never recursive and never hashed: a nested, non-finite or
    non-JSON value is simply not equal."""
    if type(given) is not type(shown):
        return False
    if type(shown) is list:
        return len(given) == len(shown) and all(
            type(item) is str and item == expected
            for item, expected in zip(given, shown))
    return given == shown


def displayed_mismatch(displayed, caller, keys=index_module.DISPLAYED_BINDING_KEYS):
    """The fields the caller supplied that differ from what was displayed.
    An absent field is not compared: the surface refuses it with its own
    existing code (a delivery approval refuses it before comparing)."""
    return sorted(name for name in keys
                  if caller.get(name) is not None
                  and not _same_as_shown(caller[name], displayed[name]))


class GrokBotAdapter(object):

    def __init__(self, surface, operator_session, repository, index, clock):
        self._surface = surface
        self._session = operator_session
        self._repository = repository
        self._index = index
        self._clock = clock

    def call(self, tool, arguments):
        """One tool call: a closed tool name and a closed argument set."""
        def checked():
            if tool not in TOOLS:
                _refuse(PROBLEM_UNKNOWN_TOOL, "unknown tool %r; the tools are %s"
                        % (tool, ", ".join(sorted(TOOLS))))
            if not isinstance(arguments, dict):
                _refuse(PROBLEM_BAD_REQUEST, "tool arguments must be an object")
            unknown = sorted(k for k in arguments if k not in TOOLS[tool])
            if unknown:
                _refuse(PROBLEM_UNKNOWN_FIELD, "unknown argument(s) %s for %s"
                        % (", ".join(repr(k) for k in unknown[:8]), tool))
            # Shape first: nothing below hashes, compares or hands on a
            # value nested past the bound.
            if framing.nesting_exceeds(arguments, framing.MAX_NESTING_DEPTH):
                _refuse(PROBLEM_BAD_REQUEST,
                        "tool arguments nest lists or objects more than %d"
                        " deep" % framing.MAX_NESTING_DEPTH)
            reference = arguments.get("request_ref")
            if reference is not None and not isinstance(reference, str):
                _refuse(PROBLEM_BAD_REQUEST,
                        "request_ref must be null or a string")
            return getattr(self, "_" + tool)(**arguments)
        return self._guarded(checked)

    def _guarded(self, operation):
        try:
            result = operation()
        except surface_module.LocalRequestRefusal as refusal:
            result = refusal.as_dict()
        except (request_store.LocalRequestStoreError,
                index_module.RequestIndexError) as exc:
            result = {"ok": False, "status": "store_error",
                      "problem": exc.problem, "reason": str(exc)}
        except surface_module.MISSION_CORE_ERRORS as exc:
            result = {"ok": False, "status": "refused",
                      "problem": getattr(exc, "problem", None),
                      "reason": str(exc)}
        return _label(result)

    # -- the tools: keyword arguments, absent means None ------------------

    def request(self, **arguments):
        return self.call("request", arguments)

    def present(self, **arguments):
        return self.call("present", arguments)

    def approve(self, **arguments):
        return self.call("approve", arguments)

    def status(self, **arguments):
        return self.call("status", arguments)

    def recover(self, **arguments):
        return self.call("recover", arguments)

    def cancel(self, **arguments):
        return self.call("cancel", arguments)

    def run(self, **arguments):
        return self.call("run", arguments)

    def present_delivery(self, **arguments):
        return self.call("present_delivery", arguments)

    def approve_delivery(self, **arguments):
        return self.call("approve_delivery", arguments)

    def delivery_status(self, **arguments):
        return self.call("delivery_status", arguments)

    # -- implementations ---------------------------------------------------

    @staticmethod
    def _origin(text, conversation_ref):
        """A request's origin: exactly what keys it in the index."""
        if not isinstance(text, str) or not text.strip() or len(text) > (
            framing.MAX_TEXT_CHARS
        ):
            _refuse(PROBLEM_BAD_REQUEST,
                    "text must be non-empty plain text of at most %d characters;"
                    " it is never truncated" % framing.MAX_TEXT_CHARS)
        if conversation_ref is not None and (not isinstance(conversation_ref, str)
                                             or len(conversation_ref) > MAX_REF_CHARS):
            _refuse(PROBLEM_BAD_REQUEST, "conversation_ref must be null or a"
                    " string of at most %d characters" % MAX_REF_CHARS)
        return {"text": text, "conversation_ref": conversation_ref}

    def _request(self, text=None, conversation_ref=None, operator_session_id=None):
        origin = self._origin(text, conversation_ref)
        if operator_session_id is not None and (
            not isinstance(operator_session_id, str)
            or len(operator_session_id) > MAX_REF_CHARS
        ):
            _refuse(PROBLEM_BAD_REQUEST, "operator_session_id must be null or a"
                    " string of at most %d characters" % MAX_REF_CHARS)
        key = json_digest(origin)
        state, request_ref = self._index.begin(key, self._clock())
        if state == index_module.STATE_PROPOSED:
            result = self._surface.status(request_ref)
            result.update(duplicate=True, request_ref=request_ref)
            return result
        if state is not None:
            _refuse(PROBLEM_IN_FLIGHT,
                    "an identical request is in flight or its outcome is unknown;"
                    " nothing was proposed again. Read the pending proposal"
                    " with present, or change the request text to ask anew")
        framed, neutralized = framing.operator_text(text)
        try:
            prepared = self._session.prepare(
                framed, self._repository, session_id=operator_session_id,
                source=TRANSPORT)
            outcome = self._session.execute(prepared)
        except Exception as exc:
            self._index.abandon(key)
            _refuse(PROBLEM_OPERATOR_FAILED, "the Operator turn raised %s;"
                    " nothing was proposed" % type(exc).__name__)
        if outcome.status != gateway_contract.STATUS_COMPLETED:
            self._index.abandon(key)
            _refuse(PROBLEM_OPERATOR_FAILED, "the Operator turn ended %r;"
                    " nothing was proposed" % (outcome.status,),
                    operator_status=outcome.status)
        proposal, problem = framing.parse_proposal(outcome.message)
        reply = {"operator_message": outcome.message,
                 "operator_session_id": outcome.session_id,
                 "neutralized": neutralized, "duplicate": False}
        if problem is not None:
            self._index.abandon(key)
            _refuse(problem, "the Operator's proposal envelope is malformed;"
                    " nothing was proposed", **reply)
        if proposal is None:
            self._index.abandon(key)
            reply.update(ok=True, status="operator_reply", proposal=None)
            return reply
        try:
            result = self._surface.submit(proposal)
        except surface_module.LocalRequestRefusal:
            self._index.abandon(key)
            raise
        # Recovery needs an origin no reader of the proposal can restate:
        # never kept for a request without a conversation_ref.
        recovery = index_module.seal_capability(
            origin, result["request_ref"], result.get("control_capability")
        ) if conversation_ref else None
        self._index.record_proposed(key, result["request_ref"], self._clock(),
                                    recovery)
        result.update(reply)
        result["control_capability_recovery"] = (
            RECOVERY_KEPT if recovery is not None else RECOVERY_NOT_KEPT)
        return result

    def _present(self, request_ref=None):
        # One critical section with every approval: the surface read and
        # the receipt write land together (see RequestIndex.serialized).
        with self._index.serialized():
            presented = self._surface.present(request_ref)
            binding = {
                "request_ref": presented["request_ref"],
                "mission_id": presented["mission_id"],
                "revision": presented["revision"],
                "proposal_digest_sha256": presented["proposal_digest_sha256"],
                "approved_action_scope": list(presented["approved_action_scope"]),
                "approved_delivery_targets": list(
                    presented["approved_delivery_targets"]),
                "expires_at": presented["latest_expires_at"],
            }
            text = display_text(presented, binding)
            # Recorded BEFORE it is returned: nothing is displayed unrecorded.
            self._index.record_presentation(binding, text_digest(text),
                                            self._clock())
        presented.update(approval_binding=binding, display_text=text,
                         display_digest_sha256=text_digest(text),
                         limitation=LIMITATION)
        return presented

    def _approve(self, **binding):
        """Compare, then refuse: the caller's binding must equal the one
        last displayed for its request, field by field. Nothing is ever
        filled in or corrected from the receipt; when it matches, the
        caller's own values go to the surface, which checks them again.
        The comparison and the surface's application run in ONE critical
        section, so no presentation can replace the receipt between them."""
        fields = dict((name, binding.get(name)) for name in APPROVAL_FIELDS)
        request_ref = fields["request_ref"]
        if request_ref is None:
            # The surface refuses an absent reference with its own code.
            return self._surface.attest_approval(**fields)
        with self._index.serialized():
            receipt = self._index.presentation(request_ref)
            if receipt is None:
                _refuse(PROBLEM_NOT_PRESENTED,
                        "nothing was displayed for request %r through this"
                        " adapter; present it, show the human that exact text,"
                        " then relay the reply. Nothing was recorded"
                        % (request_ref,))
            differing = displayed_mismatch(receipt["binding"], binding)
            if differing:
                _refuse(PROBLEM_NOT_DISPLAYED,
                        "the relayed binding differs from the binding last"
                        " displayed for request %s in %s; nothing was recorded"
                        " and nothing was corrected" % (
                            request_ref, ", ".join(differing)),
                        fields=differing)
            return self._surface.attest_approval(**fields)

    def _status(self, request_ref=None):
        return self._surface.status(request_ref)

    def _recover(self, request_ref=None):
        return self._surface.recover(request_ref)

    def _cancel(self, request_ref=None, control_capability=None, text=None,
                conversation_ref=None):
        if text is None and conversation_ref is None:
            return self._surface.cancel(request_ref, control_capability)
        if control_capability is not None:
            _refuse(PROBLEM_BAD_REQUEST,
                    "cancel takes either the control_capability, or the original"
                    " request's text and conversation_ref (recovery after a lost"
                    " reply), never both; nothing was cancelled")
        return self._recover_cancel(request_ref, self._origin(text, conversation_ref))

    def _recover_cancel(self, request_ref, origin):
        """Cancel through the capability sealed under ``origin``. Every
        surface check still applies: the unsealed capability goes to
        ``LocalRequestSurface.cancel`` exactly like a caller's own."""
        with self._index.serialized():
            entry = self._index.entry(json_digest(origin))
            if entry is not None and entry["state"] == index_module.STATE_IN_FLIGHT:
                _refuse(PROBLEM_RECOVERY_INTERRUPTED,
                        "the original request's local record was interrupted: its"
                        " index entry is still IN_FLIGHT (the process stopped, or a"
                        " write failed, between proposing and recording the"
                        " proposal), so no recovery material was kept and none is"
                        " fabricated; nothing was cancelled. status and present"
                        " read what the surface holds and recover binds an"
                        " interrupted proposal; only the control capability its"
                        " request reply carried can cancel it",
                        condition="in_flight", request_ref=request_ref)
            if entry is None and not self._index.indexes_request(request_ref):
                self._surface.status(request_ref)  # the surface's own refusal
                _refuse(PROBLEM_RECOVERY_INTERRUPTED,
                        "request %s is held by the local request surface, but this"
                        " adapter's index holds no entry naming it (it was not"
                        " created through this adapter, or its index entry was"
                        " never recorded), so no recovery material exists and"
                        " none is fabricated; nothing was cancelled. Only the"
                        " control capability its creation reply carried can"
                        " cancel it" % request_ref,
                        condition="not_indexed", request_ref=request_ref)
            if entry is None or entry["request_ref"] != request_ref:
                _refuse(PROBLEM_ORIGIN_MISMATCH,
                        "the origin proof (the exact request text and"
                        " conversation_ref) does not name request %s; nothing was"
                        " cancelled. A proof binds exactly the one request it"
                        " created, and knowing a request_ref or a Mission id never"
                        " permits control" % (request_ref,),
                        request_ref=request_ref)
            status = self._surface.status(request_ref)
            if status["surface_state"] == request_store.STATE_CANCELLED:
                _refuse(surface_module.PROBLEM_CANCELLED,
                        surface_module.CANCELLED_REASON, request_ref=request_ref)
            incomplete = status.get("local_cancellation") == "INCOMPLETE"
            recovery = entry.get("recovery")
            if recovery is None and incomplete:
                _refuse(PROBLEM_RECOVERY_INTERRUPTED,
                        "local cancellation of request %s is INCOMPLETE: Mission"
                        " Core already carries its withdrawal marker (every"
                        " decision on it is refused) but the local save did not"
                        " land. This adapter holds no recovery material for it;"
                        " the surface's remedy is to run cancel again with the"
                        " same control capability" % request_ref,
                        condition="local_cancellation_incomplete",
                        request_ref=request_ref)
            if recovery is None:
                why = ("it carried no conversation_ref, which recovery requires"
                       " (the exact text alone may be restated from the proposal"
                       " anyone can read)" if not origin["conversation_ref"] else
                       "it was recorded before this adapter kept any")
                # The Mission's state as Mission Core reports it NOW, never
                # assumed (the status read above).
                mission_state = (status.get("run") or {}).get("lifecycle_state")
                if mission_state is None:
                    state = ("its Mission's current state could not be read"
                             " from Mission Core")
                elif mission_state == (
                    surface_module.mission_record.STATE_AWAITING_DECISION
                ):
                    state = ("its Mission is still AWAITING_DECISION, and"
                             " without the capability its original request"
                             " reply carried it cannot be cancelled through"
                             " this transport")
                else:
                    state = "its Mission is %s" % mission_state
                _refuse(PROBLEM_RECOVERY_UNAVAILABLE,
                        "request %s holds no recovery material: %s. Its control"
                        " capability cannot be recovered and is never"
                        " fabricated, and nothing was cancelled; %s"
                        % (request_ref, why, state),
                        request_ref=request_ref, mission_state=mission_state)
            result = self._surface.cancel(request_ref, index_module.unseal_capability(
                origin, request_ref, recovery))
        result.update(cancelled_through="origin_recovery",
                      completed_interrupted_cancellation=incomplete)
        return result

    # -- the SEPARATE delivery ceremony (slice 3) --------------------------
    #
    # pr_delivery is imported lazily, by these three alone (grok_bot.delivery
    # is the one module allowed to import it), so no other tool, and no
    # startup, loads the delivery package.

    def _present_delivery(self, **arguments):
        from grok_bot import delivery as delivery_module
        args = delivery_module.ceremony_arguments(arguments)
        # One critical section with every delivery approval, exactly as for
        # Mission presentations: the ceremony's read and the receipt write
        # land together.
        with self._index.serialized():
            document = delivery_module.present(args)
            proposal = document["delivery_proposal"]
            digest = document["proposal_digest_sha256"]
            text = delivery_module.display_text(document)
            # Recorded BEFORE it is returned: nothing is displayed unrecorded.
            self._index.record_delivery_presentation(digest, proposal,
                                                     text_digest(text))
        return {
            "ok": True, "status": "delivery_presented",
            "proposal_digest_sha256": digest, "delivery_proposal": proposal,
            "approval_binding": {"proposal_digest_sha256": digest,
                                 "expires_at": proposal["expires_at"]},
            "display_text": text, "display_digest_sha256": text_digest(text),
            "reply": document["reply"], "source": document["source"],
            "residual_risk": document["residual_risk"], "limitation": LIMITATION,
        }

    def _approve_delivery(self, proposal_digest_sha256=None, expires_at=None,
                          relayed_reply=None, reply_to=None, relay_ref=None):
        """Compare, then refuse, then relay. The digest the human's reply
        answers names the presented proposal: the receipt is content-
        addressed (its key is that proposal's own digest), so the proposal
        handed to the ceremony is byte-for-byte the one displayed under it,
        never a different or current one. A displayed field that differs is
        refused by name and never corrected."""
        from grok_bot import delivery as delivery_module
        # The WHOLE displayed binding is restated, or nothing is relayed.
        for name, value in (("proposal_digest_sha256", proposal_digest_sha256),
                            ("expires_at", expires_at)):
            if value is None:
                _refuse(PROBLEM_BAD_REQUEST, "approve_delivery needs %s, exactly"
                        " as displayed" % name)
        for name, value in (("relayed_reply", relayed_reply),
                            ("reply_to", reply_to), ("relay_ref", relay_ref)):
            if value is not None and not isinstance(value, str):
                _refuse(PROBLEM_BAD_REQUEST, "%s must be null or a string" % name)
        digest = proposal_digest_sha256
        if not isinstance(digest, str):
            _refuse(PROBLEM_BAD_REQUEST, "proposal_digest_sha256 must be a string")
        with self._index.serialized():
            receipt = self._index.delivery_presentation(digest)
            if receipt is None:
                _refuse(PROBLEM_NOT_PRESENTED,
                        "no delivery proposal %r was displayed through this"
                        " adapter; present it, show the human that exact text,"
                        " then relay the reply. Nothing was recorded" % (digest,))
            differing = displayed_mismatch(
                {"expires_at": receipt["proposal"]["expires_at"]},
                {"expires_at": expires_at}, ("expires_at",))
            if differing:
                _refuse(PROBLEM_NOT_DISPLAYED,
                        "the relayed delivery binding differs from the one"
                        " displayed in %s; nothing was recorded and nothing was"
                        " corrected" % ", ".join(differing), fields=differing)
            delivery_id = delivery_module.attest(
                receipt["proposal"], digest, relayed_reply, reply_to, relay_ref)
        return {
            "ok": True, "status": "delivery_authorized_by_operator_attestation",
            "delivery_id": delivery_id, "proposal_digest_sha256": digest,
            "delivery_authorization": delivery_module.grant(
                delivery_id, receipt["proposal"]),
        }

    def _delivery_status(self, delivery_id=None):
        from grok_bot import delivery as delivery_module
        if not isinstance(delivery_id, str):
            _refuse(PROBLEM_BAD_REQUEST, "delivery_id must be a string")
        return {"ok": True, "status": "delivery_status", "delivery_id": delivery_id,
                "delivery": delivery_module.status(delivery_id)}

    def _run(self, request_ref=None, command=None, arguments=None):
        if command is not None and not isinstance(command, str):
            _refuse(PROBLEM_BAD_REQUEST, "command must be null or a string")
        arguments = {} if arguments is None else arguments
        expected = RUN_ARGUMENTS.get(command, {})
        optional = RUN_OPTIONAL_ARGUMENTS.get(command, {})
        if not isinstance(arguments, dict) or not set(expected) <= set(
            arguments
        ) or set(arguments) - set(expected) - set(optional):
            _refuse(PROBLEM_BAD_REQUEST, "run %r takes exactly the arguments %s"
                    "%s" % (command, sorted(expected), (
                        " and optionally %s" % sorted(optional)
                        if optional else "")))
        kinds = dict(expected)
        kinds.update((name, optional[name]) for name in arguments
                     if name in optional)
        for name, kind in sorted(kinds.items()):
            if kind is not None and not isinstance(arguments[name], kind):
                _refuse(PROBLEM_BAD_REQUEST, "run %s argument %r must be a %s"
                        % (command, name, kind.__name__))
        return self._surface.run_command(request_ref, command, **arguments)
