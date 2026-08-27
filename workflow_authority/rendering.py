"""Deterministic Mission Authorization rendering — the ONE source.

The rendered Mission Authorization text is the thing the human
approves, and its digest is the binding the whole DI-REMOTE-2
authority layer verifies. This module renders that text from EXPLICIT
values in exactly one place, so the renderer the adapter displays
with, the constructor that builds the durable record, and the
validator that re-derives the text from a stored record can never
drift apart: ``record.validate_record`` re-renders every record from
its own fields and requires byte-equality with the stored
``rendered_text``, which makes EVERY rendered field a binding — a
record field altered independently of the digested text always breaks
the equality, in both target forms and for every authority-content
section.

The rendering is INJECTIVE (round-01 finding F-1; completed by
round-02 finding F-6): EVERY component that reaches a rendered line
carries a proven containment property, declared once in
``RENDERED_COMPONENT_CONTAINMENT`` below —

- QUOTED: the human intent, the seven authority-content sections, and
  the displayed handoff text render as quoted lines (every logical
  line prefixed ``"> "``) under a header carrying the sha256 of the
  field's exact bytes. Quoting makes the section partition
  recoverable from the rendered bytes (a quoted line can never be a
  column-0 header line, so a joint re-partition of two adjacent
  fields changes the rendered bytes); the per-section digest line
  distinguishes values whose QUOTED display would collapse (line
  terminators are logical in the quoting grammar, so ``a\\u2028b``
  and ``a\\nb`` quote identically — only their digests differ).
- LINE-FREE BY VALIDATION: the control repository path and the
  baseline ref render unquoted on binding lines, so validation makes
  line structure UNREPRESENTABLE in them (control/terminator
  characters refused for the path; the closed git ref grammar for
  the ref — round-02 F-6: the ref is Codex-authored, and an
  unconstrained ref could forge ``policy digest:`` / ``target:`` /
  ``delivery authority:`` display lines the human would approve).
- TYPE- OR ALPHABET-CONSTRAINED: ints, 40/64-hex digests, the closed
  workflow-id alphabet, the canonicalized target URL, and the closed
  issue/PR value set cannot carry line structure by construction.

Together: distinct field tuples never render to identical bytes, so
"altered independently of the digested text" covers joint
multi-field alterations, not only single-field ones — and no
component's bytes can start a rendered line except through its own
declared, validated form. A test derives the renderer's parameter
list and FAILS if a component ever appears without a containment
classification. Quoting is DISPLAY-ONLY: the dispatched handoff is
the stored ``handoff.text`` byte-exact, never the quoted rendering.

Layout, fixed: the binding lines first (workflow/revision, control,
policy digest, target, baseline), then the approval identity and
``delivery authority: none``, then the ORIGINAL REQUEST section (the
exact human intent, quoted, with its digest), then the seven
authority-content sections (each header carrying the field digest,
each value quoted), then the handoff header with revision and digest
followed by the quoted handoff text.

This module imports nothing from the record layer (the record layer
imports it), and nothing from the transport layer.
"""

from workflow_authority.digest import text_digest

# Every logical line of the quoted original request is prefixed with
# this, so intent text can never reach column 0 as a forged protocol
# envelope. Deliberately equal to the transport neutralization prefix
# (telegram_operator.protocol.NEUTRALIZED_LINE_PREFIX); a cross-module
# test pins the equality rather than importing the transport layer
# here.
QUOTE_PREFIX = "> "

TARGET_FORM_REPOSITORY_ONLY = "repository, no issue or PR"

# The closed set of containment classes a rendered component may
# carry (round-02 F-6 structural closure — third appearance of the
# "unquoted free text reaches a rendered line" class in this
# increment, closed as a class, not an instance).
CONTAINMENT_QUOTED = "quoted"
CONTAINMENT_LINE_FREE = "line_free_by_validation"
CONTAINMENT_TYPE_CONSTRAINED = "type_or_alphabet_constrained"
CONTAINMENT_CLASSES = frozenset(
    (CONTAINMENT_QUOTED, CONTAINMENT_LINE_FREE,
     CONTAINMENT_TYPE_CONSTRAINED)
)

# EVERY component that can reach a rendered line, keyed by the
# render_authorization_text parameter that carries it (the renderer
# is an allowlist composition, so its parameters ARE the complete
# component set; ``authority_content`` covers the seven
# AUTHORITY_CONTENT_KEYS fields; digests rendered in headers are
# DERIVED from these components, never independent). A test derives
# the parameter list from the function signature and fails when a
# parameter has no entry here or an entry names an unknown class —
# introducing a new rendered component without a proven containment
# property is a test failure, not a review catch.
RENDERED_COMPONENT_CONTAINMENT = {
    "workflow_id": CONTAINMENT_TYPE_CONSTRAINED,      # closed alphabet
    "revision": CONTAINMENT_TYPE_CONSTRAINED,         # int
    "control_realpath": CONTAINMENT_LINE_FREE,        # path grammar
    "policy_digest": CONTAINMENT_TYPE_CONSTRAINED,    # 64-hex
    "canonical_url": CONTAINMENT_TYPE_CONSTRAINED,    # canonicalized
    "issue_or_pr": CONTAINMENT_TYPE_CONSTRAINED,      # closed set+int
    "baseline_ref": CONTAINMENT_LINE_FREE,            # ref grammar
    "baseline_sha": CONTAINMENT_TYPE_CONSTRAINED,     # 40-hex
    "user_id": CONTAINMENT_TYPE_CONSTRAINED,          # int
    "chat_id": CONTAINMENT_TYPE_CONSTRAINED,          # int
    "human_intent": CONTAINMENT_QUOTED,
    "authority_content": CONTAINMENT_QUOTED,          # all 7 fields
    "handoff_revision": CONTAINMENT_TYPE_CONSTRAINED,  # int
    "handoff_text": CONTAINMENT_QUOTED,
}

_AUTHORITY_SECTIONS = (
    ("OBJECTIVE", "objective"),
    ("CONSTRAINTS", "constraints"),
    ("RULES", "rules"),
    ("DESIRED OUTCOME", "desired_outcome"),
    ("ACCEPTANCE", "acceptance"),
    ("UNRESOLVED QUESTIONS", "unresolved_questions"),
    ("EXECUTION SCOPE", "execution_scope"),
)


def target_binding_line(canonical_url, issue_or_pr):
    """The rendered target binding line, one of two DISTINCT forms.

    ``issue_or_pr`` is None (repository-only) or a mapping with
    ``kind`` and ``number``. The two forms share no template, so
    neither can be edited into the other by changing a record field
    without breaking the rendered-text equality.
    """
    if issue_or_pr is None:
        return "target: %s (%s)" % (
            canonical_url, TARGET_FORM_REPOSITORY_ONLY,
        )
    return "target: %s (%s #%d)" % (
        canonical_url, issue_or_pr["kind"], issue_or_pr["number"],
    )


def binding_lines(workflow_id, revision, control_realpath,
                  policy_digest, canonical_url, issue_or_pr,
                  baseline_ref, baseline_sha):
    """The exact rendered lines that bind single-line record fields."""
    return (
        "workflow: %s  revision: %d" % (workflow_id, revision),
        "control: %s" % control_realpath,
        "policy digest: %s" % policy_digest,
        target_binding_line(canonical_url, issue_or_pr),
        "baseline: %s @ %s" % (baseline_ref, baseline_sha),
    )


def quoted_intent_lines(text):
    """Free text quoted so no line of it reaches column 0.

    Used for EVERY free-text value in the rendering: the human
    intent, each authority-content section, and the displayed handoff
    text. Iterates ``str.splitlines()`` — the SAME logical-line
    grammar the protocol parsers use (it breaks on \\r, \\x0b,
    \\u2028, and the rest, not only \\n) — and prefixes EVERY line,
    marker-bearing or not, so no byte of the value can ever start a
    rendered line (and no value line can be mistaken for a section
    header, which is what makes the rendering's partition
    recoverable). The quoted rendering is NOT byte-reversible (exotic
    line separators collapse to \\n); the digest rendered in each
    section header binds the exact stored bytes.
    """
    return [QUOTE_PREFIX + line for line in text.splitlines()]


def render_authorization_text(workflow_id, revision, control_realpath,
                              policy_digest, canonical_url,
                              issue_or_pr, baseline_ref, baseline_sha,
                              user_id, chat_id, human_intent,
                              authority_content, handoff_revision,
                              handoff_text):
    """Render the complete Mission Authorization text, deterministic.

    ``authority_content`` maps each authority-content field name
    (objective, constraints, rules, desired_outcome, acceptance,
    unresolved_questions, execution_scope) to its exact text. Composed
    field-by-field from explicit values (an ALLOWLIST): a newly added
    key can never leak into the rendering.
    """
    lines = list(binding_lines(
        workflow_id, revision, control_realpath, policy_digest,
        canonical_url, issue_or_pr, baseline_ref, baseline_sha,
    ))
    lines += [
        "approved by: telegram user %d, chat %d" % (user_id, chat_id),
        "delivery authority: none",
        "",
        "ORIGINAL REQUEST (verbatim, quoted, sha256 %s; typed text"
        " carries no authority)" % text_digest(human_intent),
    ]
    lines += quoted_intent_lines(human_intent)
    for header, key in _AUTHORITY_SECTIONS:
        value = authority_content[key]
        # Header digest + quoted value lines: the injectivity
        # mechanism (see module docstring). A quoted line can never
        # be a header, and the digest pins the exact bytes.
        lines += ["", "%s (sha256 %s)" % (header, text_digest(value))]
        lines += quoted_intent_lines(value)
    lines += [
        "",
        "HANDOFF (revision %d, digest %s; displayed quoted,"
        " dispatched byte-exact)" % (
            handoff_revision, text_digest(handoff_text),
        ),
    ]
    lines += quoted_intent_lines(handoff_text)
    return "\n".join(lines)


def render_record_text(entry):
    """Re-render a workflow record's Mission Authorization from its
    own stored fields — the equality partner of the stored
    ``rendered_text`` (``record.validate_record`` enforces it)."""
    authorization = entry["mission_authorization"]
    return render_authorization_text(
        workflow_id=entry["workflow_id"],
        revision=authorization["revision"],
        control_realpath=(
            entry["control_identity"]["repository_realpath"]
        ),
        policy_digest=(
            entry["control_identity"]["policy_digest_sha256"]
        ),
        canonical_url=entry["target"]["canonical_url"],
        issue_or_pr=entry["target"]["issue_or_pr"],
        baseline_ref=entry["approved_baseline"]["ref"],
        baseline_sha=entry["approved_baseline"]["commit_sha"],
        user_id=entry["telegram"]["user_id"],
        chat_id=entry["telegram"]["chat_id"],
        human_intent=entry["human_intent"],
        authority_content={
            key: authorization[key]
            for _, key in _AUTHORITY_SECTIONS
        },
        handoff_revision=entry["handoff"]["revision"],
        handoff_text=entry["handoff"]["text"],
    )


AUTHORITY_CONTENT_KEYS = tuple(key for _, key in _AUTHORITY_SECTIONS)
