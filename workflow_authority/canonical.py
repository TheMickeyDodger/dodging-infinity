"""Safe canonicalization of GitHub target URLs — the ONE source.

Every consumer of a repository / issue / PR URL (the Mission
Authorization validator, the workflow record validator, and any later
runtime consumer) canonicalizes through this module; no second copy of
the logic may exist anywhere. The accepted grammar is deliberately
tiny — exactly

    https://github.com/<owner>/<repo>
    https://github.com/<owner>/<repo>/issues/<number>
    https://github.com/<owner>/<repo>/pull/<number>

with a closed name alphabet and a canonical decimal number — and
EVERYTHING else fails closed with its own distinct problem code:
malformed input, forbidden characters, percent-encoding, shell
metacharacters, wrong or confusable hosts (``github.com.evil.tld``,
userinfo ``@``, an explicit port, IDN/punycode, a non-lowercase
host), unsupported schemes (``file:``, ``ssh:``, ``git:``,
``javascript:``, scheme-relative ``//``), queries and fragments
(ambiguous second targets), traversal (``..``, encoded traversal,
null bytes, newlines), empty path segments (double or trailing
slashes), ``.git``-suffixed and otherwise substitutable references,
and any path shape outside the three accepted forms.

A canonicalized value is IDEMPOTENT: re-canonicalizing
``canonical_url`` (or ``repository_url``) yields the same components.
The checks run in a fixed order so each hostile input maps to one
stable problem code.

REPOSITORY IDENTITY (round-01 finding F-4, binding for I3/I5): the
canonical URL preserves the case the human approved and displayed,
but GitHub resolves owner/repo case-insensitively, so
``.../O/R`` and ``.../o/r`` are two canonical VALUES naming ONE
repository identity. Any cross-workflow comparison of target
identity must therefore use ``repository_identity_key`` /
``same_repository_identity`` — never string equality on the URL.
"""

import collections

CANONICAL_TARGET_HOST = "github.com"

# Hard bounds, never derived from input.
MAX_TARGET_URL_CHARS = 512
MAX_TARGET_NAME_CHARS = 100

_SCHEME_PREFIX = "https://"

_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789._-"
)

# Shell metacharacters refused outright wherever they appear: a URL
# is data, and one that could double as shell syntax is refused, not
# quoted.
_SHELL_CHARS = frozenset(";|&$`<>(){}[]'\"\\*!~^ \t")

ISSUE_SEGMENT = "issues"
PULL_SEGMENT = "pull"
KIND_ISSUE = "issue"
KIND_PR = "pr"

PROBLEM_URL_NOT_TEXT = "target_url_not_text"
PROBLEM_URL_TOO_LONG = "target_url_too_long"
PROBLEM_URL_CONTROL_CHARACTER = "target_url_control_character"
PROBLEM_URL_NON_ASCII = "target_url_non_ascii"
PROBLEM_URL_SHELL_CHARACTER = "target_url_shell_character"
PROBLEM_URL_PERCENT_ENCODED = "target_url_percent_encoded"
PROBLEM_URL_MULTI_TARGET = "target_url_multiple_targets"
PROBLEM_URL_SCHEME_RELATIVE = "target_url_scheme_relative"
PROBLEM_URL_UNSUPPORTED_SCHEME = "target_url_unsupported_scheme"
PROBLEM_URL_USERINFO = "target_url_userinfo"
PROBLEM_URL_PORT = "target_url_port"
PROBLEM_URL_PUNYCODE = "target_url_punycode_host"
PROBLEM_URL_HOST_CASE = "target_url_host_not_lowercase"
PROBLEM_URL_HOST_CONFUSABLE = "target_url_host_confusable"
PROBLEM_URL_WRONG_HOST = "target_url_wrong_host"
PROBLEM_URL_QUERY = "target_url_query"
PROBLEM_URL_FRAGMENT = "target_url_fragment"
PROBLEM_URL_TRAVERSAL = "target_url_traversal"
PROBLEM_URL_EMPTY_SEGMENT = "target_url_empty_path_segment"
PROBLEM_URL_BAD_NAME = "target_url_bad_name"
PROBLEM_URL_GIT_SUFFIX = "target_url_git_suffix"
PROBLEM_URL_PATH_SHAPE = "target_url_path_shape"
PROBLEM_URL_BAD_NUMBER = "target_url_bad_number"

CanonicalTarget = collections.namedtuple(
    "CanonicalTarget",
    ("host", "owner", "repo", "kind", "number", "repository_url",
     "canonical_url"),
)


class CanonicalizationError(Exception):
    """A target URL failed canonicalization; message is actionable."""

    def __init__(self, message, problem):
        super(CanonicalizationError, self).__init__(message)
        self.problem = problem


def _refuse(problem, message):
    raise CanonicalizationError(message, problem)


def _require_name(value, label):
    """One GitHub owner/repo path segment, closed alphabet."""
    if (
        not value
        or len(value) > MAX_TARGET_NAME_CHARS
        or any(ch not in _NAME_CHARS for ch in value)
        or value.startswith(".")
        or value.startswith("-")
    ):
        _refuse(
            PROBLEM_URL_BAD_NAME,
            "target URL %s segment %r is not a valid GitHub name"
            " (closed alphabet, at most %d characters, no leading"
            " '.' or '-')" % (label, value, MAX_TARGET_NAME_CHARS),
        )
    return value


def canonicalize_target_url(url):
    """Canonicalize one GitHub repository / issue / PR URL, or refuse.

    Returns a ``CanonicalTarget``; ``kind``/``number`` are None for
    the repository-only form. Raises CanonicalizationError with a
    distinct ``problem`` code on the first failing check; nothing is
    ever repaired, stripped, or case-folded on the caller's behalf —
    a value that is not already canonical is refused.
    """
    if not isinstance(url, str) or not url:
        _refuse(
            PROBLEM_URL_NOT_TEXT,
            "target URL must be a non-empty string, got %r" % (url,),
        )
    if len(url) > MAX_TARGET_URL_CHARS:
        _refuse(
            PROBLEM_URL_TOO_LONG,
            "target URL is %d characters; the hard bound is %d and"
            " the value is refused, not truncated"
            % (len(url), MAX_TARGET_URL_CHARS),
        )
    for ch in url:
        # Control characters cover null bytes, newlines, and every
        # other traversal-by-line or log-forgery vehicle.
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            _refuse(
                PROBLEM_URL_CONTROL_CHARACTER,
                "target URL contains control character %r; refused"
                % (ch,),
            )
        if ord(ch) > 0x7E:
            # Non-ASCII — raw IDN/homoglyph hosts included — has no
            # canonical byte form here; refused outright, with its
            # own code (round-01 finding F-5: this is not a control
            # character and must not be reported as one).
            _refuse(
                PROBLEM_URL_NON_ASCII,
                "target URL contains non-ASCII character %r (raw"
                " IDN/homoglyph forms included); refused" % (ch,),
            )
        if ch in _SHELL_CHARS:
            _refuse(
                PROBLEM_URL_SHELL_CHARACTER,
                "target URL contains shell metacharacter %r; a URL"
                " that could double as shell syntax is refused"
                % (ch,),
            )
    if "%" in url:
        _refuse(
            PROBLEM_URL_PERCENT_ENCODED,
            "target URL contains percent-encoding; an encoded form"
            " could smuggle traversal or a different identity, so"
            " only the literal canonical form is accepted",
        )
    if url.count("://") > 1:
        _refuse(
            PROBLEM_URL_MULTI_TARGET,
            "target URL names more than one target (multiple '://'"
            " occurrences); exactly one target is required",
        )
    if url.startswith("//"):
        _refuse(
            PROBLEM_URL_SCHEME_RELATIVE,
            "scheme-relative target URL is refused; only %r is"
            " accepted" % (_SCHEME_PREFIX,),
        )
    if not url.startswith(_SCHEME_PREFIX):
        _refuse(
            PROBLEM_URL_UNSUPPORTED_SCHEME,
            "target URL %r does not use the only supported scheme"
            " prefix %r (file:, ssh:, git:, javascript:, http:, and"
            " every other scheme are refused)" % (url, _SCHEME_PREFIX),
        )
    rest = url[len(_SCHEME_PREFIX):]
    if "?" in rest:
        _refuse(
            PROBLEM_URL_QUERY,
            "target URL carries a query string; a query could name a"
            " second target, so it is refused",
        )
    if "#" in rest:
        _refuse(
            PROBLEM_URL_FRAGMENT,
            "target URL carries a fragment; a fragment could name a"
            " second target, so it is refused",
        )
    authority, separator, path = rest.partition("/")
    if "@" in authority:
        _refuse(
            PROBLEM_URL_USERINFO,
            "target URL authority %r carries userinfo ('@'); a"
            " userinfo prefix can disguise the real host, so it is"
            " refused" % (authority,),
        )
    if ":" in authority:
        _refuse(
            PROBLEM_URL_PORT,
            "target URL authority %r carries an explicit port; only"
            " the default %r is accepted"
            % (authority, CANONICAL_TARGET_HOST),
        )
    if "xn--" in authority.lower():
        _refuse(
            PROBLEM_URL_PUNYCODE,
            "target URL host %r is punycode/IDN; refused" % (authority,),
        )
    if authority != CANONICAL_TARGET_HOST:
        if authority.lower() == CANONICAL_TARGET_HOST:
            _refuse(
                PROBLEM_URL_HOST_CASE,
                "target URL host %r is not lowercase; only the exact"
                " canonical %r is accepted"
                % (authority, CANONICAL_TARGET_HOST),
            )
        if authority.lower().startswith(CANONICAL_TARGET_HOST + "."):
            _refuse(
                PROBLEM_URL_HOST_CONFUSABLE,
                "target URL host %r is a confusable of %r (the real"
                " host would be the trailing suffix); refused"
                % (authority, CANONICAL_TARGET_HOST),
            )
        _refuse(
            PROBLEM_URL_WRONG_HOST,
            "target URL host %r is not the canonical %r"
            % (authority, CANONICAL_TARGET_HOST),
        )
    if not separator or not path:
        _refuse(
            PROBLEM_URL_PATH_SHAPE,
            "target URL carries no owner/repo path; a bare host names"
            " no repository",
        )
    segments = path.split("/")
    for segment in segments:
        if segment in (".", ".."):
            _refuse(
                PROBLEM_URL_TRAVERSAL,
                "target URL path contains the traversal segment %r;"
                " a reference that resolves elsewhere is refused"
                % (segment,),
            )
        if segment == "":
            _refuse(
                PROBLEM_URL_EMPTY_SEGMENT,
                "target URL path contains an empty segment (double or"
                " trailing slash); only the exact canonical form is"
                " accepted",
            )
    if len(segments) == 2:
        kind, number = None, None
    elif len(segments) == 4 and segments[2] in (
        ISSUE_SEGMENT, PULL_SEGMENT
    ):
        kind = (
            KIND_ISSUE if segments[2] == ISSUE_SEGMENT else KIND_PR
        )
        number_text = segments[3]
        if (
            not number_text.isdigit()
            or number_text != str(int(number_text))
            or int(number_text) < 1
        ):
            _refuse(
                PROBLEM_URL_BAD_NUMBER,
                "target URL %s number %r is not a canonical positive"
                " decimal (no leading zeros, no signs)"
                % (segments[2], number_text),
            )
        number = int(number_text)
    else:
        _refuse(
            PROBLEM_URL_PATH_SHAPE,
            "target URL path %r is not one of the accepted forms"
            " (owner/repo, owner/repo/issues/N, owner/repo/pull/N)"
            % (path,),
        )
    owner = _require_name(segments[0], "owner")
    repo = _require_name(segments[1], "repo")
    if repo.endswith(".git"):
        _refuse(
            PROBLEM_URL_GIT_SUFFIX,
            "target URL repo %r carries a '.git' suffix; that names"
            " the same repository under a second identity, so only"
            " the suffix-free canonical form is accepted" % (repo,),
        )
    repository_url = "%s%s/%s/%s" % (
        _SCHEME_PREFIX, CANONICAL_TARGET_HOST, owner, repo,
    )
    if kind is None:
        canonical_url = repository_url
    else:
        canonical_url = "%s/%s/%d" % (
            repository_url,
            ISSUE_SEGMENT if kind == KIND_ISSUE else PULL_SEGMENT,
            number,
        )
    return CanonicalTarget(
        host=CANONICAL_TARGET_HOST,
        owner=owner,
        repo=repo,
        kind=kind,
        number=number,
        repository_url=repository_url,
        canonical_url=canonical_url,
    )


def repository_identity_key(target):
    """The case-folded repository IDENTITY of a CanonicalTarget.

    GitHub resolves owner/repo case-insensitively, so two canonical
    URLs differing only in name case denote the same repository. The
    identity key case-folds owner and repo (the host is already
    pinned lowercase); the canonical URL itself keeps the exact case
    the human approved. Every cross-workflow target-identity
    comparison MUST use this key (or ``same_repository_identity``),
    never URL string equality.
    """
    return "%s/%s/%s" % (
        target.host, target.owner.casefold(), target.repo.casefold(),
    )


def same_repository_identity(first, second):
    """True when two CanonicalTargets name the same repository."""
    return repository_identity_key(first) == (
        repository_identity_key(second)
    )


def canonicalize_repository_url(url):
    """Canonicalize a REPOSITORY-only URL; issue/PR forms are refused.

    The workflow record's ``target.canonical_url`` names the
    repository identity alone (the issue or PR identity is a separate
    bound field), so a URL that smuggles an issue path where a
    repository is expected is refused with the path-shape code.
    """
    target = canonicalize_target_url(url)
    if target.kind is not None:
        _refuse(
            PROBLEM_URL_PATH_SHAPE,
            "expected a repository-only URL; %r names a %s"
            % (url, target.kind),
        )
    return target
