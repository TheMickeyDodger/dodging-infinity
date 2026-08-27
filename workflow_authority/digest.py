"""Pinned canonical serialization and digests for workflow authority.

Every digest in the DI-REMOTE-2 authority layer is computed here, so
the canonical form is defined exactly once:

- JSON values serialize with ``sort_keys=True``,
  ``separators=(",", ":")``, ``ensure_ascii=True`` and
  ``allow_nan=False``. Non-serializable input (NaN/Infinity included)
  is REFUSED rather than digested, and non-string object keys are
  refused explicitly because ``json.dumps`` would silently coerce them
  (``{1: "x"}`` and ``{"1": "x"}`` must never share a digest).
- Any digest over MORE THAN ONE input uses length-prefixed framing
  (a part count followed by an 8-byte big-endian length before every
  part), never raw concatenation: with concatenation a byte could move
  across the boundary between two inputs without changing the result.
- The policy digest (plan determination D-9) covers the control
  repository's authority documents ``AGENTS.md`` and
  ``OPERATOR_PROTOCOL.md`` as exact bytes plus the canonical form of
  the effective policy record, framed.
"""

import hashlib
import json
import os
import struct

AGENTS_DOCUMENT_NAME = "AGENTS.md"
OPERATOR_PROTOCOL_DOCUMENT_NAME = "OPERATOR_PROTOCOL.md"

_LENGTH_PREFIX_FORMAT = ">Q"


class DigestError(Exception):
    """Input cannot be digested; message is actionable."""


def _refuse_non_string_keys(value, location):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DigestError(
                    "canonical serialization refuses non-string object"
                    " key %r at %s: json.dumps would coerce it and two"
                    " different values could share a digest"
                    % (key, location)
                )
            _refuse_non_string_keys(item, "%s.%s" % (location, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _refuse_non_string_keys(item, "%s[%d]" % (location, index))


def canonical_json_bytes(value):
    """The pinned canonical JSON byte serialization of ``value``.

    Refuses (rather than approximates) anything that does not have an
    exact canonical form: NaN, Infinity, -Infinity, non-JSON types,
    and non-string object keys.
    """
    _refuse_non_string_keys(value, "$")
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DigestError(
            "value has no canonical JSON serialization (%s); refusing"
            " to emit a digest for it" % exc
        )
    return text.encode("ascii")


def sha256_hex(data):
    """sha256 hex digest of exact bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise DigestError(
            "sha256_hex digests exact bytes; got %s. Encode text"
            " explicitly so the byte form is never ambiguous"
            % type(data).__name__
        )
    return hashlib.sha256(bytes(data)).hexdigest()


def json_digest(value):
    """sha256 hex digest of the canonical JSON form of ``value``."""
    return sha256_hex(canonical_json_bytes(value))


def text_digest(text):
    """sha256 hex digest of exact UTF-8 text bytes.

    Used for single-text bindings (rendered Mission Authorization,
    handoff text), matching the existing plan-digest convention.
    """
    if not isinstance(text, str):
        raise DigestError(
            "text_digest digests a str; got %s" % type(text).__name__
        )
    return sha256_hex(text.encode("utf-8"))


def framed_digest(parts):
    """sha256 hex digest over multiple byte inputs, framed.

    The hash covers the part COUNT, then for every part an 8-byte
    big-endian length prefix followed by the part bytes. Two different
    splits of the same concatenated bytes therefore produce DIFFERENT
    digests: a byte can never move across a part boundary unnoticed.
    """
    part_list = list(parts)
    hasher = hashlib.sha256()
    hasher.update(struct.pack(_LENGTH_PREFIX_FORMAT, len(part_list)))
    for index, part in enumerate(part_list):
        if not isinstance(part, (bytes, bytearray)):
            raise DigestError(
                "framed_digest part %d must be bytes; got %s. Encode"
                " text explicitly before framing"
                % (index, type(part).__name__)
            )
        part_bytes = bytes(part)
        hasher.update(
            struct.pack(_LENGTH_PREFIX_FORMAT, len(part_bytes))
        )
        hasher.update(part_bytes)
    return hasher.hexdigest()


def policy_digest(agents_document_bytes, operator_protocol_bytes,
                  effective_policy):
    """The D-9 policy digest, framed over its three inputs.

    Inputs are the EXACT bytes of the two control authority documents
    plus the effective policy record (canonically serialized). Framing
    means editing either document, or the policy, or moving bytes
    between them, always changes the digest.
    """
    for label, document in (
        (AGENTS_DOCUMENT_NAME, agents_document_bytes),
        (OPERATOR_PROTOCOL_DOCUMENT_NAME, operator_protocol_bytes),
    ):
        if not isinstance(document, (bytes, bytearray)):
            raise DigestError(
                "policy digest input %s must be exact bytes; got %s"
                % (label, type(document).__name__)
            )
    return framed_digest(
        [
            bytes(agents_document_bytes),
            bytes(operator_protocol_bytes),
            canonical_json_bytes(effective_policy),
        ]
    )


# The canonical effective-policy record for the D-9 policy digest.
# Changing ANY entry changes every policy digest and therefore fails
# closed every workflow authorized under the old policy — that is the
# intended drift behavior, and changing this record is a deliberate,
# reviewed act.
EFFECTIVE_POLICY_RECORD = {
    "policy_version": 1,
    "delivery_authority": "none",
    "policy_documents": [
        AGENTS_DOCUMENT_NAME,
        OPERATOR_PROTOCOL_DOCUMENT_NAME,
    ],
}


def control_policy_digest(control_repository_realpath):
    """The live control repository's policy digest (D-9 binding).

    This is the digest a workflow record's
    ``control_identity.policy_digest_sha256`` must equal before any
    target work is performed for it; drift between authorization and
    use fails closed.
    """
    return compute_policy_digest(
        control_repository_realpath, EFFECTIVE_POLICY_RECORD
    )


def compute_policy_digest(control_repository_realpath, effective_policy):
    """Read the control repository's authority documents and digest.

    Reads ``AGENTS.md`` and ``OPERATOR_PROTOCOL.md`` as exact bytes
    from the control repository root. A missing or unreadable document
    fails closed: authorization must never be digested over a partial
    policy surface.
    """
    contents = []
    for name in (
        AGENTS_DOCUMENT_NAME,
        OPERATOR_PROTOCOL_DOCUMENT_NAME,
    ):
        path = os.path.join(control_repository_realpath, name)
        try:
            with open(path, "rb") as handle:
                contents.append(handle.read())
        except OSError as exc:
            raise DigestError(
                "control authority document %s could not be read (%s);"
                " refusing to compute a policy digest over a partial"
                " policy surface" % (path, exc)
            )
    return policy_digest(contents[0], contents[1], effective_policy)
