"""The SEPARATE delivery ceremony, relayed from Grok Bot: pr_delivery's own
``present-dots`` then ``attest-dots``, unchanged, and its read-only status.

What this module does, and nothing more:

- ``approved_repository`` (and ``bound_repository``, for a presented
  proposal or a delivery record) refuses every repository that is not, by
  filesystem shape, the configured approved repository or one of its
  worktrees directly under the configured workspaces root, named by its
  realpath. Every delivery tool passes through it; unconfigured, every one
  is refused before pr_delivery is reached. When it runs differs by tool:
  ``present_delivery`` and ``approve_delivery`` check BEFORE their ceremony
  (and so before any Git process); ``delivery_status`` first loads the
  delivery's record from pr_delivery's store, because only the record names
  the repository, then checks, then projects the status. It reads shape
  (realpaths, the ``.git`` entry, Git's ``gitdir`` and ``commondir`` pointer
  files), not Git's own resolution; what that does not exclude is stated
  where it is defined.
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
- ``status`` loads one delivery's record through ``PrDeliveryBoundary``'s
  machine, checks that the record's repository is the approved identity
  (``bound_repository``), and only then projects its status.

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
external host, and a fetch writes local repository data. ``status`` runs no
ceremony and no Git: it reads pr_delivery's store for the record, plus the
``.git`` pointer files of the repository that record names (the identity
check); it opens no network connection and performs no delivery step.

Provenance: the relayed reply is OPERATOR-ATTESTED, not cryptographically
authenticated; pr_delivery records its residual risk on the authorization.
"""

import io
import json
import math
import os
import stat
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
# The configured, approved repository identity.
PROBLEM_NOT_CONFIGURED = "grok_bot_delivery_not_configured"
PROBLEM_REPOSITORY_NOT_APPROVED = "grok_bot_delivery_repository_not_approved"
GITDIR_PREFIX = "gitdir: "
MAX_POINTER_BYTES = 4096
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


# -- the approved repository identity --------------------------------------
#
# Decided on the FILESYSTEM ALONE: lstat, realpath and Git's small pointer
# files (``gitdir``, ``commondir``), read without following a final
# symlink. Nothing here starts a process, opens a socket or writes. For
# ``present_delivery`` and ``approve_delivery`` it runs before the ceremony,
# so before any Git process; ``delivery_status`` must load the record from
# pr_delivery's store first, because the record names the repository.
#
# What this is, and is not: a check of filesystem SHAPE, laid out as Git
# lays out a repository and its linked worktrees. Shape is necessary, not
# sufficient: it is NOT Git's own resolution of the repository, it is not
# proof of repository identity, and it does not model every Git behaviour.
# Constructing any accepted shape needs write access to the approved
# repository's Git directory and the workspaces root, which a caller holding
# only the MCP bearer token, who can name paths but write none, does not
# have. Like the Git gates, this check is a workflow guardrail: it is not
# designed to contain processes running with the user's own privileges.
# What the ceremony then reads with Git (repository and Git directory
# realpaths, remotes, the exact candidate) is bound into the displayed
# proposal, and ``attest-dots`` re-reads it live and refuses any change.


def _not_approved(reason):
    _refuse(PROBLEM_REPOSITORY_NOT_APPROVED,
            "%s; delivery is accepted only for the configured approved"
            " repository or one of its worktrees directly under the configured"
            " workspaces root, named by its realpath. Nothing was read with"
            " Git and no ceremony ran" % reason)


def _pointer(path):
    """The single ``gitdir: TARGET`` line of the pointer file at ``path``
    (Git's worktree layout), or None: no file, a symlink or anything but a
    regular file, more than 4096 bytes, not UTF-8, a NUL, not exactly one
    line, or no target. Never follows a symlink at the final component."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        data = os.read(descriptor, MAX_POINTER_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    line = text.strip()
    if len(data) > MAX_POINTER_BYTES or "\x00" in text or not line or (
        "\n" in line or "\r" in line
    ):
        return None
    return line


def _pointer_target(base, path, prefix=""):
    """The realpath the pointer file at ``path`` names, resolved against
    ``base``, or None. A checkout's ``.git`` reads ``gitdir: TARGET``
    (``prefix`` GITDIR_PREFIX); an administrative ``gitdir`` or
    ``commondir`` is the bare path."""
    line = _pointer(path)
    if line is None or not line.startswith(prefix):
        return None
    target = line[len(prefix):].strip()
    return os.path.realpath(os.path.join(base, target)) if target else None


def _common_of(git_dir):
    """The common directory of the Git directory ``git_dir``, by Git's own
    layout rule: the target of its ``commondir`` file when it has one,
    otherwise itself. None when ``commondir`` exists but is unusable (a
    symlink, malformed, or naming no directory)."""
    pointer = os.path.join(git_dir, "commondir")
    if not os.path.lexists(pointer):
        return git_dir
    common = _pointer_target(git_dir, pointer)
    return common if common is not None and os.path.isdir(common) else None


def _common_dir(repository_realpath):
    """The configured repository's Git common directory, from its ``.git``
    entry (the directory itself, or the administrative directory a ``.git``
    file names) and then that Git directory's ``commondir``, as Git resolves
    it. None when it holds no usable Git directory."""
    dot_git = os.path.join(repository_realpath, ".git")
    try:
        mode = os.lstat(dot_git).st_mode
    except OSError:
        return None
    if stat.S_ISDIR(mode):
        git_dir = os.path.realpath(dot_git)
    else:
        git_dir = _pointer_target(repository_realpath, dot_git, GITDIR_PREFIX)
    if git_dir is None or not os.path.isdir(git_dir):
        return None
    return _common_of(git_dir)


def _is_worktree_of(checkout, common_dir):
    """Three ways, on Git's own pointer files (the shape of
    ``target_runtime.mission_workspace``'s pointer proof, without its Git
    confirmation): the checkout's ``.git`` file names one administrative
    directory directly under ``<common>/worktrees``; that directory's
    ``gitdir`` names this checkout's ``.git`` back; and its ``commondir``,
    from which Git takes the objects, refs and config the checkout uses,
    resolves to exactly ``common_dir`` (with none, the administrative
    directory would be its own repository, which is not the approved one)."""
    dot_git = os.path.join(checkout, ".git")
    admin = _pointer_target(checkout, dot_git, GITDIR_PREFIX)
    if admin is None or os.path.dirname(admin) != os.path.join(
        common_dir, "worktrees"
    ):
        return False
    if _pointer_target(admin, os.path.join(admin, "gitdir")) != dot_git:
        return False
    return _common_of(admin) == common_dir


def approved_repository(repo, configured_repository, workspaces_root):
    """``repo`` when, by filesystem shape, it is the configured, approved
    repository, or one of its worktrees directly under the configured
    workspaces root; otherwise a refusal. ``repo`` must be its own realpath:
    no symlink, ``.`` or ``..`` component and no trailing separator, so the
    name checked is the name pr_delivery receives. A refusal never echoes
    what a path resolves to. Shape, not Git's resolution: see above for what
    this does not exclude."""
    if configured_repository is None:
        _refuse(PROBLEM_NOT_CONFIGURED,
                "delivery is not configured: no approved repository was given"
                " (--workspace-repository), so every delivery tool is refused."
                " Nothing was read with Git and no ceremony ran")
    if not isinstance(repo, str) or not os.path.isabs(repo):
        _not_approved("the repository is not an absolute path")
    if os.path.realpath(repo) != repo:
        _not_approved("the repository is not named by its own realpath (it"
                      " has a symlink, '.' or '..' component, or a trailing"
                      " separator)")
    approved = os.path.realpath(configured_repository)
    common = _common_dir(approved)
    if common is None:
        _not_approved("the configured repository holds no Git repository")
    if repo == approved:
        return repo
    if workspaces_root is not None and os.path.dirname(repo) == (
        os.path.realpath(workspaces_root)
    ) and _is_worktree_of(repo, common):
        return repo
    _not_approved("the repository is not the approved one")


def bound_repository(document, configured_repository, workspaces_root):
    """The repository a presented delivery proposal (``binding.repository
    .realpath``) or a delivery record (``repository.realpath``) binds,
    checked against the same identity as ``approved_repository``. One that
    binds none in pr_delivery's shape is refused."""
    if configured_repository is None:
        approved_repository(None, None, None)
    binding = document.get("binding", document) if isinstance(
        document, dict) else None
    repository = binding.get("repository") if isinstance(binding, dict) else None
    realpath = repository.get("realpath") if isinstance(
        repository, dict) else None
    if not isinstance(realpath, str):
        _not_approved("the delivery binds no repository")
    return approved_repository(realpath, configured_repository, workspaces_root)


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
        "To approve exactly this delivery, first arm it on the DI machine:"
        " run the arming command presented with it in your local shell,"
        " approving that command only if every value in it matches this"
        " delivery. It prints a one-time approval code.",
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


def status(delivery_id, configured_repository, workspaces_root):
    """Read-only: pr_delivery's status projection of one delivery, only for
    a delivery whose repository is the approved identity. Unconfigured, it
    is refused before pr_delivery's store is opened. Configured, the order is
    load the record (pr_delivery's store), check the repository it names
    (filesystem pointer files), then project: the check cannot precede the
    load, since only the record names the repository. No Git, no network,
    no step."""
    if configured_repository is None:
        approved_repository(None, None, None)

    def read():
        boundary = delivery_boundary.PrDeliveryBoundary(
            delivery_cli.build_machine())
        record = boundary.machine.load(delivery_id)
        bound_repository(record, configured_repository, workspaces_root)
        return delivery_boundary.project_status(record, boundary.machine.clock())
    return _ceremony(read)
