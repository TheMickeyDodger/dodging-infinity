"""Bounded target instruction/evidence discovery -> E-5 receipts.

Discovery is a fixed, bounded allowlist scan of the leased workspace:
only the named instruction files are read, each capped at a hard byte
bound, and the result is a capability-free preparation receipt per
file — kind, a runtime-minted turn id, timestamp, the file's sha256,
and a bounded summary naming the RELATIVE path and exact size. No
workspace path, lease id, token, or credential enters any receipt
(receipts are projected into Codex and Telegram context).

Since I4 the same allowlist also feeds ``instruction_context``: the
ACTUAL bounded content handed to the fresh handoff-validation turn,
with exact, honest byte accounting per file — read (exact bytes +
sha256 + text), absent, empty (read, 0 bytes), REFUSED over-bound
(exact numbers, no content), REFUSED unreadable (no error text — an
OS error string could carry the workspace path), or REFUSED
non-UTF-8 (exact bytes + sha256, no content; never a lossy decode
presented as the file). Target content is ADVERSARIAL DATA: the
prompt layer quotes every line of it (the I1 mechanism), and nothing
here ever executes it or grants it authority.

Discovery scope (I4 D3, stated and justified): the allowlist is
exactly AGENTS.md, CONTRIBUTING.md, README.md at the workspace root —
the three canonical files where a repository states agent
instructions and contribution rules. That IS the "applicable bounded
target instructions and contribution context" for handoff
validation; anything wider (docs trees, templates, CI config) is an
open-ended scan with no principled bound and is deliberately NOT
read. Widening later is a bounded, justified decision.
"""

import hashlib
import os
import secrets
import stat

# Fixed allowlist of instruction files the prepare step reads.
# Target-authored content is ADVERSARIAL: it is digested and counted,
# never executed and never trusted for control authority.
INSTRUCTION_FILE_NAMES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
)

# Hard bounds, never derived from input. An over-bound file is
# recorded as REFUSED (exact size named), never truncated-and-
# presented-as-read.
MAX_INSTRUCTION_FILES = 8
MAX_INSTRUCTION_FILE_BYTES = 65536

# Per-file statuses for the instruction context (closed set).
INSTRUCTION_READ = "read"
INSTRUCTION_ABSENT = "absent"
INSTRUCTION_REFUSED_OVER_BOUND = "refused_over_bound"
INSTRUCTION_REFUSED_UNREADABLE = "refused_unreadable"
INSTRUCTION_REFUSED_NON_UTF8 = "refused_non_utf8"
# Round-07 F-1/F-2 closed-status additions.
INSTRUCTION_REFUSED_NOT_REGULAR = "refused_not_a_regular_file"
INSTRUCTION_REFUSED_ESCAPES = "refused_escapes_workspace"
# Round-08 F-3 closed-status addition.
INSTRUCTION_REFUSED_HARDLINK = "refused_hardlink"

# The COMPLETE closed status set — the single source both the receipt
# scan and the prompt renderer (across the module boundary) enumerate.
# A new status added above without an entry in the renderer's explicit
# mapping fails a test (no silent default, round-08 F-1).
INSTRUCTION_STATUSES = (
    INSTRUCTION_READ,
    INSTRUCTION_ABSENT,
    INSTRUCTION_REFUSED_OVER_BOUND,
    INSTRUCTION_REFUSED_UNREADABLE,
    INSTRUCTION_REFUSED_NON_UTF8,
    INSTRUCTION_REFUSED_NOT_REGULAR,
    INSTRUCTION_REFUSED_ESCAPES,
    INSTRUCTION_REFUSED_HARDLINK,
)


def _prepare_turn_id():
    return "prep-" + secrets.token_hex(8)


def read_workspace_instruction(workspace_realpath, name):
    """The ONE hardened read of an allowlist entry (round-07 F-1/F-2).

    A symlinked, escaping, non-regular, or over-bound instruction
    file is refused BY CONSTRUCTION — the target repository is
    untrusted, so no read of a workspace-derived path may follow a
    link out of the lease or read past the hard bound:

    - ``os.open(..., O_RDONLY | O_NOFOLLOW | O_NONBLOCK)`` — ONE open,
      no stat-then-open race; a symlink at the final component raises
      (refused ``not a regular file``), so a hostile target committing
      ``AGENTS.md`` as a git mode-120000 symlink cannot leak the
      linked file's content. ``O_NONBLOCK`` (round-08 F-2) means a
      FIFO or device node opens instead of BLOCKING FOREVER (this path
      has no deadline by design), so the ``fstat`` regular-file check
      below actually runs and refuses it; the flag is a no-op for
      regular files;
    - ``os.fstat`` the DESCRIPTOR (not the path) and require a regular
      file, so a directory, FIFO, or device named ``AGENTS.md`` is
      refused honestly rather than mislabelled absent;
    - require ``st_nlink == 1`` (round-08 F-3): a git checkout never
      creates hardlinks, so an allowlisted instruction file with more
      than one link is anomalous and is refused — closing the only
      link-out-of-the-lease a path check cannot see (a hardlink to a
      file outside the workspace);
    - require the descriptor's resolved path to be inside the lease
      realpath (defense in depth against any residual escape);
    - read at most ``bound + 1`` bytes from that SAME descriptor and
      refuse when longer, so the bound binds the bytes ACTUALLY read
      — a file that grows between a stat and a read can never be read
      or rendered whole.

    Returns ``(status, byte_count, digest, text)``: ``text`` is a str
    only for ``read`` (None otherwise); ``byte_count``/``digest`` are
    exact for read and non-UTF-8, None for shapes with no measured
    bytes. Never returns partial content: an over-bound file yields
    the refusal status with no ``text``.
    """
    root = os.path.realpath(workspace_realpath)
    path = os.path.join(root, name)
    try:
        fd = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
    except OSError as exc:
        # ENOENT -> absent; ELOOP (symlink under O_NOFOLLOW) and
        # everything else that is not "regular file missing" ->
        # refused. A symlinked instruction file lands here (ELOOP)
        # and is REFUSED, never followed.
        import errno
        if exc.errno == errno.ENOENT:
            return INSTRUCTION_ABSENT, None, None, None
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            return INSTRUCTION_REFUSED_NOT_REGULAR, None, None, None
        return INSTRUCTION_REFUSED_UNREADABLE, None, None, None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return INSTRUCTION_REFUSED_NOT_REGULAR, None, None, None
        if info.st_nlink != 1:
            # A hardlink (round-08 F-3): a git checkout never creates
            # one, so >1 link is anomalous and may point outside the
            # lease. Refused with its own status.
            return INSTRUCTION_REFUSED_HARDLINK, None, None, None
        # BELT (round-07): the descriptor's resolved path must be
        # exactly this allowlist entry inside the lease. This is
        # UNREACHABLE given the primitive — O_NOFOLLOW refuses a
        # final-component symlink at open, and the allowlist names
        # (INSTRUCTION_FILE_NAMES) carry NO path separators, so no
        # intermediate component of root/name can be attacker-chosen;
        # root is itself realpath'd. Kept as defense in depth if the
        # allowlist ever gains a nested name, and driven directly by
        # test_containment_belt_refuses_an_escaping_resolution. The
        # invariant (separator-free names) is pinned by
        # test_instruction_names_carry_no_separators.
        resolved = os.path.realpath(os.path.join(root, name))
        if (
            os.path.dirname(resolved) != root
            or os.path.basename(resolved) != name
        ):
            return INSTRUCTION_REFUSED_ESCAPES, None, None, None
        # Read at most bound+1 from THIS descriptor; the bound binds
        # the bytes actually read (never a prior getsize), and the
        # read itself is capped so a huge file is never materialized
        # into memory. The exact read size is pinned by
        # test_read_is_capped_at_bound_plus_one.
        data = os.read(fd, MAX_INSTRUCTION_FILE_BYTES + 1)
    except OSError:
        return INSTRUCTION_REFUSED_UNREADABLE, None, None, None
    finally:
        os.close(fd)
    if len(data) > MAX_INSTRUCTION_FILE_BYTES:
        # We read bound+1 and there was at least that much, so the
        # file exceeds the bound; the descriptor's st_size is the
        # measured size (informational — the refusal DECISION is on
        # the bytes actually read). No content is returned.
        return (
            INSTRUCTION_REFUSED_OVER_BOUND, info.st_size, None, None
        )
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return INSTRUCTION_REFUSED_NON_UTF8, len(data), digest, None
    return INSTRUCTION_READ, len(data), digest, text


def discover_instructions(entry, now, turn_id_factory=None):
    """Scan the leased workspace; return E-5 preparation receipts.

    Returns ``(receipts, refused)`` where ``refused`` lists files
    present but over the byte bound (name + exact size), reported
    honestly rather than silently skipped.
    """
    make_turn_id = turn_id_factory or _prepare_turn_id
    workspace_path = entry["workspace_lease"]["path_realpath"]
    receipts = []
    refused = []
    for name in INSTRUCTION_FILE_NAMES[:MAX_INSTRUCTION_FILES]:
        # SAME hardened primitive as instruction_context (round-07
        # F-1/F-2 structural closure): a symlinked, escaping,
        # non-regular, or over-bound instruction file is never
        # followed, read whole, or digested. A receipt is written
        # ONLY for a genuinely read regular file, so a preparation
        # receipt can never account for content the validation turn
        # would then be refused.
        status, byte_count, digest, text = read_workspace_instruction(
            workspace_path, name
        )
        if status == INSTRUCTION_ABSENT:
            continue
        if status != INSTRUCTION_READ:
            refused.append(
                "%s — %s (hard bound %d bytes; refused, not read)"
                % (name, status, MAX_INSTRUCTION_FILE_BYTES)
            )
            continue
        receipts.append({
            "kind": "preparation",
            "turn_id": make_turn_id(),
            "recorded_at": now,
            "digest": digest,
            "bounded_summary": "instruction file %s (%d bytes, exact)"
            % (name, byte_count),
        })
    return receipts, refused


def receipt_instruction_name(receipt):
    """The instruction file name a preparation receipt records, or
    None for receipts that are not instruction receipts. Parses the
    exact summary format ``discover_instructions`` emits; the names
    come from the closed allowlist."""
    summary = receipt.get("bounded_summary", "")
    prefix = "instruction file "
    if receipt.get("kind") != "preparation" or not summary.startswith(
        prefix
    ):
        return None
    return summary[len(prefix):].split(" (")[0]


def instruction_context(entry):
    """The ACTUAL bounded instruction content for handoff validation.

    Reads the SAME fixed allowlist from the (already verified) leased
    workspace, with exact, honest per-file accounting. Returns a list
    of dicts, one per allowlisted name in order:
    ``{"name", "status", "byte_count", "digest", "text"}`` where
    ``text`` is present ONLY for status ``read`` (strict UTF-8;
    an empty file reads as "" with byte_count 0), ``byte_count`` and
    ``digest`` are exact for everything that was actually read or
    measured, and every refusal names its own status — nothing is
    silently truncated, lossily decoded, or presented as complete
    when it is not. No workspace path, lease id, capability, nonce,
    or environment value enters the result (names are RELATIVE
    allowlist members; unreadable files carry no OS error text).
    """
    workspace_path = entry["workspace_lease"]["path_realpath"]
    context = []
    for name in INSTRUCTION_FILE_NAMES[:MAX_INSTRUCTION_FILES]:
        status, byte_count, digest, text = read_workspace_instruction(
            workspace_path, name
        )
        item = {
            "name": name,
            "status": status,
            "byte_count": byte_count,
            "digest": digest,
        }
        if status == INSTRUCTION_READ:
            item["text"] = text
        context.append(item)
    return context
