"""Deterministic pull-request title, body, and revision message.

Every input is a durable field of the validated PR Delivery
Authorization: the human-recorded objective, architecture notes, and
nonblocking risks; the exact candidate entries (actual changed scope);
the recorded independent verification (command, exit status, log
digest); the recorded canonical Reviewer APPROVE (task, round, artifact
digest); and the recorded engineering completion. Nothing is fabricated,
nothing comes from a transcript, and the rendering carries no model
provenance and no co-author attribution of any kind — the static suite
scans this module and a behavioral test scans the rendered text for
such tokens.

The same function always yields the same bytes for the same record, so
the PR_CREATE receipt can bind ``title_sha256``/``body_sha256`` and the
reconciliation after a crash can prove the text it would have sent.
"""

from workflow_authority.digest import text_digest

from pr_delivery.authorization import (
    MAX_PR_BODY_CHARS,
    MAX_PR_TITLE_CHARS,
)

_STATUS_WORDS = {"A": "added", "M": "modified", "D": "deleted"}
# How many changed paths the body lists before an exact "and N more".
_LISTED_PATHS = 200


def title(record):
    text = record["pr_content"]["title"].strip()
    return text[:MAX_PR_TITLE_CHARS]


def _section(heading, text):
    text = text.strip()
    if not text:
        return []
    return ["## " + heading, "", text, ""]


def body(record):
    """The PR body; bounded, with an explicit truncation line if the
    listed scope had to be cut (the exact count is always stated)."""
    content = record["pr_content"]
    candidate = record["candidate"]
    evidence = record["evidence"]
    verification = evidence["independent_verification"]
    review = evidence["reviewer_approve"]
    engineering = evidence["engineering_complete"]
    lines = []
    lines.extend(_section("Objective", content["objective"]))
    lines.append("## Changed scope")
    lines.append("")
    lines.append(
        "%d path(s) changed relative to base `%s`; candidate identity"
        " `%s`." % (
            candidate["entry_count"],
            record["original_baseline"]["commit_sha"],
            candidate["identity_digest_sha256"],
        )
    )
    lines.append("")
    for entry in candidate["entries"][:_LISTED_PATHS]:
        lines.append("- %s `%s` (%s)" % (
            _STATUS_WORDS[entry["status"]], entry["path"], entry["mode"],
        ))
    remaining = candidate["entry_count"] - _LISTED_PATHS
    if remaining > 0:
        lines.append("- and %d more path(s) not listed here" % remaining)
    lines.append("")
    lines.append("## Validation evidence")
    lines.append("")
    lines.append(
        "- Independent verification command: `%s`; exit status %d; log"
        " sha256 `%s` (%d bytes)." % (
            " ".join(verification["command_argv"]),
            verification["exit_status"], verification["log_sha256"],
            verification["log_bytes"],
        )
    )
    lines.append(
        "- Engineering task `%s` reached %s." % (
            engineering["task_id"], engineering["status"],
        )
    )
    lines.append(
        "- Canonical Reviewer decision: %s in round %d (artifact `%s`,"
        " sha256 `%s`)." % (
            review["decision"], review["round"],
            review["review_file_name"], review["review_file_sha256"],
        )
    )
    lines.append("")
    lines.extend(_section("Architecture notes", content["architecture_notes"]))
    lines.extend(_section("Known nonblocking risks",
                          content["nonblocking_risks"]))
    text = "\n".join(lines).rstrip() + "\n"
    if len(text) > MAX_PR_BODY_CHARS:
        marker = "\n[body truncated to the %d-character bound]\n" % (
            MAX_PR_BODY_CHARS
        )
        text = text[:MAX_PR_BODY_CHARS - len(marker)] + marker
    return text


def revision_message(record):
    """The commit message: title, blank line, objective (bounded by the
    same body bound). Deterministic like the body."""
    objective = record["pr_content"]["objective"].strip()
    text = title(record) + "\n"
    if objective:
        text += "\n" + objective + "\n"
    return text[:MAX_PR_BODY_CHARS]


def title_digest(record):
    return text_digest(title(record))


def body_digest(record):
    return text_digest(body(record))


def message_digest(record):
    return text_digest(revision_message(record))
