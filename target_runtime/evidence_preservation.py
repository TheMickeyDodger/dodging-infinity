"""AB-1: preserve the target herd's evidence BEFORE cleanup reclaims it.

The obligation this discharges
==============================

R-33 Y-2 said terminal cleanup "reclaims LIVE RESOURCES; it does NOT
erase the record of what happened". It was erasing it. The target
herd's own engineering evidence — its Supervisor's strategy, its
Lead/Executor/Reviewer artifacts — is written into the managed
workspace directory, and terminal cleanup deletes that directory. So
the run finished, the workspace was reclaimed, and the proof that the
DI-REMOTE-2 chain had worked went with it.

That matters beyond a test: the mission's stated outcome is a system ready for INDEPENDENT
INSPECTION, and cleanup that destroys the target engineering evidence
leaves an inspector with no artifact to inspect.

Reclaiming live resources and preserving forensics are DIFFERENT
obligations. Satisfying the first left the second open — the same shape as ordering
leaving conditionality open, and wiring leaving attribution open.

Where it lands in the ordering
==============================

Preservation runs BEFORE the session close and BEFORE the directory
deletion, so it reads the artifacts while they still exist. That makes
it a THIRD step in the destructive ordering, and the destructive-operation enumeration records it inside `_release`'s
row: it must run FIRST, and what must be proven for the steps after it
is that it succeeded. It is not itself in that domain, because it only
reads and writes.

What it will not do
===================

**Fabrication is outside what it does.** It copies bytes and records a
digest of the FULL file, so a preserved artifact is bound to what was
actually there. Reconstructing, summarising or synthesising would be worse than
losing the evidence, because a reader could not tell the difference. A
mutant that lets this invent content dies by authored assertion.

**It is truthful about its bounds.** Each preserved file records whether
it was TRUNCATED, how many bytes were kept, and the digest of the whole
file — so within this projection a capped archive is not readable as a
complete one, and the digest still identifies the original even when the stored
copy is partial.
"""

import hashlib
import json
import os

#: Where preserved evidence lives: under the STORE directory, outside
#: the workspace being reclaimed. A projection stored inside the
#: resource it preserves would be destroyed with it.
PRESERVED_DIR_NAME = "preserved-evidence"

#: Per-file and total bounds. A projection that grew without limit
#: would turn cleanup into an unbounded copy.
MAX_FILE_BYTES = 65536
MAX_FILES = 32
MAX_TOTAL_BYTES = 1048576

#: The target-side state directory, relative to the leased workspace.
STATE_SUBDIRS = (".herd", "state")

PRESERVE_RECEIPT_MARKER = "target evidence preserved"

PROBLEM_NO_LEASE = "preserve_no_lease_recorded"
PROBLEM_UNREADABLE = "preserve_workspace_unreadable"
PROBLEM_WRITE_FAILED = "preserve_write_failed"
PROBLEM_READBACK = "preserve_readback_mismatch"
PROBLEM_INCOMPLETE = "preserve_incomplete_against_policy"


def preserved_path(store_directory, workflow_id):
    return os.path.join(
        store_directory, PRESERVED_DIR_NAME, "%s.json" % workflow_id
    )


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def collect(lease_path):
    """Read the target herd's state artifacts as bounded records.

    Returns ``(files, truncated_listing)``. Each file carries its
    name, the bytes KEPT, whether it was truncated, its kept-byte
    count, its FULL byte count, and the digest of the FULL contents —
    so a partial copy still identifies the original exactly.

    ``truncated_listing`` is True when more files were present than
    `MAX_FILES`, which is reported rather than silently dropped.
    """
    state = os.path.join(lease_path, *STATE_SUBDIRS)
    if not os.path.isdir(state):
        return [], False
    names = sorted(
        name for name in os.listdir(state)
        if os.path.isfile(os.path.join(state, name))
    )
    truncated_listing = len(names) > MAX_FILES
    files, total = [], 0
    for name in names[:MAX_FILES]:
        path = os.path.join(state, name)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            # A file this is unable to read is RECORDED as unreadable
            # rather than skipped: a preserved set that silently omitted
            # it would be a partial archive presented as whole.
            files.append({
                "name": name, "unreadable": True, "kept_bytes": 0,
                "full_bytes": None, "truncated": False,
                "digest": None, "content": None,
            })
            continue
        full_digest = _digest(data)
        kept = data[:MAX_FILE_BYTES]
        if total + len(kept) > MAX_TOTAL_BYTES:
            kept = kept[:max(0, MAX_TOTAL_BYTES - total)]
        total += len(kept)
        files.append({
            "name": name,
            "unreadable": False,
            "kept_bytes": len(kept),
            "full_bytes": len(data),
            "truncated": len(kept) < len(data),
            "digest": full_digest,
            "content": kept.decode("utf-8", errors="replace"),
        })
    return files, truncated_listing


#: THE COMPLETENESS POLICY (R-42 AF-3), named rather than implied.
#:
#: "Complete" has to mean complete AGAINST A POLICY, not against
#: whatever happened to be readable — otherwise an unreadable file
#: makes the archive smaller and still "complete", which is the
#: silent-truncation shape wearing a checkmark.
#:
#: REQUIRED: every artifact found must be READABLE, and the LISTING
#: must not be truncated. Missing evidence halts the chain, because
#: the two steps after preservation destroy the only other copy, and
#: #: a truncated listing is unable to say what was lost.
#:
#: OPTIONAL: the CONTENT of an over-large file may be truncated and
#: the chain may proceed — the policy says so explicitly, the entry
#: records exact kept and full byte counts, and the digest still
#: identifies the whole file. A bounded, disclosed loss of bytes,
#: rather than an unknown loss of evidence.
POLICY_REQUIRES_READABLE = True
POLICY_REQUIRES_WHOLE_LISTING = True
POLICY_ALLOWS_CONTENT_TRUNCATION = True

#: THE REQUIRED ARTIFACTS, NAMED (R-42 AF-3).
#:
#: AF-3 asked for the required artifacts to be named explicitly, and
#: the first attempt named none: `required_names` defaulted to `()`
#: and the only caller that ever passed it was a test, so within
#: production the required-artifact half of the policy could not fire.
#: A correct mechanism exercised only by tests is the R-31 shape — a
#: seam defaulted to an empty set — combined with the R-42 shape of a
#: value computed and left unenforced.
#:
#: These four are what R-37 found terminal cleanup destroying: the
#: TARGET herd's own engineering evidence, and the capstone's proof
#: that the DI-REMOTE-2 chain ran end to end. Their absence is what
#: must HALT the chain, because the two steps after preservation
#: destroy the only other copy.
REQUIRED_ARTIFACTS = (
    "supervisor-strategy.md",
    "lead-evidence.md",
    "executor-evidence.md",
    "reviewer-evidence.md",
)


def policy_violations(files, truncated_listing, required_names):
    """What the policy says is MISSING, as a list of reasons.

    Empty means complete against the policy. Non-empty means the chain
    must HALT: this is the value that GATES, not a label that
    decorates.
    """
    reasons = []
    if truncated_listing and POLICY_REQUIRES_WHOLE_LISTING:
        reasons.append(
            "the listing was truncated, so the archive cannot say"
            " what it failed to preserve"
        )
    present = {entry.get("name") for entry in files}
    for name in required_names or ():
        if name not in present:
            reasons.append("required artifact %r is absent" % name)
    if POLICY_REQUIRES_READABLE:
        for entry in files:
            if entry.get("unreadable"):
                reasons.append(
                    "required artifact %r could not be read"
                    % entry.get("name")
                )
    return reasons


def is_complete(files, truncated_listing, required_names):
    """Whether the archive is complete AGAINST THE POLICY.

    AC-5: DERIVED from the entries rather than asserted alongside
    them. AF-1: it GATES — `preserve` returns not-ok when this is
    False, so an incomplete archive halts the chain rather than
    proceeding with a different adjective in its summary.

    Content truncation of an over-large file does NOT make the archive
    incomplete: the policy allows it explicitly and the entry
    discloses the exact bounds.
    """
    return not policy_violations(files, truncated_listing,
                                 required_names)


def preserve(entry, lease_path, store_directory, now,
             workspace_id=None, *, required_names):
    """Write the preserved projection, and PROVE it by reading it back.

    ``workspace_id`` is passed IN rather than derived here (AC-2): it
    comes from the same unique child-record/live-workspace binding the
    close is about to act on. Deriving it independently would let the preserved record name one
    workspace while the close named another, with no check between them.

    ``required_names`` is KEYWORD-ONLY AND HAS NO DEFAULT, which is
    the structural half of AF-3. It defaulted to `()` before, and
    production passed an empty set, so within production the
    required-artifact half of the policy could not fire. Within this
    seam a parameter with no default cannot be omitted silently:
    production passes `REQUIRED_ARTIFACTS`, and a caller that
    genuinely requires no named artifact has to say `()` out loud.

    Returns ``(ok, problem, detail, summary)``. ``ok`` is True only
    when a FRESH READ from disk returns the document just written
    (AC-4): a write that returned is not proof, and preservation is
    the precondition for two destructive steps.
    """
    workflow_id = entry.get("workflow_id")
    if not isinstance(lease_path, str) or not lease_path:
        return False, PROBLEM_NO_LEASE, "no lease path recorded", None
    try:
        files, truncated_listing = collect(lease_path)
    except OSError as exc:
        return (False, PROBLEM_UNREADABLE,
                "could not read %s: %s" % (lease_path, exc), None)
    engine = entry.get("target_engine")
    violations = policy_violations(files, truncated_listing,
                                   required_names)
    complete = not violations
    document = {
        "workflow_id": workflow_id,
        "required_names": list(required_names),
        "task_id": (engine or {}).get("task_id"),
        "workspace_id": workspace_id,
        "preserved_at": now,
        "truncated_listing": truncated_listing,
        "complete": complete,
        "policy_violations": violations,
        "file_count": len(files),
        "files": files,
    }
    path = preserved_path(store_directory, workflow_id)
    payload = json.dumps(document, indent=2, sort_keys=True)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # ATOMIC: # written to a temp file in the same directory and renamed, so
        # within this write a crash leaves the previous archive or none,
        # rather than a half-written one a later read would treat as the
        # archive.
        temporary = path + ".partial"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        return (False, PROBLEM_WRITE_FAILED,
                "could not write %s: %s" % (path, exc), None)
    # READ-BACK: preservation is proven by reading it, not by the
    # write returning.
    fresh = load_preserved(store_directory, workflow_id)
    if fresh is None or fresh.get("file_count") != len(files):
        return (False, PROBLEM_READBACK,
                "a fresh read of %s does not return the projection"
                " just written" % path, None)
    summary = "%s: %d file(s), %s" % (
        PRESERVE_RECEIPT_MARKER, len(files),
        "complete" if complete
        else "INCOMPLETE: " + "; ".join(violations),
    )
    if not complete:
        # AF-1: THE VALUE GATES. It previously chose a word in
        # this summary while the function returned True
        # regardless, so an INCOMPLETE archive reported itself
        # honestly and the chain closed the sessions and deleted
        # the directory anyway. Truthful reporting is not
        # enforcement.
        return False, PROBLEM_INCOMPLETE, "; ".join(violations), summary
    return True, None, None, summary


#: Task 8 startup correction: a corrective follow-up's start RETIRES the
#: target herd's persisted runtime state (``.herd/state/runtime.json``) that
#: names this workflow's earlier runtime. Its exact bytes are preserved here
#: first — beside, never inside, the terminal projection ``preserved_path``
#: names, one file per follow-up ordinal.
#: TERMINAL: an INTACT copy of different bytes is already preserved.
PROBLEM_RUNTIME_STATE_CONFLICT = "preserve_runtime_state_conflict"
#: TERMINAL: something exists at the archive path that is not an intact
#: archive of THIS retirement — never overwritten, never read as missing.
PROBLEM_RUNTIME_STATE_MALFORMED = "preserve_runtime_state_malformed"
#: Recoverable: something exists at the archive path and cannot be read.
PROBLEM_RUNTIME_STATE_UNREADABLE = "preserve_runtime_state_unreadable"

RUNTIME_STATE_MISSING = "missing"
RUNTIME_STATE_UNREADABLE = "unreadable"
RUNTIME_STATE_MALFORMED = "malformed"
RUNTIME_STATE_INTACT = "intact"
#: The largest archive read (the state is bounded at 64 KiB; JSON escaping
#: at most sextuples it).
MAX_RUNTIME_STATE_ARCHIVE_BYTES = 512 * 1024


def runtime_state_path(store_directory, workflow_id, dispatch_sequence):
    return os.path.join(
        store_directory, PRESERVED_DIR_NAME,
        "%s.runtime-state-%d.json" % (workflow_id, dispatch_sequence),
    )


def inspect_runtime_state(store_directory, workflow_id, dispatch_sequence):
    """Classify the preserved copy of one retirement: ``(status, document,
    detail)``. ``missing`` only when nothing exists at the path;
    ``unreadable`` when something exists and cannot be read; ``malformed``
    when it reads but is not an intact archive of THIS retirement (not
    UTF-8 JSON, not an object, over-bound, a field missing or of the wrong
    type, another workflow or ordinal, or a stored text whose OWN sha256 or
    size is not the recorded one); ``intact`` (with the document) only when
    every binding holds — the text re-digested, never the recorded digest
    alone."""
    path = runtime_state_path(store_directory, workflow_id, dispatch_sequence)
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_RUNTIME_STATE_ARCHIVE_BYTES + 1)
    except FileNotFoundError:
        return RUNTIME_STATE_MISSING, None, "%s does not exist" % path
    except OSError as exc:
        return (RUNTIME_STATE_UNREADABLE, None,
                "%s exists and cannot be read (%s)" % (path, exc.__class__.__name__))
    if len(raw) > MAX_RUNTIME_STATE_ARCHIVE_BYTES:
        return (RUNTIME_STATE_MALFORMED, None,
                "%s exceeds %d bytes" % (path, MAX_RUNTIME_STATE_ARCHIVE_BYTES))
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return RUNTIME_STATE_MALFORMED, None, "%s is not valid UTF-8 JSON" % path
    if not isinstance(document, dict):
        return RUNTIME_STATE_MALFORMED, None, "%s is not a JSON object" % path
    text, sha256, size = document.get("text"), document.get("sha256"), document.get("size")
    sequence = document.get("dispatch_sequence")
    if not (isinstance(text, str) and isinstance(sha256, str)
            and isinstance(size, int) and not isinstance(size, bool)
            and isinstance(sequence, int) and not isinstance(sequence, bool)):
        return (RUNTIME_STATE_MALFORMED, None,
                "%s lacks a well-typed text, sha256, size or dispatch_sequence" % path)
    if (document.get("workflow_id"), sequence) != (workflow_id, dispatch_sequence):
        return (RUNTIME_STATE_MALFORMED, None,
                "%s is bound to workflow %r ordinal %r, not %r ordinal %d"
                % (path, document.get("workflow_id"), sequence, workflow_id,
                   dispatch_sequence))
    try:
        stored = text.encode("utf-8")
    except UnicodeEncodeError:
        return RUNTIME_STATE_MALFORMED, None, "%s holds a text that is not UTF-8" % path
    if (_digest(stored), len(stored)) != (sha256, size):
        return (RUNTIME_STATE_MALFORMED, None,
                "%s: its stored text does not match its recorded sha256/size" % path)
    return RUNTIME_STATE_INTACT, document, None


def preserve_runtime_state(store_directory, workflow_id, dispatch_sequence, data, now):
    """Preserve ``data`` — the EXACT bytes of the runtime state about to be
    retired — and PROVE it: ``(ok, problem, detail, path)``. Idempotent only
    for an INTACT copy of the same bytes (a resumed retirement: the existing
    copy's text is re-digested and bound to this workflow and ordinal). A
    copy of DIFFERENT bytes (``PROBLEM_RUNTIME_STATE_CONFLICT``), or anything
    at the path that is malformed (``PROBLEM_RUNTIME_STATE_MALFORMED``) or
    unreadable (``PROBLEM_RUNTIME_STATE_UNREADABLE``), is NEVER overwritten
    or read as missing: its bytes are kept and the caller refuses. A new copy
    is linked into place only where nothing exists (``os.link`` never
    replaces), then read back through the same classification."""
    path = runtime_state_path(store_directory, workflow_id, dispatch_sequence)
    digest = _digest(data)

    def existing_verdict(status, document, detail):
        if status == RUNTIME_STATE_INTACT:
            if document["sha256"] == digest:
                return True, None, None, path
            return (False, PROBLEM_RUNTIME_STATE_CONFLICT,
                    "%s already preserves other bytes (sha256 %s, verified against its"
                    " stored text)" % (path, document["sha256"][:12]), path)
        if status == RUNTIME_STATE_UNREADABLE:
            return False, PROBLEM_RUNTIME_STATE_UNREADABLE, detail, path
        return False, PROBLEM_RUNTIME_STATE_MALFORMED, detail, path
    status, document, detail = inspect_runtime_state(
        store_directory, workflow_id, dispatch_sequence)
    if status != RUNTIME_STATE_MISSING:
        return existing_verdict(status, document, detail)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return False, PROBLEM_WRITE_FAILED, "the state is not UTF-8 (%s)" % exc, path
    document = {
        "workflow_id": workflow_id,
        "dispatch_sequence": dispatch_sequence,
        "source": "/".join(STATE_SUBDIRS + ("runtime.json",)),
        "sha256": digest,
        "size": len(data),
        "text": text,
        "preserved_at": now,
    }
    temporary = path + ".partial"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            # Something appeared since the inspection: judged, never replaced.
            return existing_verdict(*inspect_runtime_state(
                store_directory, workflow_id, dispatch_sequence))
    except OSError as exc:
        return False, PROBLEM_WRITE_FAILED, "could not write %s: %s" % (path, exc), path
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    status, fresh, detail = inspect_runtime_state(
        store_directory, workflow_id, dispatch_sequence)
    if status != RUNTIME_STATE_INTACT or fresh["sha256"] != digest:
        return (False, PROBLEM_READBACK,
                "a fresh read of %s does not return the bytes just preserved (%s)"
                % (path, detail or status), path)
    return True, None, None, path


def load_preserved(store_directory, workflow_id):
    """The preserved projection, or None.

    THE READ PATH FOR VERIFICATION AND STATUS (AB-2). It reads the
    store, so it does not depend on the managed directory surviving —
    which is the whole point, since by the time anyone reads this the
    directory is gone.
    """
    path = preserved_path(store_directory, workflow_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def preserved_text(store_directory, workflow_id, name):
    """The preserved content of one named artifact, or None."""
    document = load_preserved(store_directory, workflow_id)
    if not document:
        return None
    for entry in document.get("files") or []:
        if entry.get("name") == name:
            return entry.get("content")
    return None


def digests_match(store_directory, workflow_id, lease_path):
    """Whether every preserved digest still matches the live file.

    Usable only while the workspace exists, so it is a check for the
    moment of preservation rather than after cleanup. It exists so a
    test can prove the stored bytes are the ones that were there,
    rather than something this module produced.
    """
    document = load_preserved(store_directory, workflow_id)
    if not document:
        return False
    state = os.path.join(lease_path, *STATE_SUBDIRS)
    for entry in document.get("files") or []:
        if entry.get("unreadable"):
            continue
        path = os.path.join(state, entry["name"])
        try:
            with open(path, "rb") as handle:
                if _digest(handle.read()) != entry["digest"]:
                    return False
        except OSError:
            return False
    return True
