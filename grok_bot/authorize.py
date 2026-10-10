"""The local arming command: the ONLY writer of
approval commitments.

Reached only from ``grokbot.py ... authorize`` and ``authorize-delivery``,
which are local subcommands and never MCP tools: no tool, the adapter, the
MCP mapping and the server ever import this module (pinned by test). The
human runs it in Grok Bot's per-command, user-approved local shell, and the
command string they approve carries the FULL displayed binding (see
``grok_bot.arming.mission_arming_argv``). Everything is checked BEFORE
anything takes effect, inside ONE ``RequestIndex.serialized()`` critical
section (the same cross-process lock ``present`` and the fire paths hold):

1. every value equals, type- and order-exactly, the LATEST presentation
   receipt (any difference is refused by name,
   ``grok_bot_arming_not_displayed``, naming the fields). A presentation
   made between the human's display and this arming therefore never lets the
   newer proposal inherit the older consent: its receipt differs (its expiry
   alone is clock-derived), so arming is refused; and an arming made BEFORE a
   newer presentation commits to the older binding, which no fire against
   the newer receipt can reproduce;
2. the proposal behind it is still that one: the surface's current mission
   id, revision and proposal digest equal the receipt's, and the display
   re-rendered from the receipt's own binding has exactly the recorded
   display digest (an additional check, not a substitute for 1);
3. the displayed expiry has not passed.

Only then is a 128-bit nonce drawn, ``C`` over the full binding stored
(never the nonce), and the nonce printed once as the approval code. A
re-arming replaces the earlier commitment for the same request, and
markers of commitments that no longer exist are forgotten. What this is
NOT, and what it depends on: ``grok_bot.arming``.
"""

import json
import secrets

from workflow_authority.atomic import atomic_write_json
from workflow_authority.digest import text_digest

from grok_bot import adapter as adapter_module
from grok_bot import arming
from grok_bot import index as index_module

COMMITMENTS_TEMP_PREFIX = ".grok-bot-approval-commitments-"


def _refuse(problem, reason, **details):
    raise adapter_module.surface_module.LocalRequestRefusal(problem, reason,
                                                            **details)


def _not_displayed(fields, what):
    _refuse(arming.PROBLEM_ARMING_NOT_DISPLAYED,
            "the arming command's %s differs from the binding last displayed"
            " in %s; nothing was armed. Arm with exactly the values of the"
            " latest presentation" % (what, ", ".join(fields)), fields=fields)


def _store(directory, key, kind, digest, expires_at, now, live_index):
    """Write the one commitment for ``key`` (replacing an earlier one),
    dropping commitments past their expiry. The caller holds
    ``serialized()``."""
    arming.refuse_shared(directory)
    document = arming.load_commitments(directory)
    records = document["commitments"]
    for stale in [k for k, r in records.items() if r["expires_at"] <= now]:
        del records[stale]
    if key not in records and len(records) >= arming.MAX_COMMITMENTS:
        raise arming.CommitmentsError(
            "%d armed approvals are held; the hard bound is %d"
            % (len(records), arming.MAX_COMMITMENTS),
            arming.PROBLEM_COMMITMENTS_FULL)
    records[key] = {"kind": kind, "commitment_sha256": digest,
                    "expires_at": expires_at, "armed_at": now}
    arming.validate(document, arming.commitments_path(directory))
    atomic_write_json(directory, arming.commitments_path(directory), document,
                      temp_prefix=COMMITMENTS_TEMP_PREFIX)
    live_index.prune_consumption(set(
        r["commitment_sha256"] for r in records.values()))


def _armed(code, kind, expires_at, binding):
    return {"ok": True, "status": "armed", "kind": kind,
            "approval_code": code, "expires_at": expires_at, "binding": binding,
            "note": "Relay this approval_code with the human's separate"
                    " 'approved' reply. It fires exactly this binding, once,"
                    " before the expiry; DI stores only a one-way commitment"
                    " of it. Local arming is the approval boundary; the code"
                    " only completes it.",
            "delivery_authority": adapter_module.DELIVERY_AUTHORITY}


def arm_mission(surface, index, clock, request_ref, mission_id, revision,
                proposal_digest, action_scope, delivery_targets, expires_at,
                display_digest):
    """Arm the Mission approval displayed for ``request_ref``."""
    given = {"request_ref": request_ref, "mission_id": mission_id,
             "revision": revision, "proposal_digest_sha256": proposal_digest,
             "approved_action_scope": list(action_scope),
             "approved_delivery_targets": list(delivery_targets),
             "expires_at": expires_at}
    with index.serialized():
        receipt = index.presentation(request_ref)
        if receipt is None:
            _refuse(adapter_module.PROBLEM_NOT_PRESENTED,
                    "nothing was displayed for request %r; present it first."
                    " Nothing was armed" % (request_ref,))
        shown = receipt["binding"]
        differing = sorted(
            name for name in index_module.DISPLAYED_BINDING_KEYS
            if not adapter_module._same_as_shown(given[name], shown[name]))
        if display_digest != receipt["display_digest_sha256"]:
            differing.append("display_digest_sha256")
        if differing:
            _not_displayed(differing, "binding")
        now = clock()
        if now >= shown["expires_at"]:
            _refuse(arming.PROBLEM_ARMING_EXPIRED,
                    "the displayed approval expired at %d; present it again."
                    " Nothing was armed" % shown["expires_at"])
        presented = surface.present(request_ref)
        current = [name for name in ("mission_id", "revision",
                                     "proposal_digest_sha256")
                   if presented[name] != shown[name]]
        if current:
            _not_displayed(current, "proposal (it changed after display)")
        text = adapter_module.display_text(presented, shown)
        if text_digest(text) != receipt["display_digest_sha256"]:
            _not_displayed(["display_digest_sha256"],
                           "display (the proposal re-renders differently)")
        code = secrets.token_bytes(arming.NONCE_BYTES).hex()
        digest = arming.commitment(arming.mission_preimage(
            shown, receipt["display_digest_sha256"], code))
        _store(index.directory, arming.mission_key(request_ref),
               arming.KIND_MISSION, digest, shown["expires_at"], now, index)
    return _armed(code, arming.KIND_MISSION, shown["expires_at"],
                  json.loads(json.dumps(shown)))


def arm_delivery(index, clock, proposal_digest, expires_at, display_digest,
                 repository, configured_repository, workspaces_root):
    """Arm the delivery approval displayed under ``proposal_digest``."""
    from grok_bot import delivery as delivery_module
    with index.serialized():
        receipt = index.delivery_presentation(proposal_digest)
        if receipt is None:
            _refuse(adapter_module.PROBLEM_NOT_PRESENTED,
                    "no delivery proposal %r was displayed; present it first."
                    " Nothing was armed" % (proposal_digest,))
        proposal = receipt["proposal"]
        realpath = delivery_module.bound_repository(
            proposal, configured_repository, workspaces_root)
        differing = []
        if not adapter_module._same_as_shown(expires_at, proposal["expires_at"]):
            differing.append("expires_at")
        if display_digest != receipt["display_digest_sha256"]:
            differing.append("display_digest_sha256")
        if repository != realpath:
            differing.append("repository")
        if differing:
            _not_displayed(differing, "delivery binding")
        now = clock()
        if now >= proposal["expires_at"]:
            _refuse(arming.PROBLEM_ARMING_EXPIRED,
                    "the displayed delivery approval expired; present it"
                    " again. Nothing was armed")
        text = delivery_module.display_text(
            {"delivery_proposal": proposal,
             "proposal_digest_sha256": proposal_digest})
        if text_digest(text) != receipt["display_digest_sha256"]:
            _not_displayed(["display_digest_sha256"],
                           "display (the proposal re-renders differently)")
        code = secrets.token_bytes(arming.NONCE_BYTES).hex()
        digest = arming.commitment(arming.delivery_preimage(
            proposal_digest, proposal["expires_at"],
            receipt["display_digest_sha256"], realpath, code))
        _store(index.directory, arming.delivery_key(proposal_digest),
               arming.KIND_DELIVERY, digest, proposal["expires_at"], now, index)
    return _armed(code, arming.KIND_DELIVERY, proposal["expires_at"],
                  {"proposal_digest_sha256": proposal_digest,
                   "expires_at": proposal["expires_at"],
                   "repository": realpath})
