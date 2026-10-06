"""The Runtime's OWN observation receipts of a Mission-origin workflow
(Task 8, slice S-V, ledger R2-11-a / R2-11-b): their closed vocabulary,
how they are written and how they are read back — ONE module for the
writer (``target_runtime.broker``) and the reader
(``mission_control.reconciliation_bridge``), so neither re-implements
the other's format. Pure: no store, no transport, no process.

Three receipt families, each one ``evidence`` receipt in the workflow
record (bounded summary + digest). In EVERY family the receipt's
``digest`` is a BINDING over every semantic field its summary states
(R15-4): a canonical digest of the marker and the fields, so any
alteration of a field — even to another validly shaped value — breaks
the receipt's consistency and it is refused (``consistent`` False),
never read as an observation. A receipt is consistent only when its
summary is ALSO exactly the canonical rendering of its parsed fields
(R17 self-audit: no padded, duplicated, reordered or extra tokens), and
every number is a canonical ASCII decimal (R17-2: nothing that merely
looks like a digit, nothing that can raise). A receipt is recognised as
a member of a family by its summary marker OR its writer's turn-id
prefix, so editing one of the two turns it into a TAMPERED member of
the family, never into a foreign receipt:

- ``review round <n>`` (``rround-``): one observed review round — its
  decision (from the observer's canonical round listing), the round
  record's file name and CONTENT digest (read through the hardened review
  read). A tampered receipt contributes nothing trusted.
- ``review listing`` (``rlist-``, R17-1): the collector's statement of the
  COMPLETE review listing it observed in one pass — every listed round
  with its decision, whether the listing was complete, the highest round
  and that round's content digest as read in the same pass. Written in the
  same save as that pass's round receipts whenever it differs from the
  latest listing receipt. It is what PROVES the review standing
  (``review_round_reading``); a round number recorded later proves
  nothing.
- ``candidate identity`` (``rcand-``): one observation of the delivery
  candidate — its status (``exact`` / ``not_exact`` / ``unavailable``), the
  base it was observed against, the HEAD (labelled as HEAD), the entry
  count, the P1-A6 identity (``pr_delivery.candidate.identity_digest``;
  absent when ``unavailable``) and the problem code. The P1-A6 identity
  stays the ONE candidate identity; the binding is receipt content, not a
  second identity. The LATEST receipt decides.

These are observations the Runtime holds, never acceptance and never
authority. The binding is an integrity check against alteration of a
recorded field, not an authentication: a writer able to recompute the
whole receipt could forge a consistent one, and one able to delete or
reorder whole receipts is outside what receipt content can show.
"""

import secrets

from workflow_authority import record as record_module
from workflow_authority.digest import json_digest

REVIEW_ROUND_RECEIPT_MARKER = "review round"
REVIEW_LISTING_RECEIPT_MARKER = "review listing"
CANDIDATE_RECEIPT_MARKER = "candidate identity"
# The writers' turn-id prefixes: a second, independent recognition key.
REVIEW_ROUND_TURN_PREFIX = "rround-"
REVIEW_LISTING_TURN_PREFIX = "rlist-"
CANDIDATE_TURN_PREFIX = "rcand-"

CANDIDATE_STATUS_EXACT = "exact"
CANDIDATE_STATUS_NOT_EXACT = "not_exact"
CANDIDATE_STATUS_UNAVAILABLE = "unavailable"
CANDIDATE_STATUSES = (CANDIDATE_STATUS_EXACT, CANDIDATE_STATUS_NOT_EXACT,
                      CANDIDATE_STATUS_UNAVAILABLE)
PROBLEM_CANDIDATE_NOT_EXACT = "broker_candidate_not_exact"
PROBLEM_CANDIDATE_CAPTURE = "broker_candidate_capture_failed"
PROBLEM_CANDIDATE_RECEIPT_TAMPERED = "broker_candidate_receipt_tampered"
PROBLEM_REVIEW_RECEIPT_TAMPERED = "broker_review_round_receipt_tampered"
PROBLEM_REVIEW_UNPROVEN = "broker_review_standing_unproven"

# The decisions a round may carry (the listing's closed tokens) and the
# placeholder the writer uses when the listing carried none.
REVIEW_DECISIONS = ("APPROVE", "REJECT")
REVIEW_DECISION_NONE = "None"

# The largest number a receipt states (a round, an entry count): six ASCII
# digits. The collector records nothing larger; the parser refuses it.
MAX_RECEIPT_NUMBER = 999999
# The most listed rounds a listing receipt states (the most recent ones;
# the observer itself lists at most 40), so its summary always fits.
MAX_LISTED_ROUNDS = 64


def _hex(value, length):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))


def _decimal(text):
    """``text`` as a CANONICAL positive ASCII decimal (1..999999, no sign,
    no padding, no leading zero), or None. Never raises: a Unicode digit
    (a superscript, an Arabic-Indic digit) is not a decimal here (R17-2)."""
    if not isinstance(text, str) or not 1 <= len(text) <= 6:
        return None
    if not all("0" <= c <= "9" for c in text) or text[0] == "0":
        return None
    return int(text)


def _number(value):
    """A number the collector may state (a non-bool int in range), or None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= MAX_RECEIPT_NUMBER else None


def _token(value):
    """A summary token: ``None`` spelled out; any whitespace would split
    the summary, so a value carrying it can never round-trip (and the
    binding refuses the receipt)."""
    return "None" if value is None else str(value)


def _fields(text):
    """``key=value`` tokens of a summary tail (the canonical-render check
    refuses duplicates, extras and reordering; this only reads)."""
    return dict(token.split("=", 1) for token in text.split() if "=" in token)


def _recognised(receipt, marker, turn_prefix, separator):
    """``(summary, marked)`` for a receipt of the family named by ``marker``
    (its summary carries the marker) or ``turn_prefix`` (its turn id
    carries the writer's prefix); None when it is neither."""
    if not isinstance(receipt, dict):
        return None
    summary = receipt.get("bounded_summary")
    turn_id = receipt.get("turn_id")
    marked = isinstance(summary, str) and summary.startswith(marker + separator)
    prefixed = isinstance(turn_id, str) and turn_id.startswith(turn_prefix)
    if not marked and not prefixed:
        return None
    return (summary if isinstance(summary, str) else ""), marked


def _bounded(summary):
    return summary[:record_module.MAX_BOUNDED_SUMMARY_CHARS]


# -- review rounds --------------------------------------------------------


def review_round_binding(round_number, decision, file_name, content_digest):
    """The binding digest of a review round receipt's semantic fields."""
    return json_digest({"marker": REVIEW_ROUND_RECEIPT_MARKER, "round": round_number,
                        "decision": _token(decision), "file": _token(file_name),
                        "content": _token(content_digest)})


def _round_summary(round_number, decision, file_name, content_digest):
    return _bounded("%s %d: decision=%s file=%s content=%s" % (
        REVIEW_ROUND_RECEIPT_MARKER, round_number, _token(decision),
        _token(file_name), _token(content_digest)))


def review_round_receipt(round_number, decision, file_name, content_digest, now,
                         turn_id_factory=None):
    """The Runtime's receipt of ONE observed review round: the summary
    states round, decision, file and the round record's content digest;
    the receipt digest binds them. A listed decision outside the closed
    tokens is recorded as none (the standing stays PENDING, as before)."""
    turn_id = (REVIEW_ROUND_TURN_PREFIX + secrets.token_hex(8) if turn_id_factory is None
               else turn_id_factory())
    decision = decision if decision in REVIEW_DECISIONS else None
    return {
        "kind": record_module.RECEIPT_KIND_EVIDENCE,
        "turn_id": turn_id,
        "recorded_at": now,
        "digest": review_round_binding(round_number, decision, file_name,
                                       content_digest),
        "bounded_summary": _round_summary(round_number, decision, file_name,
                                          content_digest),
    }


def parse_review_round_receipt(receipt):
    """ONE review round receipt, parsed and checked, or None when it is not
    one (neither its summary marker nor its turn-id prefix): ``round``,
    ``decision`` (None when the listing had none), ``file``, ``content``,
    ``receipt_digest`` and ``consistent`` — True only when the round is a
    canonical decimal, every field has its closed shape, the summary is
    exactly the canonical rendering of those fields AND the receipt digest
    is their binding. Anything else — a malformed or Unicode number, a
    missing marker on a receipt with the writer's turn id — is a TAMPERED
    review round receipt: its fields are UNTRUSTED and never read as an
    observation. Never raises."""
    found = _recognised(receipt, REVIEW_ROUND_RECEIPT_MARKER, REVIEW_ROUND_TURN_PREFIX, " ")
    if found is None:
        return None
    summary, marked = found
    parsed = {"round": None, "decision": None, "file": None, "content": None,
              "receipt_digest": receipt.get("digest"), "consistent": False}
    if not marked:
        return parsed
    head, _, rest = summary[len(REVIEW_ROUND_RECEIPT_MARKER) + 1:].partition(":")
    fields = _fields(rest)
    decision = fields.get("decision")
    parsed.update({
        "round": _decimal(head),
        "decision": None if decision == REVIEW_DECISION_NONE else decision,
        "file": fields.get("file"),
        "content": fields.get("content"),
    })
    shaped = (parsed["round"] is not None and _hex(parsed["content"], 64)
              and (parsed["decision"] is None or parsed["decision"] in REVIEW_DECISIONS)
              and isinstance(parsed["file"], str) and parsed["file"] != "None")
    parsed["consistent"] = bool(
        shaped
        and summary == _round_summary(parsed["round"], parsed["decision"],
                                      parsed["file"], parsed["content"])
        and parsed["receipt_digest"] == review_round_binding(
            parsed["round"], parsed["decision"], parsed["file"], parsed["content"]))
    return parsed


# -- the review listing (R17-1) --------------------------------------------


def listing_statement(reviews, diagnostics):
    """The collector's statement of the observer's review listing:
    ``(listed, complete)`` — ``listed`` the ``(round, decision)`` pairs in
    round order (decision None unless a closed token), ``complete`` True
    only when the observer answered for the whole directory: its section
    state is ``available``/``empty``/``missing``, every listed entry has a
    recordable round number, no round is listed twice, the observer's own
    highest round (``rounds``) IS the highest listed round (truncation keeps
    the most recent rounds, so it holds when truncated), and no ``reviews``
    diagnostic reports the scan as capped (``unavailable``). At most the
    ``MAX_LISTED_ROUNDS`` most recent rounds are stated (the highest always
    among them). Pure."""
    if not isinstance(reviews, dict):
        return [], False
    listed = reviews.get("listed")
    complete = (reviews.get("state") in ("available", "empty", "missing")
                and isinstance(listed, list))
    pairs = {}
    for item in listed if isinstance(listed, list) else []:
        number = _number(item.get("round")) if isinstance(item, dict) else None
        if number is None or number in pairs:
            complete = False
            continue
        decision = item.get("decision")
        pairs[number] = decision if decision in REVIEW_DECISIONS else None
    highest = reviews.get("rounds")
    if pairs:
        if _number(highest) != max(pairs):
            complete = False
    elif highest not in (None, 0) or isinstance(highest, bool):
        complete = False
    for diagnostic in diagnostics if isinstance(diagnostics, list) else []:
        if (isinstance(diagnostic, dict) and diagnostic.get("source") == "reviews"
                and diagnostic.get("state") == "unavailable"):
            complete = False
    return sorted(pairs.items())[-MAX_LISTED_ROUNDS:], complete


def _listing_summary(complete, listed, content):
    latest, decision = listed[-1] if listed else (None, None)
    return _bounded("%s: complete=%s latest=%s decision=%s content=%s listed=%s" % (
        REVIEW_LISTING_RECEIPT_MARKER, _token(bool(complete)), _token(latest),
        _token(decision), _token(content),
        ",".join("%d:%s" % (n, _token(d)) for n, d in listed)))


def review_listing_binding(complete, listed, content):
    """The binding digest of a review listing receipt's semantic fields."""
    latest, decision = listed[-1] if listed else (None, None)
    return json_digest({"marker": REVIEW_LISTING_RECEIPT_MARKER,
                        "complete": bool(complete), "latest": latest,
                        "decision": _token(decision), "content": _token(content),
                        "listed": [[n, _token(d)] for n, d in listed]})


def review_listing_receipt(listed, latest_content, complete, now, turn_id_factory=None):
    """The collector's receipt of ONE pass's review listing (``listed`` and
    ``complete`` from ``listing_statement``; ``latest_content`` the content
    digest of the highest listed round as read in THIS pass, or None)."""
    turn_id = (REVIEW_LISTING_TURN_PREFIX + secrets.token_hex(8)
               if turn_id_factory is None else turn_id_factory())
    listed = [(n, d if d in REVIEW_DECISIONS else None) for n, d in listed]
    content = latest_content if listed else None
    return {
        "kind": record_module.RECEIPT_KIND_EVIDENCE,
        "turn_id": turn_id,
        "recorded_at": now,
        "digest": review_listing_binding(complete, listed, content),
        "bounded_summary": _listing_summary(complete, listed, content),
    }


def _listed_pairs(text):
    """The ``listed`` token parsed: strictly ascending canonical rounds with
    closed decisions, or None when malformed. Never raises."""
    if text == "":
        return []
    if not isinstance(text, str):
        return None
    pairs = []
    for item in text.split(","):
        number, colon, decision = item.partition(":")
        number = _decimal(number)
        if number is None or not colon or (pairs and number <= pairs[-1][0]):
            return None
        if decision == REVIEW_DECISION_NONE:
            decision = None
        elif decision not in REVIEW_DECISIONS:
            return None
        pairs.append((number, decision))
    return pairs


def parse_review_listing_receipt(receipt):
    """ONE review listing receipt, parsed and checked, or None when it is
    not one (neither marker nor turn-id prefix): ``complete``, ``listed``,
    ``latest``, ``decision``, ``content``, ``receipt_digest`` and
    ``consistent`` — True only when every field is canonical, ``latest`` /
    ``decision`` are the last listed pair, the summary is exactly the
    canonical rendering AND the digest is the binding. Never raises."""
    found = _recognised(receipt, REVIEW_LISTING_RECEIPT_MARKER,
                        REVIEW_LISTING_TURN_PREFIX, ":")
    if found is None:
        return None
    summary, marked = found
    parsed = {"complete": None, "listed": None, "latest": None, "decision": None,
              "content": None, "receipt_digest": receipt.get("digest"),
              "consistent": False}
    if not marked:
        return parsed
    fields = _fields(summary[len(REVIEW_LISTING_RECEIPT_MARKER) + 1:])
    listed = _listed_pairs(fields.get("listed"))
    content = fields.get("content")
    parsed.update({
        "complete": {"True": True, "False": False}.get(fields.get("complete")),
        "listed": listed,
        "content": None if content == "None" else content,
    })
    if listed:
        parsed["latest"], parsed["decision"] = listed[-1]
    shaped = (parsed["complete"] is not None and listed is not None
              and (parsed["content"] is None
                   or (listed and _hex(parsed["content"], 64))))
    parsed["consistent"] = bool(
        shaped
        and summary == _listing_summary(parsed["complete"], listed, parsed["content"])
        and parsed["receipt_digest"] == review_listing_binding(
            parsed["complete"], listed, parsed["content"]))
    return parsed


def same_review_listing(latest, receipt):
    """Whether ``receipt`` repeats the record's latest CONSISTENT listing
    receipt exactly: an unchanged listing is never recorded twice."""
    if latest is None or not latest["consistent"]:
        return False
    parsed = parse_review_listing_receipt(receipt)
    return parsed is not None and all(
        parsed[key] == latest[key] for key in ("complete", "listed", "content"))


# -- reading the review standing ------------------------------------------


def review_round_reading(entry):
    """The record's review receipts, read by the ONE rule (R16-1, R17-1):

    1. TRUST: a consistent round receipt is trusted (its bound round,
       decision, file and content digest); per round the LATEST trusted
       receipt stands for ``rounds`` (the held digests). A TAMPERED review
       receipt — round or listing — contributes nothing trusted and is
       named by its recorded POSITION, never by a number it states.
    2. PROOF: the standing is PROVEN only by the collector's own statement
       of what it observed — the LATEST review listing receipt (by recorded
       position; listing receipts are written once per changed pass, so the
       latest is the newest listing) must be consistent and ``complete``,
       list at least one round, and carry the content digest of its highest
       round N; and a TRUSTED round receipt must bind exactly
       (N, the listed decision, that content digest). The standing is then
       that decision. The listing must also never REGRESS: its highest
       round must be at least every round any trusted receipt — round or
       earlier listing — has stated (rounds never disappear from an honest
       listing; so a listing that lost the newest round, or an older
       listing left latest because a newer one is gone, proves nothing).
       Nothing else resolves anything: not a position, not a maximum
       recorded before or after a tampered receipt, not a bare higher
       round recorded later.
    3. Why a delayed read cannot forge it: a round whose read failed and
       later succeeds is recorded AFTER a newer round, but the pass that
       recorded the newer round also recorded the complete listing naming
       it as the highest; the delayed read adds a receipt for an OLDER
       round, which can never back the listed highest round. Hiding the
       highest round's receipt (renumbering, any edit) removes its backing;
       editing the listing breaks the listing; editing either's marker or
       turn id leaves it recognised as tampered.

    Returns ``{"rounds", "tampered", "listing", "proof", "gap",
    "unresolved"}``: ``proof`` is ``{"round", "decision", "content",
    "listing_position", "backing_position"}`` or None with ``gap`` naming
    why; ``unresolved`` is every tampered position while no proof stands
    (a proof shows the standing whatever the tampered receipts hid)."""
    trusted = {}
    backings = []
    tampered = []
    listing = None
    highest_seen = 0
    for position, receipt in enumerate(entry.get("receipts") or []):
        parsed = parse_review_round_receipt(receipt)
        if parsed is not None:
            if parsed["consistent"]:
                trusted[parsed["round"]] = parsed
                backings.append((position, parsed))
                highest_seen = max(highest_seen, parsed["round"])
            else:
                tampered.append(position)
        parsed = parse_review_listing_receipt(receipt)
        if parsed is not None:
            listing = dict(parsed, position=position)
            if not parsed["consistent"] and position not in tampered:
                tampered.append(position)
            elif parsed["consistent"] and parsed["latest"] is not None:
                highest_seen = max(highest_seen, parsed["latest"])
    proof = None
    gap = None
    if listing is None:
        gap = "no review listing receipt is recorded"
    elif not listing["consistent"]:
        gap = "the latest review listing receipt is tampered"
    elif not listing["complete"]:
        gap = "the latest review listing is incomplete"
    elif listing["latest"] is None:
        gap = "the latest review listing lists no round"
    elif listing["latest"] < highest_seen:
        gap = ("the latest review listing names round %d, older than round %d"
               " already observed" % (listing["latest"], highest_seen))
    elif listing["content"] is None:
        gap = "round %d was not read when it was last listed" % listing["latest"]
    else:
        for position, parsed in backings:
            if (parsed["round"], parsed["decision"], parsed["content"]) == (
                    listing["latest"], listing["decision"], listing["content"]):
                proof = {"round": listing["latest"], "decision": listing["decision"],
                         "content": listing["content"],
                         "listing_position": listing["position"],
                         "backing_position": position}
                gap = None
                break
        else:
            gap = ("round %d as listed is not backed by a trusted round receipt"
                   % listing["latest"])
    return {"rounds": [(n, trusted[n]["decision"], trusted[n]["file"],
                        trusted[n]["content"]) for n in sorted(trusted)],
            "tampered": sorted(tampered), "listing": listing, "proof": proof,
            "gap": gap, "unresolved": [] if proof is not None else sorted(tampered)}


def observed_review_rounds(entry):
    """``[(round, decision, file, content_digest)]`` in round order: the
    TRUSTED rounds (``review_round_reading`` rule 1); a tampered receipt
    contributes none."""
    return review_round_reading(entry)["rounds"]


def tampered_review_rounds(entry):
    """The recorded POSITIONS (indices in ``entry["receipts"]``) of every
    tampered review receipt — never a stated, untrusted number."""
    return review_round_reading(entry)["tampered"]


def unresolved_review_rounds(entry):
    """The positions of the tampered review receipts while no proof stands
    (``review_round_reading`` rule 2)."""
    return review_round_reading(entry)["unresolved"]


# -- the candidate --------------------------------------------------------


def head_commit_digest(commit_sha):
    """The HEAD commit, digested as ONE identity — reported to the Mission
    Core under its own key and labelled as HEAD; never the candidate
    identity (that is the P1-A6 staged identity)."""
    return json_digest({"head_commit_sha": commit_sha})


def porcelain_outside_candidate(text):
    """Every porcelain line that is NOT a fully staged A/M/D entry — the
    delivery layer's own exactness rule (``pr_delivery.machine
    ._porcelain_unstaged``: an untracked ``??`` entry, an index status
    other than A/M/D — rename, copy, type change, unmerged — or any
    worktree status beside the index one), each line bounded. Pinned
    equal to that rule line by line in the tests."""
    outside = []
    for line in text.splitlines():
        if len(line) < 3:
            continue
        if line.startswith("??") or line[0] not in "AMD" or line[1] != " ":
            outside.append(line[:120])
    return outside


def candidate_binding(status, base, head, entries, identity, problem):
    """The binding digest of a candidate observation receipt's semantic
    fields (``entries`` is the entry COUNT)."""
    return json_digest({"marker": CANDIDATE_RECEIPT_MARKER, "status": _token(status),
                        "base": _token(base), "head": _token(head),
                        "entries": _token(entries), "identity": _token(identity),
                        "problem": _token(problem)})


def _candidate_summary(status, base, head, entries, identity, problem):
    return _bounded("%s: status=%s base=%s head=%s entries=%s identity=%s problem=%s" % (
        CANDIDATE_RECEIPT_MARKER, _token(status), _token(base), _token(head),
        _token(entries), _token(identity), _token(problem)))


def candidate_receipt(observation, now, turn_id_factory=None):
    """The Runtime's receipt of ONE candidate observation: the summary
    states status, base, HEAD, entry count, the P1-A6 identity (absent
    when ``unavailable``) and the problem; the receipt digest binds them
    (R15-4)."""
    turn_id = (CANDIDATE_TURN_PREFIX + secrets.token_hex(8) if turn_id_factory is None
               else turn_id_factory())
    status = observation["status"]
    identity = (observation["digest"] if status != CANDIDATE_STATUS_UNAVAILABLE
                else None)
    count = None if observation["entries"] is None else len(observation["entries"])
    return {
        "kind": record_module.RECEIPT_KIND_EVIDENCE,
        "turn_id": turn_id,
        "recorded_at": now,
        "digest": candidate_binding(status, observation["base"], observation["head"],
                                    count, identity, observation["problem"]),
        "bounded_summary": _candidate_summary(status, observation["base"],
                                              observation["head"], count, identity,
                                              observation["problem"]),
    }


def parse_candidate_receipt(receipt):
    """ONE candidate observation receipt, parsed and checked, or None when
    ``receipt`` is not one (neither marker nor turn-id prefix): ``status``,
    ``base``, ``head``, ``entries`` (count), ``identity`` (the P1-A6
    identity, None when unavailable), ``problem``, ``receipt_digest`` and
    ``consistent``. ``consistent`` is False — TAMPERED, never accepted —
    unless every field has its closed shape (status one of three, 40-hex
    base, 40-hex or absent HEAD, a canonical decimal entry count), the
    status rules hold (``exact``: a 64-hex identity, entries >= 1, no
    problem; ``not_exact``: the same with its own problem; ``unavailable``:
    no identity, no entries, a problem), the summary is exactly the
    canonical rendering AND the receipt digest is the binding of exactly
    the stated fields. Never raises."""
    found = _recognised(receipt, CANDIDATE_RECEIPT_MARKER, CANDIDATE_TURN_PREFIX, ":")
    if found is None:
        return None
    summary, marked = found
    parsed = {"status": None, "base": None, "head": None, "entries": None,
              "identity": None, "problem": None,
              "receipt_digest": receipt.get("digest"), "consistent": False}
    if not marked:
        return parsed
    fields = _fields(summary[len(CANDIDATE_RECEIPT_MARKER) + 1:])

    def optional(name):
        value = fields.get(name)
        return None if value in (None, "None") else value
    entries = fields.get("entries")
    parsed.update({
        "status": fields.get("status"),
        "base": fields.get("base"),
        "head": optional("head"),
        "entries": _decimal(entries),
        "identity": optional("identity"),
        "problem": optional("problem"),
    })
    status = parsed["status"]
    shaped = (status in CANDIDATE_STATUSES and _hex(parsed["base"], 40)
              and (parsed["head"] is None or _hex(parsed["head"], 40)))
    if shaped and status == CANDIDATE_STATUS_UNAVAILABLE:
        shaped = (parsed["problem"] is not None and parsed["entries"] is None
                  and parsed["identity"] is None and entries == "None")
    elif shaped:
        shaped = (_hex(parsed["identity"], 64) and parsed["entries"] is not None
                  and (
                      (status == CANDIDATE_STATUS_EXACT and parsed["problem"] is None)
                      or (status == CANDIDATE_STATUS_NOT_EXACT
                          and parsed["problem"] == PROBLEM_CANDIDATE_NOT_EXACT)))
    parsed["consistent"] = bool(
        shaped
        and summary == _candidate_summary(status, parsed["base"], parsed["head"],
                                          parsed["entries"], parsed["identity"],
                                          parsed["problem"])
        and parsed["receipt_digest"] == candidate_binding(
            status, parsed["base"], parsed["head"], parsed["entries"],
            parsed["identity"], parsed["problem"]))
    return parsed


def observed_candidate(entry):
    """The record's LATEST candidate observation receipt, parsed (see
    ``parse_candidate_receipt``), or None when the record holds none. The
    latest decides: an earlier identity is never reported once a later
    observation says the candidate changed, disappeared or is not exact,
    and a tampered latest receipt (``consistent`` False) is refused
    rather than skipped in favour of an older one."""
    latest = None
    for receipt in entry.get("receipts") or []:
        parsed = parse_candidate_receipt(receipt)
        if parsed is not None:
            latest = parsed
    return latest


def same_candidate_observation(latest, receipt):
    """Whether ``receipt`` repeats the record's latest CONSISTENT candidate
    observation exactly (status, base, HEAD, entry count, identity,
    problem): an unchanged observation is never recorded twice."""
    if latest is None or not latest["consistent"]:
        return False
    parsed = parse_candidate_receipt(receipt)
    return parsed is not None and all(
        parsed[key] == latest[key]
        for key in ("status", "base", "head", "entries", "identity", "problem"))
