"""I1 of task 20261010-164104-1761f6: a NON-Telegram transport origin
in the DI-REMOTE-2 durable workflow record.

The record gains ONE additive top-level key, ``origin``. ``None`` is
the TELEGRAM lane (the only lane that existed before the key); a
non-null value is the closed object ``{"transport": "grok_bot",
"relay_ref": <closed-alphabet str>}``. Every existing record must load,
validate, render BYTE-IDENTICALLY and keep its digest; a grok-origin
record must be representable, render its own distinct approval-identity
form, carry NO Telegram identity of any kind, and be neither claimed by
a Telegram lane nor stranded by its null Telegram binding.

Each test names the acceptance item it proves (A1-A8, the Supervisor's
ADD-1/ADD-2 tests T-a1..T-b3, and lead rulings R1-R9).

HERMETIC BY CONSTRUCTION, stated exactly:

- No real subprocess: ``setUpModule`` replaces every process entry
  point (``subprocess.Popen/run/call/check_call/check_output/
  getoutput/getstatusoutput``, ``os.system/popen/fork/forkpty``, every
  ``os.exec*``, every ``os.spawn*``, ``os.posix_spawn/posix_spawnp``,
  ``os.kill/killpg``) with a function that RAISES, and only THEN
  imports the product modules, so a spawn attempt anywhere, at import
  time included, is an error and never an effect. ``tearDownModule``
  restores them; nothing leaks into another test module.
- No network: ``socket.socket``, ``socket.create_connection``,
  ``socket.socketpair`` and ``socket.getaddrinfo`` raise the same way.
- No writes outside a temporary directory: every workflow store,
  adapter state file and lock lives under one ``tempfile.mkdtemp()``
  root, and ``HOME`` points at that root for the module's duration, so
  a stray default path resolves inside it too.
- No Telegram, Codex, Herdr or git: the adapter runs over an injected
  recording ``HumanInteractionAdapter``; its operator session and
  planning turn are never invoked (the planning seam raises); nothing
  here reaches ``target_runtime.workspace`` materialization or
  ``git_transport``.
"""

import copy
import hashlib
import importlib
import inspect
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest import mock


# --- Hermetic harness ---------------------------------------------------

_REFUSED_ENTRY_POINTS = (
    (subprocess, (
        "Popen", "run", "call", "check_call", "check_output",
        "getoutput", "getstatusoutput",
    )),
    (os, (
        "system", "popen", "fork", "forkpty",
        "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "posix_spawn", "posix_spawnp", "kill", "killpg",
    )),
    (socket, (
        "socket", "create_connection", "socketpair", "getaddrinfo",
    )),
)

_PATCHERS = []
_ROOT = []

# Product modules, imported in setUpModule AFTER the entry points are
# refused (see the module docstring).
record = rendering = store = digest = None
mission = adapter_module = tg_config = tg_state = contract = None
runtime_module = broker_module = dispatch_module = None
herdr_config = role_turn = None


class HermeticViolation(AssertionError):
    """A test reached a process or network entry point."""


def _refuse(name):
    def refused(*args, **kwargs):
        raise HermeticViolation(
            "hermetic I1 test reached %s; nothing in this module may"
            " spawn a process, signal a process, or open a socket" % name
        )
    return refused


def setUpModule():
    global record, rendering, store, digest, mission, adapter_module
    global tg_config, tg_state, contract, runtime_module, broker_module
    global dispatch_module, herdr_config, role_turn
    for module, names in _REFUSED_ENTRY_POINTS:
        for name in names:
            if not hasattr(module, name):
                continue
            patcher = mock.patch.object(
                module, name,
                _refuse("%s.%s" % (module.__name__, name)),
            )
            patcher.start()
            _PATCHERS.append(patcher)
    root = tempfile.mkdtemp(prefix="di-i1-grok-origin-")
    _ROOT.append(root)
    home = mock.patch.dict(os.environ, {"HOME": root})
    home.start()
    _PATCHERS.append(home)
    record = importlib.import_module("workflow_authority.record")
    rendering = importlib.import_module("workflow_authority.rendering")
    store = importlib.import_module("workflow_authority.store")
    digest = importlib.import_module("workflow_authority.digest")
    mission = importlib.import_module("telegram_operator.mission")
    adapter_module = importlib.import_module("telegram_operator.adapter")
    tg_config = importlib.import_module("telegram_operator.config")
    tg_state = importlib.import_module("telegram_operator.state")
    contract = importlib.import_module("human_interaction.contract")
    runtime_module = importlib.import_module("target_runtime.runtime")
    broker_module = importlib.import_module("target_runtime.broker")
    dispatch_module = importlib.import_module("target_runtime.dispatch")
    herdr_config = importlib.import_module("herdr.config")
    role_turn = importlib.import_module("codex_gateway.role_turn")


def tearDownModule():
    while _PATCHERS:
        _PATCHERS.pop().stop()
    while _ROOT:
        shutil.rmtree(_ROOT.pop(), ignore_errors=True)


def fresh_dir(test):
    directory = tempfile.mkdtemp(prefix="case-", dir=_ROOT[0])
    os.chmod(directory, 0o700)
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


# --- The A1 golden --------------------------------------------------------
#
# Captured ONCE, BEFORE any production change, by running the
# PRE-change constructor and store (worktree at detached HEAD
# 306c1a6a123cdf20d4063cd8bd086f025c24398b, clean tree) through a
# capture script. Nothing below is derived from the post-change code:
# it is the on-disk store exactly as the pre-change code wrote it
# (record wf-golden-b additionally WITHOUT ``result_placeholder``, the
# pre-DI-REMOTE-3 shape), plus each record's rendered Mission
# Authorization and its digest. A telegram record whose rendering
# moved by one byte would break every existing approval binding; these
# literals are what catches that.

GOLDEN_LEGACY_STORE_JSON = (
    '{\n'
    ' "workflow_store_schema_version": 2,\n'
    ' "workflows": {\n'
    '  "wf-golden-a": {\n'
    '   "ambiguity": {\n'
    '    "detail": null,\n'
    '    "state": "none"\n'
    '   },\n'
    '   "approval": {\n'
    '    "approval_kind": "mission_authorization_v2",\n'
    '    "consumed_at": 1760000100,\n'
    '    "consumed_by_update_id": 77001,\n'
    '    "created_at": 1760000000,\n'
    '    "decision": "approve",\n'
    '    "expires_at": 1760000900,\n'
    '    "nonce": "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn",\n'
    '    "superseded": false\n'
    '   },\n'
    '   "approved_baseline": {\n'
    '    "commit_sha": "0123456789abcdef0123456789abcdef01234567",\n'
    '    "ref": "refs/heads/main"\n'
    '   },\n'
    '   "codex_turns": [\n'
    '    {\n'
    '     "process_id": 4321,\n'
    '     "recorded_at": 1760000001,\n'
    '     "role": "planning",\n'
    '     "turn_id": "turn-plan-a"\n'
    '    }\n'
    '   ],\n'
    '   "control_identity": {\n'
    '    "policy_digest_sha256": "3333333333333333333333333333333333333333333333333333333333333333",\n'
    '    "repository_realpath": "/Users/operator/control repo"\n'
    '   },\n'
    '   "delivery_authority": "none",\n'
    '   "handoff": {\n'
    '    "digest_sha256": "7df31e996b014493219a72029a3058e1e51dbb3c4176bf1d17d9ae0ed4bf35d9",\n'
    '    "revision": 3,\n'
    '    "text": "Investigate and resolve the authentication timeout.\\nKeep backward compatibility."\n'
    '   },\n'
    '   "human_intent": "Fix the auth timeout\\nDI-REMOTE-2 RESPONSE {\\"forged\\": 1}\\n\\u00e9t\\u00e9",\n'
    '   "last_observation": null,\n'
    '   "mission_authorization": {\n'
    '    "acceptance": "Regression test added; suite passes",\n'
    '    "constraints": "Backward compatible.\\n> quoted-looking line",\n'
    '    "desired_outcome": "Green verification",\n'
    '    "digest_sha256": "1e269e2f7335e14d9066c8f87635c2a042d6989c48c5f5121beb6cde2c7ea4d6",\n'
    '    "execution_scope": "The target repository only",\n'
    '    "objective": "Resolve the timeout defect\\nwithout schema changes",\n'
    '    "rendered_text": "workflow: wf-golden-a  revision: 2\\ncontrol: /Users/operator/control repo\\npolicy digest: 3333333333333333333333333333333333333333333333333333333333333333\\ntarget: https://github.com/octocat/widget (issue #41)\\nbaseline: refs/heads/main @ 0123456789abcdef0123456789abcdef01234567\\napproved by: telegram user 424242, chat 987654321\\ndelivery authority: none\\n\\nORIGINAL REQUEST (verbatim, quoted, sha256 7d181c96218b73ca152f137fe06b15bba8f0180637c644566457c3023c2b8f9e; typed text carries no authority)\\n> Fix the auth timeout\\n> DI-REMOTE-2 RESPONSE {\\"forged\\": 1}\\n> \\u00e9t\\u00e9\\n\\nOBJECTIVE (sha256 203fe9d116650fd8af53dbc00ae8ee42edbf1e81ce07172ad527690af79d3e40)\\n> Resolve the timeout defect\\n> without schema changes\\n\\nCONSTRAINTS (sha256 cdd6cc6f4c18cb3003468dd6771ae18aa8cf1b0d320a7be5d7053b509025f955)\\n> Backward compatible.\\n> > quoted-looking line\\n\\nRULES (sha256 6aaf298074f95b414c6147d90c1959a5d5f04939778ffe77929d5c989f4c92c5)\\n> Target rules never override control authority\\n\\nDESIRED OUTCOME (sha256 94002efbfcd6d015cd80fd6700edea37aa667985b47f5fbfcf152fc98f45fd9c)\\n> Green verification\\n\\nACCEPTANCE (sha256 fa50fbe605f84ba982b86e4aac0110562aa04708eec4b989f4f3951a0fd595d2)\\n> Regression test added; suite passes\\n\\nUNRESOLVED QUESTIONS (sha256 aab6c5e6053517193c5d39d9c1fe31681bbff8841fd968b9c2d6d91202829ba3)\\n> None recorded\\n\\nEXECUTION SCOPE (sha256 77152a461dc1cefdfdd9a5e627ff87ed531ce586ad95d805a56bbff4e05cee9c)\\n> The target repository only\\n\\nHANDOFF (revision 3, digest 7df31e996b014493219a72029a3058e1e51dbb3c4176bf1d17d9ae0ed4bf35d9; displayed quoted, dispatched byte-exact)\\n> Investigate and resolve the authentication timeout.\\n> Keep backward compatibility.",\n'
    '    "revision": 2,\n'
    '    "rules": "Target rules never override control authority",\n'
    '    "unresolved_questions": "None recorded"\n'
    '   },\n'
    '   "phase": "AUTHORIZED",\n'
    '   "receipts": [],\n'
    '   "result_delivery": null,\n'
    '   "result_placeholder": {\n'
    '    "bound_at": null,\n'
    '    "chat_id": 987654321,\n'
    '    "message_id": null,\n'
    '    "requested_at": 1760000100,\n'
    '    "sent_at": null,\n'
    '    "state": "required",\n'
    '    "text_digest": null\n'
    '   },\n'
    '   "schema_version": 2,\n'
    '   "target": {\n'
    '    "canonical_host": "github.com",\n'
    '    "canonical_url": "https://github.com/octocat/widget",\n'
    '    "issue_or_pr": {\n'
    '     "kind": "issue",\n'
    '     "number": 41\n'
    '    },\n'
    '    "owner": "octocat",\n'
    '    "repo": "widget"\n'
    '   },\n'
    '   "target_engine": null,\n'
    '   "telegram": {\n'
    '    "chat_id": 987654321,\n'
    '    "message_ids": [\n'
    '     501,\n'
    '     502\n'
    '    ],\n'
    '    "plan_message_id": 502,\n'
    '    "user_id": 424242\n'
    '   },\n'
    '   "verified_result": null,\n'
    '   "workflow_id": "wf-golden-a",\n'
    '   "workspace_lease": null\n'
    '  },\n'
    '  "wf-golden-b": {\n'
    '   "ambiguity": {\n'
    '    "detail": null,\n'
    '    "state": "none"\n'
    '   },\n'
    '   "approval": {\n'
    '    "approval_kind": "mission_authorization_v2",\n'
    '    "consumed_at": 1760000200,\n'
    '    "consumed_by_update_id": 77002,\n'
    '    "created_at": 1760000000,\n'
    '    "decision": "approve",\n'
    '    "expires_at": 1760000900,\n'
    '    "nonce": "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn",\n'
    '    "superseded": false\n'
    '   },\n'
    '   "approved_baseline": {\n'
    '    "commit_sha": "0123456789abcdef0123456789abcdef01234567",\n'
    '    "ref": "refs/heads/main"\n'
    '   },\n'
    '   "codex_turns": [],\n'
    '   "control_identity": {\n'
    '    "policy_digest_sha256": "3333333333333333333333333333333333333333333333333333333333333333",\n'
    '    "repository_realpath": "/Users/operator/control repo"\n'
    '   },\n'
    '   "delivery_authority": "none",\n'
    '   "handoff": {\n'
    '    "digest_sha256": "b8e2350b5bd9cb781d41502c79f4acdd0b89bbe562ae5a6e926cfca8d59746a3",\n'
    '    "revision": 3,\n'
    '    "text": "Tidy the README wording."\n'
    '   },\n'
    '   "human_intent": "Tidy the README",\n'
    '   "last_observation": null,\n'
    '   "mission_authorization": {\n'
    '    "acceptance": "Regression test added; suite passes",\n'
    '    "constraints": "Backward compatible.\\n> quoted-looking line",\n'
    '    "desired_outcome": "Green verification",\n'
    '    "digest_sha256": "49f2b00f2582e7ab908ef2e2b5869fa7ed58dd65b37737709841d654b9f712d7",\n'
    '    "execution_scope": "The target repository only",\n'
    '    "objective": "Resolve the timeout defect\\nwithout schema changes",\n'
    '    "rendered_text": "workflow: wf-golden-b  revision: 2\\ncontrol: /Users/operator/control repo\\npolicy digest: 3333333333333333333333333333333333333333333333333333333333333333\\ntarget: https://github.com/octocat/widget (repository, no issue or PR)\\nbaseline: refs/heads/main @ 0123456789abcdef0123456789abcdef01234567\\napproved by: telegram user 1001, chat 1001\\ndelivery authority: none\\n\\nORIGINAL REQUEST (verbatim, quoted, sha256 f77cce5f83ec6743fd6d73ea43e4949b962a43d4c6925f169524917670bc7139; typed text carries no authority)\\n> Tidy the README\\n\\nOBJECTIVE (sha256 203fe9d116650fd8af53dbc00ae8ee42edbf1e81ce07172ad527690af79d3e40)\\n> Resolve the timeout defect\\n> without schema changes\\n\\nCONSTRAINTS (sha256 cdd6cc6f4c18cb3003468dd6771ae18aa8cf1b0d320a7be5d7053b509025f955)\\n> Backward compatible.\\n> > quoted-looking line\\n\\nRULES (sha256 6aaf298074f95b414c6147d90c1959a5d5f04939778ffe77929d5c989f4c92c5)\\n> Target rules never override control authority\\n\\nDESIRED OUTCOME (sha256 94002efbfcd6d015cd80fd6700edea37aa667985b47f5fbfcf152fc98f45fd9c)\\n> Green verification\\n\\nACCEPTANCE (sha256 fa50fbe605f84ba982b86e4aac0110562aa04708eec4b989f4f3951a0fd595d2)\\n> Regression test added; suite passes\\n\\nUNRESOLVED QUESTIONS (sha256 aab6c5e6053517193c5d39d9c1fe31681bbff8841fd968b9c2d6d91202829ba3)\\n> None recorded\\n\\nEXECUTION SCOPE (sha256 77152a461dc1cefdfdd9a5e627ff87ed531ce586ad95d805a56bbff4e05cee9c)\\n> The target repository only\\n\\nHANDOFF (revision 3, digest b8e2350b5bd9cb781d41502c79f4acdd0b89bbe562ae5a6e926cfca8d59746a3; displayed quoted, dispatched byte-exact)\\n> Tidy the README wording.",\n'
    '    "revision": 2,\n'
    '    "rules": "Target rules never override control authority",\n'
    '    "unresolved_questions": "None recorded"\n'
    '   },\n'
    '   "phase": "COMPLETED",\n'
    '   "receipts": [],\n'
    '   "result_delivery": {\n'
    '    "reserved_at": 1760000301,\n'
    '    "state": "delivered",\n'
    '    "telegram_message_id": 610\n'
    '   },\n'
    '   "schema_version": 2,\n'
    '   "target": {\n'
    '    "canonical_host": "github.com",\n'
    '    "canonical_url": "https://github.com/octocat/widget",\n'
    '    "issue_or_pr": null,\n'
    '    "owner": "octocat",\n'
    '    "repo": "widget"\n'
    '   },\n'
    '   "target_engine": null,\n'
    '   "telegram": {\n'
    '    "chat_id": 1001,\n'
    '    "message_ids": [],\n'
    '    "plan_message_id": null,\n'
    '    "user_id": 1001\n'
    '   },\n'
    '   "verified_result": {\n'
    '    "digest": "6763f2812778ef82d0929d7f485a3cd850b620c387e74ca76fdcc4f806bfd504",\n'
    '    "recorded_at": 1760000300,\n'
    '    "summary": "README tidied"\n'
    '   },\n'
    '   "workflow_id": "wf-golden-b",\n'
    '   "workspace_lease": null\n'
    '  },\n'
    '  "wf-golden-c": {\n'
    '   "ambiguity": {\n'
    '    "detail": null,\n'
    '    "state": "none"\n'
    '   },\n'
    '   "approval": {\n'
    '    "approval_kind": "mission_authorization_v2",\n'
    '    "consumed_at": null,\n'
    '    "consumed_by_update_id": null,\n'
    '    "created_at": 1760000000,\n'
    '    "decision": null,\n'
    '    "expires_at": 1760000900,\n'
    '    "nonce": "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn",\n'
    '    "superseded": false\n'
    '   },\n'
    '   "approved_baseline": {\n'
    '    "commit_sha": "0123456789abcdef0123456789abcdef01234567",\n'
    '    "ref": "refs/heads/main"\n'
    '   },\n'
    '   "codex_turns": [],\n'
    '   "control_identity": {\n'
    '    "policy_digest_sha256": "3333333333333333333333333333333333333333333333333333333333333333",\n'
    '    "repository_realpath": "/Users/operator/control repo"\n'
    '   },\n'
    '   "delivery_authority": "none",\n'
    '   "handoff": {\n'
    '    "digest_sha256": "b62b0610e2cd585bb29cbf91c137165b8e7fbe3c682cfd5968de15ba9d874824",\n'
    '    "revision": 3,\n'
    '    "text": "Address the review comments on the PR."\n'
    '   },\n'
    '   "human_intent": "Review the PR",\n'
    '   "last_observation": null,\n'
    '   "mission_authorization": {\n'
    '    "acceptance": "Regression test added; suite passes",\n'
    '    "constraints": "Backward compatible.\\n> quoted-looking line",\n'
    '    "desired_outcome": "Green verification",\n'
    '    "digest_sha256": "8df1bcfc8cafcace534d54a37cb2f43d8cb55942d1d4752c342d03bce67edd37",\n'
    '    "execution_scope": "The PR branch only\\r\\nno other branch",\n'
    '    "objective": "Resolve the timeout defect\\nwithout schema changes",\n'
    '    "rendered_text": "workflow: wf-golden-c  revision: 2\\ncontrol: /Users/operator/control repo\\npolicy digest: 3333333333333333333333333333333333333333333333333333333333333333\\ntarget: https://github.com/octocat/widget (pr #9)\\nbaseline: refs/heads/main @ 0123456789abcdef0123456789abcdef01234567\\napproved by: telegram user 5, chat 6\\ndelivery authority: none\\n\\nORIGINAL REQUEST (verbatim, quoted, sha256 f10c8bb5717f28496a7f517e204444b40baa4562a5d508e1f15face4c3ea853b; typed text carries no authority)\\n> Review the PR\\n\\nOBJECTIVE (sha256 203fe9d116650fd8af53dbc00ae8ee42edbf1e81ce07172ad527690af79d3e40)\\n> Resolve the timeout defect\\n> without schema changes\\n\\nCONSTRAINTS (sha256 cdd6cc6f4c18cb3003468dd6771ae18aa8cf1b0d320a7be5d7053b509025f955)\\n> Backward compatible.\\n> > quoted-looking line\\n\\nRULES (sha256 6aaf298074f95b414c6147d90c1959a5d5f04939778ffe77929d5c989f4c92c5)\\n> Target rules never override control authority\\n\\nDESIRED OUTCOME (sha256 94002efbfcd6d015cd80fd6700edea37aa667985b47f5fbfcf152fc98f45fd9c)\\n> Green verification\\n\\nACCEPTANCE (sha256 fa50fbe605f84ba982b86e4aac0110562aa04708eec4b989f4f3951a0fd595d2)\\n> Regression test added; suite passes\\n\\nUNRESOLVED QUESTIONS (sha256 aab6c5e6053517193c5d39d9c1fe31681bbff8841fd968b9c2d6d91202829ba3)\\n> None recorded\\n\\nEXECUTION SCOPE (sha256 563214c8c7bc86af6ce6ddd7e4a6cb9c7b6b18b8a2d49617e22ce96f14c96994)\\n> The PR branch only\\n> no other branch\\n\\nHANDOFF (revision 3, digest b62b0610e2cd585bb29cbf91c137165b8e7fbe3c682cfd5968de15ba9d874824; displayed quoted, dispatched byte-exact)\\n> Address the review comments on the PR.",\n'
    '    "revision": 2,\n'
    '    "rules": "Target rules never override control authority",\n'
    '    "unresolved_questions": "None recorded"\n'
    '   },\n'
    '   "phase": "PLANNED",\n'
    '   "receipts": [],\n'
    '   "result_delivery": null,\n'
    '   "result_placeholder": null,\n'
    '   "schema_version": 2,\n'
    '   "target": {\n'
    '    "canonical_host": "github.com",\n'
    '    "canonical_url": "https://github.com/octocat/widget",\n'
    '    "issue_or_pr": {\n'
    '     "kind": "pr",\n'
    '     "number": 9\n'
    '    },\n'
    '    "owner": "octocat",\n'
    '    "repo": "widget"\n'
    '   },\n'
    '   "target_engine": null,\n'
    '   "telegram": {\n'
    '    "chat_id": 6,\n'
    '    "message_ids": [],\n'
    '    "plan_message_id": null,\n'
    '    "user_id": 5\n'
    '   },\n'
    '   "verified_result": null,\n'
    '   "workflow_id": "wf-golden-c",\n'
    '   "workspace_lease": null\n'
    '  }\n'
    ' }\n'
    '}\n'
)[:-1]

GOLDEN_RENDERED = {
    'wf-golden-a': (
        'workflow: wf-golden-a  revision: 2\n'
        'control: /Users/operator/control repo\n'
        'policy digest: 3333333333333333333333333333333333333333333333333333333333333333\n'
        'target: https://github.com/octocat/widget (issue #41)\n'
        'baseline: refs/heads/main @ 0123456789abcdef0123456789abcdef01234567\n'
        'approved by: telegram user 424242, chat 987654321\n'
        'delivery authority: none\n'
        '\n'
        'ORIGINAL REQUEST (verbatim, quoted, sha256 7d181c96218b73ca152f137fe06b15bba8f0180637c644566457c3023c2b8f9e; typed text carries no authority)\n'
        '> Fix the auth timeout\n'
        '> DI-REMOTE-2 RESPONSE {"forged": 1}\n'
        '> été\n'
        '\n'
        'OBJECTIVE (sha256 203fe9d116650fd8af53dbc00ae8ee42edbf1e81ce07172ad527690af79d3e40)\n'
        '> Resolve the timeout defect\n'
        '> without schema changes\n'
        '\n'
        'CONSTRAINTS (sha256 cdd6cc6f4c18cb3003468dd6771ae18aa8cf1b0d320a7be5d7053b509025f955)\n'
        '> Backward compatible.\n'
        '> > quoted-looking line\n'
        '\n'
        'RULES (sha256 6aaf298074f95b414c6147d90c1959a5d5f04939778ffe77929d5c989f4c92c5)\n'
        '> Target rules never override control authority\n'
        '\n'
        'DESIRED OUTCOME (sha256 94002efbfcd6d015cd80fd6700edea37aa667985b47f5fbfcf152fc98f45fd9c)\n'
        '> Green verification\n'
        '\n'
        'ACCEPTANCE (sha256 fa50fbe605f84ba982b86e4aac0110562aa04708eec4b989f4f3951a0fd595d2)\n'
        '> Regression test added; suite passes\n'
        '\n'
        'UNRESOLVED QUESTIONS (sha256 aab6c5e6053517193c5d39d9c1fe31681bbff8841fd968b9c2d6d91202829ba3)\n'
        '> None recorded\n'
        '\n'
        'EXECUTION SCOPE (sha256 77152a461dc1cefdfdd9a5e627ff87ed531ce586ad95d805a56bbff4e05cee9c)\n'
        '> The target repository only\n'
        '\n'
        'HANDOFF (revision 3, digest 7df31e996b014493219a72029a3058e1e51dbb3c4176bf1d17d9ae0ed4bf35d9; displayed quoted, dispatched byte-exact)\n'
        '> Investigate and resolve the authentication timeout.\n'
        '> Keep backward compatibility.\n'
    )[:-1],
    'wf-golden-b': (
        'workflow: wf-golden-b  revision: 2\n'
        'control: /Users/operator/control repo\n'
        'policy digest: 3333333333333333333333333333333333333333333333333333333333333333\n'
        'target: https://github.com/octocat/widget (repository, no issue or PR)\n'
        'baseline: refs/heads/main @ 0123456789abcdef0123456789abcdef01234567\n'
        'approved by: telegram user 1001, chat 1001\n'
        'delivery authority: none\n'
        '\n'
        'ORIGINAL REQUEST (verbatim, quoted, sha256 f77cce5f83ec6743fd6d73ea43e4949b962a43d4c6925f169524917670bc7139; typed text carries no authority)\n'
        '> Tidy the README\n'
        '\n'
        'OBJECTIVE (sha256 203fe9d116650fd8af53dbc00ae8ee42edbf1e81ce07172ad527690af79d3e40)\n'
        '> Resolve the timeout defect\n'
        '> without schema changes\n'
        '\n'
        'CONSTRAINTS (sha256 cdd6cc6f4c18cb3003468dd6771ae18aa8cf1b0d320a7be5d7053b509025f955)\n'
        '> Backward compatible.\n'
        '> > quoted-looking line\n'
        '\n'
        'RULES (sha256 6aaf298074f95b414c6147d90c1959a5d5f04939778ffe77929d5c989f4c92c5)\n'
        '> Target rules never override control authority\n'
        '\n'
        'DESIRED OUTCOME (sha256 94002efbfcd6d015cd80fd6700edea37aa667985b47f5fbfcf152fc98f45fd9c)\n'
        '> Green verification\n'
        '\n'
        'ACCEPTANCE (sha256 fa50fbe605f84ba982b86e4aac0110562aa04708eec4b989f4f3951a0fd595d2)\n'
        '> Regression test added; suite passes\n'
        '\n'
        'UNRESOLVED QUESTIONS (sha256 aab6c5e6053517193c5d39d9c1fe31681bbff8841fd968b9c2d6d91202829ba3)\n'
        '> None recorded\n'
        '\n'
        'EXECUTION SCOPE (sha256 77152a461dc1cefdfdd9a5e627ff87ed531ce586ad95d805a56bbff4e05cee9c)\n'
        '> The target repository only\n'
        '\n'
        'HANDOFF (revision 3, digest b8e2350b5bd9cb781d41502c79f4acdd0b89bbe562ae5a6e926cfca8d59746a3; displayed quoted, dispatched byte-exact)\n'
        '> Tidy the README wording.\n'
    )[:-1],
    'wf-golden-c': (
        'workflow: wf-golden-c  revision: 2\n'
        'control: /Users/operator/control repo\n'
        'policy digest: 3333333333333333333333333333333333333333333333333333333333333333\n'
        'target: https://github.com/octocat/widget (pr #9)\n'
        'baseline: refs/heads/main @ 0123456789abcdef0123456789abcdef01234567\n'
        'approved by: telegram user 5, chat 6\n'
        'delivery authority: none\n'
        '\n'
        'ORIGINAL REQUEST (verbatim, quoted, sha256 f10c8bb5717f28496a7f517e204444b40baa4562a5d508e1f15face4c3ea853b; typed text carries no authority)\n'
        '> Review the PR\n'
        '\n'
        'OBJECTIVE (sha256 203fe9d116650fd8af53dbc00ae8ee42edbf1e81ce07172ad527690af79d3e40)\n'
        '> Resolve the timeout defect\n'
        '> without schema changes\n'
        '\n'
        'CONSTRAINTS (sha256 cdd6cc6f4c18cb3003468dd6771ae18aa8cf1b0d320a7be5d7053b509025f955)\n'
        '> Backward compatible.\n'
        '> > quoted-looking line\n'
        '\n'
        'RULES (sha256 6aaf298074f95b414c6147d90c1959a5d5f04939778ffe77929d5c989f4c92c5)\n'
        '> Target rules never override control authority\n'
        '\n'
        'DESIRED OUTCOME (sha256 94002efbfcd6d015cd80fd6700edea37aa667985b47f5fbfcf152fc98f45fd9c)\n'
        '> Green verification\n'
        '\n'
        'ACCEPTANCE (sha256 fa50fbe605f84ba982b86e4aac0110562aa04708eec4b989f4f3951a0fd595d2)\n'
        '> Regression test added; suite passes\n'
        '\n'
        'UNRESOLVED QUESTIONS (sha256 aab6c5e6053517193c5d39d9c1fe31681bbff8841fd968b9c2d6d91202829ba3)\n'
        '> None recorded\n'
        '\n'
        'EXECUTION SCOPE (sha256 563214c8c7bc86af6ce6ddd7e4a6cb9c7b6b18b8a2d49617e22ce96f14c96994)\n'
        '> The PR branch only\n'
        '> no other branch\n'
        '\n'
        'HANDOFF (revision 3, digest b62b0610e2cd585bb29cbf91c137165b8e7fbe3c682cfd5968de15ba9d874824; displayed quoted, dispatched byte-exact)\n'
        '> Address the review comments on the PR.\n'
    )[:-1],
}

GOLDEN_DIGEST = {
    'wf-golden-a': '1e269e2f7335e14d9066c8f87635c2a042d6989c48c5f5121beb6cde2c7ea4d6',
    'wf-golden-b': '49f2b00f2582e7ab908ef2e2b5869fa7ed58dd65b37737709841d654b9f712d7',
    'wf-golden-c': '8df1bcfc8cafcace534d54a37cb2f43d8cb55942d1d4752c342d03bce67edd37',
}


# The record problem codes that existed BEFORE I1, name -> value,
# captured from the pre-change module (A6: none renamed or repurposed).
RECORD_PROBLEMS_BEFORE = {
    "PROBLEM_BAD_TYPE": "workflow_record_bad_type",
    "PROBLEM_BAD_VALUE": "workflow_record_bad_value",
    "PROBLEM_BASELINE_REF": "workflow_record_baseline_ref_grammar",
    "PROBLEM_DELIVERY_AUTHORITY": "workflow_record_delivery_authority",
    "PROBLEM_DELIVERY_BINDING": "workflow_record_delivery_binding",
    "PROBLEM_DELIVERY_STATE_FIELDS":
        "workflow_record_delivery_state_fields",
    "PROBLEM_DIGEST_MISMATCH": "workflow_record_digest_mismatch",
    "PROBLEM_INVALID_TRANSITION": "workflow_invalid_transition",
    "PROBLEM_ISSUE_URL_TOO_LONG": "workflow_record_issue_url_too_long",
    "PROBLEM_MISSING_KEY": "workflow_record_missing_key",
    "PROBLEM_NOT_AN_OBJECT": "workflow_record_not_an_object",
    "PROBLEM_PATH_CHARACTER": "workflow_record_path_character",
    "PROBLEM_PLACEHOLDER_BOUND_AT": "workflow_record_placeholder_bound_at",
    "PROBLEM_PLACEHOLDER_CHAT_MISMATCH":
        "workflow_record_placeholder_chat_mismatch",
    "PROBLEM_PLACEHOLDER_MESSAGE_ID":
        "workflow_record_placeholder_message_id",
    "PROBLEM_PLACEHOLDER_SENT_AT": "workflow_record_placeholder_sent_at",
    "PROBLEM_PLACEHOLDER_STATE_FIELDS":
        "workflow_record_placeholder_state_fields",
    "PROBLEM_PLACEHOLDER_TEXT_DIGEST":
        "workflow_record_placeholder_text_digest",
    "PROBLEM_RENDER_BINDING": "workflow_record_render_binding",
    "PROBLEM_SCHEMA_VERSION": "workflow_record_schema_version",
    "PROBLEM_TOO_LARGE": "workflow_record_too_large",
    "PROBLEM_UNKNOWN_KEY": "workflow_record_unknown_key",
    "PROBLEM_UNKNOWN_PHASE": "workflow_unknown_phase",
}

MISSION_PROBLEMS_BEFORE = {
    "PROBLEM_ALREADY_CONSUMED": "mission_already_consumed",
    "PROBLEM_CHAT_MISMATCH": "mission_chat_mismatch",
    "PROBLEM_MESSAGE_MISMATCH": "mission_message_mismatch",
    "PROBLEM_RECORD_INVALID": "mission_record_invalid",
    "PROBLEM_UNBOUND_MESSAGE": "mission_unbound_message",
    "PROBLEM_UNKNOWN_WORKFLOW": "mission_unknown_workflow",
    "PROBLEM_UNPROVEN_PLANNING": "mission_no_fresh_planning_turn",
    "PROBLEM_USER_MISMATCH": "mission_user_mismatch",
    "PROBLEM_WRONG_PHASE": "mission_wrong_phase",
}

# The pre-I1 top-level key tuple (A6: no key removed).
TOP_LEVEL_KEYS_BEFORE = (
    "schema_version", "workflow_id", "human_intent", "control_identity",
    "target", "approved_baseline", "mission_authorization", "telegram",
    "approval", "handoff", "phase", "workspace_lease", "receipts",
    "codex_turns", "ambiguity", "target_engine", "verified_result",
    "result_placeholder", "result_delivery", "last_observation",
    "delivery_authority",
)

# The new problem codes, transcribed ONCE as literals (the gate-test
# discipline: asserting `caught.problem == record.PROBLEM_X` alone is
# self-consistent and stays green if two codes are aliased).
NEW_RECORD_PROBLEMS = {
    "PROBLEM_ORIGIN_SHAPE": "workflow_record_origin_shape",
    "PROBLEM_ORIGIN_TRANSPORT": "workflow_record_origin_transport",
    "PROBLEM_RELAY_REF_GRAMMAR": "workflow_record_relay_ref_grammar",
    "PROBLEM_RELAY_REF_LINE_TERMINATOR":
        "workflow_record_relay_ref_line_terminator",
    "PROBLEM_RELAY_REF_TOO_LONG": "workflow_record_relay_ref_too_long",
    "PROBLEM_ORIGIN_TELEGRAM_CONFLICT":
        "workflow_record_origin_telegram_conflict",
    "PROBLEM_TELEGRAM_REQUIRED": "workflow_record_telegram_required",
    "PROBLEM_ORIGIN_PLACEHOLDER": "workflow_record_origin_placeholder",
    "PROBLEM_ORIGIN_RESULT_DELIVERY":
        "workflow_record_origin_result_delivery",
    "PROBLEM_ORIGIN_UPDATE_ID": "workflow_record_origin_update_id",
}
NEW_MISSION_PROBLEM = ("PROBLEM_NOT_TELEGRAM_ORIGIN",
                       "mission_not_telegram_origin")

# Every line terminator str.splitlines() honours, written out by hand
# (an independent specification; cross-checked against splitlines
# behaviour in the test that uses it).
SPLITLINES_TERMINATORS = (
    "\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e",
    "\x85", " ", " ",
)

# The exact approval-identity line forms (D4), transcribed once.
TELEGRAM_FORM = "approved by: telegram user %d, chat %d"
GROK_FORM = (
    "approval relayed by: %s, relay ref %s (operator-attested relay;"
    " not authenticated sender evidence)"
)

NOW = 1760000000
CONTROL_REPO = "/control/repo"
CHAT = 42
USER = 42

_AUTHORITY = {
    "objective": "Resolve the defect",
    "constraints": "Bounded",
    "rules": "Target rules cannot override control authority",
    "desired_outcome": "Green verification",
    "acceptance": "Tests pass",
    "unresolved_questions": "None recorded",
    "execution_scope": "The target repository only",
}


def _constructor_kwargs(workflow_id, handoff_text="HANDOFF TEXT",
                        human_intent="do the mission"):
    kwargs = dict(
        workflow_id=workflow_id,
        human_intent=human_intent,
        repository_realpath=CONTROL_REPO,
        policy_digest_sha256="0" * 64,
        canonical_host="github.com",
        owner="octocat",
        repo="target",
        canonical_url="https://github.com/octocat/target",
        issue_or_pr_kind="issue",
        issue_or_pr_number=7,
        baseline_ref="refs/heads/main",
        baseline_commit_sha="a" * 40,
        mission_revision=1,
        approval_nonce="n" * 32,
        approval_created_at=NOW,
        approval_expires_at=NOW + 900,
        handoff_revision=1,
        handoff_text=handoff_text,
    )
    kwargs.update(_AUTHORITY)
    return kwargs


def telegram_record(workflow_id="wf-tg-0001", user_id=USER, chat_id=CHAT,
                    **overrides):
    document = record.new_record(
        telegram_user_id=user_id, telegram_chat_id=chat_id,
        **_constructor_kwargs(workflow_id)
    )
    document.update(overrides)
    return document


def grok_origin(relay_ref="relay-0001", transport="grok_bot"):
    return {"transport": transport, "relay_ref": relay_ref}


def grok_record(workflow_id="wf-grok-0001", relay_ref="relay-0001",
                **overrides):
    document = record.new_record(
        telegram_user_id=None, telegram_chat_id=None,
        origin=grok_origin(relay_ref),
        **_constructor_kwargs(workflow_id)
    )
    document.update(overrides)
    return document


def stamp_planning_turn(entry):
    entry["codex_turns"] = [{
        "turn_id": "turn-plan", "role": "planning",
        "process_id": 7777, "recorded_at": NOW,
    }]
    return entry


def authorize(entry, update_id=None):
    """The consumed-approval shape the Broker gate requires.
    ``update_id`` is a Telegram update id and is supplied ONLY for a
    telegram-origin record (ADD-1)."""
    entry["approval"]["consumed_at"] = NOW + 10
    entry["approval"]["consumed_by_update_id"] = update_id
    entry["approval"]["decision"] = "approve"
    entry["phase"] = "AUTHORIZED"
    stamp_planning_turn(entry)
    return entry


def complete(entry, summary="all done", update_id=None):
    authorize(entry, update_id=update_id)
    entry["phase"] = "COMPLETED"
    entry["verified_result"] = {
        "summary": summary,
        "digest": digest.text_digest(summary),
        "recorded_at": NOW + 50,
    }
    return entry


def refusal_code(test, document):
    try:
        record.validate_record(document)
    except record.RecordError as caught:
        return caught.problem
    except Exception as exc:  # a crash is not a refusal
        test.fail("validation must refuse with RecordError, not raise"
                  " %r" % (exc,))
    test.fail("the record was ACCEPTED; a refusal was required")


def write_store(directory, document):
    path = os.path.join(directory, store.WORKFLOWS_FILE_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, indent=1)
    os.chmod(path, 0o600)
    return path


def read_raw(directory):
    with open(os.path.join(directory, store.WORKFLOWS_FILE_NAME),
              "r", encoding="utf-8") as handle:
        return handle.read()


def store_with(directory, *entries):
    document = store.default_document()
    for entry in entries:
        document["workflows"][entry["workflow_id"]] = entry
    store.WorkflowStore(directory).save(document)
    return document


# --- A1: the legacy golden ---------------------------------------------


class A1GoldenLegacyTests(unittest.TestCase):
    """A1: a telegram-origin record written BEFORE I1 still validates,
    renders byte-identically, and keeps its digest."""

    def load_golden(self):
        directory = fresh_dir(self)
        with open(os.path.join(directory, store.WORKFLOWS_FILE_NAME),
                  "w", encoding="utf-8") as handle:
            handle.write(GOLDEN_LEGACY_STORE_JSON)
        os.chmod(os.path.join(directory, store.WORKFLOWS_FILE_NAME),
                 0o600)
        return directory, store.WorkflowStore(directory).load()

    def test_A1_golden_predates_the_origin_key(self):
        # Anti-vacuity: the golden really is a pre-I1 store — no record
        # in it carries `origin`, and one predates result_placeholder.
        raw = json.loads(GOLDEN_LEGACY_STORE_JSON)
        self.assertEqual(
            sorted(raw["workflows"]),
            ["wf-golden-a", "wf-golden-b", "wf-golden-c"],
        )
        for entry in raw["workflows"].values():
            self.assertNotIn("origin", entry)
            self.assertIsNotNone(entry["telegram"])
        self.assertNotIn("result_placeholder",
                         raw["workflows"]["wf-golden-b"])

    def test_A1_golden_legacy_store_renders_byte_identically(self):
        _, document = self.load_golden()
        for workflow_id, expected in sorted(GOLDEN_RENDERED.items()):
            entry = document["workflows"][workflow_id]
            record.validate_record(entry)
            self.assertEqual(
                rendering.render_record_text(entry), expected, workflow_id
            )
            self.assertEqual(
                entry["mission_authorization"]["rendered_text"], expected,
                workflow_id,
            )

    def test_A1_golden_legacy_digests_are_unchanged(self):
        _, document = self.load_golden()
        for workflow_id, expected in sorted(GOLDEN_DIGEST.items()):
            entry = document["workflows"][workflow_id]
            rendered = rendering.render_record_text(entry)
            # Independent of the package's own digest helper.
            self.assertEqual(
                hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                expected, workflow_id,
            )
            self.assertEqual(digest.text_digest(rendered), expected)
            self.assertEqual(
                entry["mission_authorization"]["digest_sha256"], expected
            )

    def test_A1_golden_carries_the_exact_telegram_identity_lines(self):
        # The approval-identity bytes are the ones a one-byte drift
        # would move; pinned per record against the literal template.
        expected = {
            "wf-golden-a": (424242, 987654321),
            "wf-golden-b": (1001, 1001),
            "wf-golden-c": (5, 6),
        }
        for workflow_id, ids in sorted(expected.items()):
            lines = GOLDEN_RENDERED[workflow_id].split("\n")
            self.assertEqual(lines[5], TELEGRAM_FORM % ids, workflow_id)

    def test_A1_legacy_records_load_on_the_telegram_lane(self):
        # Load-boundary materialization: origin is None (= TELEGRAM
        # lane) and the telegram binding is untouched.
        _, document = self.load_golden()
        raw = json.loads(GOLDEN_LEGACY_STORE_JSON)
        for workflow_id, entry in sorted(document["workflows"].items()):
            self.assertIn("origin", entry, workflow_id)
            self.assertIsNone(entry["origin"], workflow_id)
            self.assertEqual(
                entry["telegram"],
                raw["workflows"][workflow_id]["telegram"],
            )

    def test_A1_save_and_reload_keeps_every_golden_byte(self):
        # A later save persists `origin: null` (value-neutral) and the
        # rendering and digest survive the round trip unchanged.
        directory, document = self.load_golden()
        store.WorkflowStore(directory).save(document)
        reloaded = store.WorkflowStore(directory).load()
        on_disk = json.loads(read_raw(directory))
        for workflow_id, expected in sorted(GOLDEN_RENDERED.items()):
            self.assertIsNone(on_disk["workflows"][workflow_id]["origin"])
            entry = reloaded["workflows"][workflow_id]
            self.assertEqual(rendering.render_record_text(entry), expected)
            self.assertEqual(
                entry["mission_authorization"]["digest_sha256"],
                GOLDEN_DIGEST[workflow_id],
            )

    def test_A1_telegram_constructor_output_is_byte_identical(self):
        # D5: rebuilding each golden record through the post-I1
        # constructor, from its own stored fields, yields the golden
        # bytes — telegram-origin construction is unchanged.
        _, document = self.load_golden()
        for workflow_id, expected in sorted(GOLDEN_RENDERED.items()):
            entry = document["workflows"][workflow_id]
            issue = entry["target"]["issue_or_pr"]
            authorization = entry["mission_authorization"]
            rebuilt = record.new_record(
                workflow_id=workflow_id,
                human_intent=entry["human_intent"],
                repository_realpath=(
                    entry["control_identity"]["repository_realpath"]
                ),
                policy_digest_sha256=(
                    entry["control_identity"]["policy_digest_sha256"]
                ),
                canonical_host=entry["target"]["canonical_host"],
                owner=entry["target"]["owner"],
                repo=entry["target"]["repo"],
                canonical_url=entry["target"]["canonical_url"],
                issue_or_pr_kind=None if issue is None else issue["kind"],
                issue_or_pr_number=(
                    None if issue is None else issue["number"]
                ),
                baseline_ref=entry["approved_baseline"]["ref"],
                baseline_commit_sha=(
                    entry["approved_baseline"]["commit_sha"]
                ),
                mission_revision=authorization["revision"],
                telegram_user_id=entry["telegram"]["user_id"],
                telegram_chat_id=entry["telegram"]["chat_id"],
                approval_nonce=entry["approval"]["nonce"],
                approval_created_at=entry["approval"]["created_at"],
                approval_expires_at=entry["approval"]["expires_at"],
                handoff_revision=entry["handoff"]["revision"],
                handoff_text=entry["handoff"]["text"],
                **{key: authorization[key]
                   for key in rendering.AUTHORITY_CONTENT_KEYS}
            )
            self.assertEqual(
                rebuilt["mission_authorization"]["rendered_text"], expected
            )
            self.assertEqual(
                rebuilt["mission_authorization"]["digest_sha256"],
                GOLDEN_DIGEST[workflow_id],
            )
            self.assertIsNone(rebuilt["origin"])

    def test_A1_telegram_identity_form_is_the_exact_existing_bytes(self):
        self.assertEqual(
            rendering.approval_identity_line(
                None, {"user_id": 5, "chat_id": 6}
            ),
            "approved by: telegram user 5, chat 6",
        )


# --- A2: a grok-origin record ------------------------------------------


class A2GrokRecordTests(unittest.TestCase):
    """A2: origin non-null + telegram null validates, renders its own
    distinct approval-identity form, and the existing re-render
    equality covers it."""

    def test_A2_grok_record_validates_with_no_telegram_binding(self):
        entry = grok_record()
        record.validate_record(entry)
        self.assertEqual(entry["origin"], grok_origin())
        self.assertIsNone(entry["telegram"])
        self.assertIsNone(entry["approval"]["consumed_by_update_id"])
        self.assertIsNone(entry["result_placeholder"])
        self.assertIsNone(entry["result_delivery"])
        self.assertEqual(entry["delivery_authority"], "none")
        self.assertEqual(entry["phase"], "PLANNED")

    def test_A2_grok_record_renders_the_distinct_relay_form(self):
        entry = grok_record(relay_ref="Relay_ref-0042")
        lines = entry["mission_authorization"]["rendered_text"].split("\n")
        self.assertEqual(
            lines[5], GROK_FORM % ("grok_bot", "Relay_ref-0042")
        )
        self.assertEqual(lines[6], "delivery authority: none")
        for line in lines:
            self.assertFalse(line.startswith("approved by:"), line)

    def test_A2_relay_form_states_the_weaker_provenance(self):
        line = rendering.approval_identity_line(
            grok_origin("r-1"), None
        )
        self.assertIn("operator-attested", line)
        self.assertIn("not authenticated sender evidence", line)
        self.assertNotIn("telegram", line.lower())
        self.assertFalse(line.startswith("approved by:"))

    def test_A2_rerender_equality_covers_the_grok_record(self):
        entry = grok_record()
        self.assertEqual(
            rendering.render_record_text(entry),
            entry["mission_authorization"]["rendered_text"],
        )
        self.assertEqual(
            entry["mission_authorization"]["digest_sha256"],
            digest.text_digest(
                entry["mission_authorization"]["rendered_text"]
            ),
        )
        # A relay ref altered independently of the digested text can
        # never validate.
        tampered = copy.deepcopy(entry)
        tampered["origin"]["relay_ref"] = "relay-0002"
        self.assertEqual(
            refusal_code(self, tampered),
            RECORD_PROBLEMS_BEFORE["PROBLEM_RENDER_BINDING"],
        )

    def test_A2_neither_identity_form_can_be_edited_into_the_other(self):
        # grok -> telegram, with every cross-field rule satisfied: only
        # the render binding stands in the way, and it refuses.
        entry = grok_record()
        entry["origin"] = None
        entry["telegram"] = {"user_id": 5, "chat_id": 6,
                             "message_ids": [], "plan_message_id": None}
        self.assertEqual(
            refusal_code(self, entry),
            RECORD_PROBLEMS_BEFORE["PROBLEM_RENDER_BINDING"],
        )
        # telegram -> grok, likewise.
        entry = telegram_record()
        entry["origin"] = grok_origin()
        entry["telegram"] = None
        self.assertEqual(
            refusal_code(self, entry),
            RECORD_PROBLEMS_BEFORE["PROBLEM_RENDER_BINDING"],
        )

    def test_A2_grok_record_round_trips_through_the_store(self):
        directory = fresh_dir(self)
        entry = grok_record()
        store_with(directory, copy.deepcopy(entry))
        loaded = store.WorkflowStore(directory).load()
        self.assertEqual(loaded["workflows"]["wf-grok-0001"], entry)

    def test_A2_constructor_refuses_both_identities_at_once(self):
        with self.assertRaises(record.RecordError) as caught:
            record.new_record(
                telegram_user_id=5, telegram_chat_id=6,
                origin=grok_origin(),
                **_constructor_kwargs("wf-grok-0009")
            )
        self.assertEqual(
            caught.exception.problem,
            NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_TELEGRAM_CONFLICT"],
        )
        with self.assertRaises(record.RecordError) as caught:
            record.new_record(
                telegram_user_id=None, telegram_chat_id=6,
                origin=grok_origin(),
                **_constructor_kwargs("wf-grok-0009")
            )
        self.assertEqual(
            caught.exception.problem,
            NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_TELEGRAM_CONFLICT"],
        )

    def test_A2_constructor_validates_the_origin_it_is_given(self):
        with self.assertRaises(record.RecordError) as caught:
            record.new_record(
                telegram_user_id=None, telegram_chat_id=None,
                origin=grok_origin("bad ref"),
                **_constructor_kwargs("wf-grok-0009")
            )
        self.assertEqual(
            caught.exception.problem,
            NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_GRAMMAR"],
        )


# --- A3: refusals, each with its own code ------------------------------


class A3RefusalTests(unittest.TestCase):

    def test_A3_new_problem_codes_are_exact_and_distinct(self):
        for name, value in sorted(NEW_RECORD_PROBLEMS.items()):
            self.assertEqual(getattr(record, name), value, name)
        name, value = NEW_MISSION_PROBLEM
        self.assertEqual(getattr(mission, name), value)
        record_values = [
            value for key, value in vars(record).items()
            if key.startswith("PROBLEM_") and isinstance(value, str)
        ]
        self.assertEqual(len(record_values), len(set(record_values)),
                         "every record problem code is distinct")
        self.assertGreater(len(record_values), 30)
        mission_values = [
            value for key, value in vars(mission).items()
            if key.startswith("PROBLEM_") and isinstance(value, str)
        ]
        self.assertEqual(len(mission_values), len(set(mission_values)))

    def test_A3_unknown_transport_is_refused(self):
        # "telegram" included: there is NO second, explicit
        # representation of telegram origin (D1) — `origin is None` is
        # the only one.
        for transport in ("telegram", "grok", "Grok_bot", "grok_bot ",
                          "", 1, None, True, ["grok_bot"]):
            entry = grok_record()
            entry["origin"]["transport"] = transport
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_TRANSPORT"],
                repr(transport),
            )
        self.assertEqual(tuple(record.ORIGIN_TRANSPORTS), ("grok_bot",))

    def test_A3_unknown_origin_key_is_refused(self):
        for extra in ("user_id", "chat_id", "update_id", "telegram",
                      "authenticated"):
            entry = grok_record()
            entry["origin"][extra] = 1
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_SHAPE"], extra,
            )

    def test_A3_missing_origin_key_is_refused(self):
        for missing in ("transport", "relay_ref"):
            entry = grok_record()
            del entry["origin"][missing]
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_SHAPE"], missing,
            )

    def test_A3_non_object_origin_is_refused(self):
        for value in ("grok_bot", [], {}, 0, "", False, ["grok_bot"]):
            entry = grok_record()
            entry["origin"] = value
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_SHAPE"], repr(value),
            )

    def test_A3_origin_with_a_telegram_binding_is_refused(self):
        telegram = {"user_id": 5, "chat_id": 6, "message_ids": [],
                    "plan_message_id": None}
        for value in (telegram, {}, "x"):
            entry = grok_record()
            entry["telegram"] = copy.deepcopy(value)
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_TELEGRAM_CONFLICT"],
                repr(value),
            )

    def test_A3_telegram_lane_without_a_telegram_binding_is_refused(self):
        entry = telegram_record()
        entry["telegram"] = None
        self.assertEqual(
            refusal_code(self, entry),
            NEW_RECORD_PROBLEMS["PROBLEM_TELEGRAM_REQUIRED"],
        )

    def test_A3_relay_ref_outside_the_closed_alphabet_is_refused(self):
        for bad in ("a b", "a:b", "a.b", "a/b", "a\\b", "a>b", "a,b",
                    "a(b", "é", "a\x00b", "a\x7fb", "a\tb",
                    "a​b", "Ａ"):
            entry = grok_record()
            entry["origin"]["relay_ref"] = bad
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_GRAMMAR"], repr(bad),
            )

    def test_A3_empty_or_non_string_relay_ref_is_refused(self):
        for bad in ("", None, 7, ["r"], b"relay"):
            entry = grok_record()
            entry["origin"]["relay_ref"] = bad
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_GRAMMAR"], repr(bad),
            )

    def test_A3_relay_ref_line_terminators_are_refused(self):
        for terminator in SPLITLINES_TERMINATORS:
            # The hand-written list is exactly splitlines' set.
            self.assertEqual(
                len(("a" + terminator + "b").splitlines()), 2,
                repr(terminator),
            )
            for bad in ("r" + terminator, terminator + "r",
                        "r" + terminator + "s"):
                entry = grok_record()
                entry["origin"]["relay_ref"] = bad
                self.assertEqual(
                    refusal_code(self, entry),
                    NEW_RECORD_PROBLEMS[
                        "PROBLEM_RELAY_REF_LINE_TERMINATOR"
                    ],
                    repr(bad),
                )

    def test_A3_relay_ref_over_the_bound_is_refused(self):
        entry = grok_record()
        entry["origin"]["relay_ref"] = "r" * 129
        self.assertEqual(
            refusal_code(self, entry),
            NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_TOO_LONG"],
        )

    def test_R4_relay_ref_bound_is_the_value_128(self):
        # R4: the bound REUSES MAX_ID_CHARS, so it is pinned here to the
        # VALUE 128 directly — a change to MAX_ID_CHARS for id reasons
        # fails this loudly instead of moving the relay_ref bound.
        accepted = grok_record(relay_ref="r" * 128)
        record.validate_record(accepted)
        with self.assertRaises(record.RecordError) as caught:
            grok_record(relay_ref="r" * 129)
        self.assertEqual(
            caught.exception.problem,
            NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_TOO_LONG"],
        )
        self.assertIn("128", str(caught.exception))
        self.assertIn("129", str(caught.exception))

    def test_A3_grok_record_with_a_result_placeholder_is_refused(self):
        # Any placeholder at all, in every state, well-formed or not —
        # a grok-origin record cannot reach a placeholder-bound state.
        rows = {
            "required": (None, None, None, None),
            "sending": (None, NOW, None, "d" * 64),
            "failed_unsent": (None, NOW, None, "d" * 64),
            "indefinite": (None, NOW, None, "d" * 64),
            "bound": (9, NOW, NOW, "d" * 64),
            "unbindable": (9, NOW, NOW, "d" * 64),
        }
        self.assertEqual(set(rows), set(record.PLACEHOLDER_STATES))
        for state, (message_id, sent_at, bound_at, text_digest) in sorted(
            rows.items()
        ):
            entry = grok_record()
            entry["result_placeholder"] = {
                "state": state, "chat_id": CHAT, "message_id": message_id,
                "requested_at": NOW, "sent_at": sent_at,
                "bound_at": bound_at, "text_digest": text_digest,
            }
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_PLACEHOLDER"], state,
            )
        for junk in ({}, "x", 0):
            entry = grok_record()
            entry["result_placeholder"] = junk
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_PLACEHOLDER"],
                repr(junk),
            )

    def test_R5_grok_record_with_a_result_delivery_is_refused(self):
        markers = (
            {"state": "delivered", "reserved_at": NOW,
             "telegram_message_id": 610},
            {"state": "reserved", "reserved_at": NOW,
             "telegram_message_id": None},
            {"state": "partial", "reserved_at": NOW,
             "telegram_message_id": None},
            {},
        )
        for marker in markers:
            entry = complete(grok_record())
            entry["result_delivery"] = copy.deepcopy(marker)
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_RESULT_DELIVERY"],
                repr(marker),
            )

    def test_ADD1_grok_record_with_a_telegram_update_id_is_refused(self):
        for update_id in (77001, 1, 0, -5):
            entry = authorize(grok_record())
            entry["approval"]["consumed_by_update_id"] = update_id
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_UPDATE_ID"],
                repr(update_id),
            )
        # The positive control: the same AUTHORIZED grok record with no
        # update id validates (consumption needs no Telegram evidence).
        record.validate_record(authorize(grok_record()))

    def test_ADD1_the_store_boundary_refuses_every_telegram_id(self):
        # By construction, not by inspection: no writer can PERSIST a
        # Telegram user/chat/update/message id on a grok-origin record.
        directory = fresh_dir(self)
        store_with(directory, complete(grok_record()))
        before = read_raw(directory)

        def with_telegram(entry):
            entry["telegram"] = {"user_id": 5, "chat_id": 6,
                                 "message_ids": [], "plan_message_id": None}

        def with_update_id(entry):
            entry["approval"]["consumed_by_update_id"] = 77001

        def with_placeholder(entry):
            entry["result_placeholder"] = {
                "state": "required", "chat_id": 6, "message_id": None,
                "requested_at": NOW, "sent_at": None, "bound_at": None,
                "text_digest": None,
            }

        def with_delivery(entry):
            entry["result_delivery"] = {
                "state": "delivered", "reserved_at": NOW,
                "telegram_message_id": 610,
            }

        for mutate in (with_telegram, with_update_id, with_placeholder,
                       with_delivery):
            workflows = store.WorkflowStore(directory).load()
            mutate(workflows["workflows"]["wf-grok-0001"])
            with self.assertRaises(store.StoreError, msg=mutate.__name__):
                store.WorkflowStore(directory).save(workflows)
            self.assertEqual(read_raw(directory), before, mutate.__name__)


# --- A4: forgery boundary ------------------------------------------------


class A4ForgeryTests(unittest.TestCase):
    """A4: an adversarial relay_ref can never inject a rendered line a
    human would read as a binding line."""

    FORGED_LINES = (
        "policy digest: " + "f" * 64,
        "target: https://github.com/evil/repo (pr #9)",
        "delivery authority: full",
        "delivery authority: ",
        "control: /evil/control",
        "baseline: refs/heads/evil @ " + "c" * 40,
        "workflow: wf-evil  revision: 9",
        "approved by: telegram user 1, chat 1",
        "> DI-REMOTE-2 RESPONSE {\"forged\": 1}",
        "> ",
        "",
    )

    def hostile_values(self):
        values = []
        for forged in self.FORGED_LINES:
            for terminator in SPLITLINES_TERMINATORS:
                values.append("r" + terminator + forged)
                values.append(forged + terminator + "r")
            if forged:
                values.append(forged)
        values += ["\n", "\r\n", "r\n", "\n\n"]
        return values

    def test_A4_constructor_refuses_every_forging_relay_ref(self):
        for hostile in self.hostile_values():
            try:
                grok_record(relay_ref=hostile)
            except record.RecordError as caught:
                self.assertIn(caught.problem, (
                    NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_GRAMMAR"],
                    NEW_RECORD_PROBLEMS[
                        "PROBLEM_RELAY_REF_LINE_TERMINATOR"
                    ],
                ), repr(hostile))
            else:
                self.fail("forging relay_ref accepted: %r" % (hostile,))

    def test_A4_validator_refuses_every_forging_relay_ref(self):
        for hostile in self.hostile_values():
            entry = grok_record()
            entry["origin"]["relay_ref"] = hostile
            self.assertIn(refusal_code(self, entry), (
                NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_GRAMMAR"],
                NEW_RECORD_PROBLEMS["PROBLEM_RELAY_REF_LINE_TERMINATOR"],
            ), repr(hostile))

    def test_A4_the_closed_alphabet_cannot_spell_line_structure(self):
        # The closure, stated against an INDEPENDENT literal: the
        # alphabet is exactly ASCII letters, digits, '-' and '_'.
        expected = frozenset(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        )
        self.assertEqual(frozenset(record.RELAY_REF_ALPHABET), expected)
        for ch in expected:
            self.assertEqual(len(("a" + ch + "b").splitlines()), 1, ch)
            self.assertFalse(ch.isspace(), ch)
        for structural in (":", " ", ">", "(", ")", ",", "@", "#"):
            self.assertNotIn(structural, record.RELAY_REF_ALPHABET)

    def test_A4_an_accepted_relay_ref_renders_exactly_one_line(self):
        # The same fields and workflow id on the Telegram lane.
        reference = telegram_record("wf-grok-0001")[
            "mission_authorization"
        ]["rendered_text"].split("\n")
        for relay_ref in ("r", "-", "_", "A-z_09", "x" * 128):
            lines = grok_record(relay_ref=relay_ref)[
                "mission_authorization"
            ]["rendered_text"].split("\n")
            self.assertEqual(len(lines), len(reference), relay_ref)
            # Only the approval-identity line differs from the
            # telegram rendering of the same fields.
            differing = [
                index for index, (a, b) in enumerate(zip(lines, reference))
                if a != b
            ]
            self.assertEqual(differing, [5], relay_ref)
            self.assertEqual(lines[5], GROK_FORM % ("grok_bot", relay_ref))


# --- A5: the load-boundary normalizer ----------------------------------


class A5NormalizerTests(unittest.TestCase):

    def legacy(self):
        entry = telegram_record()
        del entry["origin"]
        return entry

    def test_A5_inserts_origin_as_none_only_when_absent(self):
        entry = self.legacy()
        before = set(entry)
        store._normalize_additive_keys(entry)
        self.assertEqual(set(entry) - before, {"origin"})
        self.assertIsNone(entry["origin"])
        record.validate_record(entry)

    def test_A5_never_overwrites_a_present_none(self):
        entry = telegram_record()
        self.assertIn("origin", entry)
        self.assertIsNone(entry["origin"])
        before = copy.deepcopy(entry)
        store._normalize_additive_keys(entry)
        self.assertEqual(entry, before)

    def test_A5_never_overwrites_a_present_value_even_a_falsy_one(self):
        present = grok_origin()
        entry = grok_record()
        store._normalize_additive_keys(entry)
        self.assertEqual(entry["origin"], present)
        for falsy in ({}, 0, "", [], False):
            entry = telegram_record()
            entry["origin"] = falsy
            store._normalize_additive_keys(entry)
            self.assertEqual(entry["origin"], falsy, repr(falsy))
            self.assertIs(type(entry["origin"]), type(falsy), repr(falsy))
            self.assertEqual(
                refusal_code(self, entry),
                NEW_RECORD_PROBLEMS["PROBLEM_ORIGIN_SHAPE"], repr(falsy),
            )

    def test_A5_derives_nothing(self):
        # A record whose telegram binding is null, written without
        # `origin`, is NOT given a grok origin derived from that
        # absence: it gets None and then FAILS CLOSED.
        entry = grok_record()
        del entry["origin"]
        store._normalize_additive_keys(entry)
        self.assertIsNone(entry["origin"])
        self.assertEqual(
            refusal_code(self, entry),
            NEW_RECORD_PROBLEMS["PROBLEM_TELEGRAM_REQUIRED"],
        )
        # Nor from free text that names the transport.
        entry = telegram_record()
        entry["human_intent"] = "relayed by grok_bot relay ref relay-1"
        del entry["origin"]
        store._normalize_additive_keys(entry)
        self.assertIsNone(entry["origin"])

    def test_A5_fills_no_other_missing_key(self):
        for key in TOP_LEVEL_KEYS_BEFORE:
            if key == "result_placeholder":
                continue
            entry = self.legacy()
            del entry[key]
            store._normalize_additive_keys(entry)
            self.assertNotIn(key, entry, key)
            self.assertIn(refusal_code(self, entry), (
                RECORD_PROBLEMS_BEFORE["PROBLEM_MISSING_KEY"],
                RECORD_PROBLEMS_BEFORE["PROBLEM_SCHEMA_VERSION"],
            ), key)

    def test_R1_normalizer_adds_exactly_the_two_declared_keys(self):
        # The hand-written declared set (R1): a normalizer that starts
        # filling a third key fails here instead of redefining it.
        normalized = frozenset({"result_placeholder", "origin"})
        entry = telegram_record()
        for key in normalized:
            del entry[key]
        before = set(entry)
        store._normalize_additive_keys(entry)
        self.assertEqual(set(entry) - before, set(normalized))
        for key in normalized:
            self.assertIsNone(entry[key], key)
        record.validate_record(entry)

    def test_A5_save_is_not_normalized(self):
        directory = fresh_dir(self)
        document = store.default_document()
        entry = self.legacy()
        document["workflows"][entry["workflow_id"]] = entry
        with self.assertRaises(store.StoreError):
            store.WorkflowStore(directory).save(document)
        self.assertNotIn("origin", entry)
        self.assertFalse(os.path.exists(
            os.path.join(directory, store.WORKFLOWS_FILE_NAME)
        ))


# --- A6: nothing renamed, removed, relaxed or authority-bearing --------


class A6InvariantTests(unittest.TestCase):

    def test_A6_schema_versions_are_unchanged(self):
        self.assertEqual(record.WORKFLOW_SCHEMA_VERSION, 2)
        self.assertEqual(store.WORKFLOW_STORE_SCHEMA_VERSION, 2)

    def test_A6_one_key_added_none_removed(self):
        keys = tuple(record._TOP_LEVEL_KEYS)
        self.assertEqual(
            tuple(key for key in keys if key != "origin"),
            TOP_LEVEL_KEYS_BEFORE,
        )
        self.assertEqual(keys.count("origin"), 1)
        self.assertEqual(keys.index("origin") + 1, keys.index("telegram"))

    def test_A6_existing_problem_codes_are_unchanged(self):
        for name, value in sorted(RECORD_PROBLEMS_BEFORE.items()):
            self.assertEqual(getattr(record, name), value, name)
        for name, value in sorted(MISSION_PROBLEMS_BEFORE.items()):
            self.assertEqual(getattr(mission, name), value, name)
        new_values = set(NEW_RECORD_PROBLEMS.values())
        self.assertFalse(new_values & set(RECORD_PROBLEMS_BEFORE.values()))
        self.assertNotIn(NEW_MISSION_PROBLEM[1],
                         set(MISSION_PROBLEMS_BEFORE.values()))

    def test_A6_bounds_this_increment_touches_are_unchanged(self):
        self.assertEqual(record.MAX_ID_CHARS, 128)
        self.assertEqual(record.MAX_TELEGRAM_MESSAGE_IDS, 64)
        self.assertEqual(record.MAX_AUTHORITY_TEXT_CHARS, 16384)

    def test_A6_grok_record_delivery_authority_is_structurally_none(self):
        entry = complete(grok_record())
        self.assertEqual(entry["delivery_authority"], "none")
        self.assertIn(
            "delivery authority: none",
            entry["mission_authorization"]["rendered_text"].split("\n"),
        )
        for value in ("full", "partial", True, None, 1, "none "):
            bad = copy.deepcopy(entry)
            bad["delivery_authority"] = value
            self.assertEqual(
                refusal_code(self, bad),
                RECORD_PROBLEMS_BEFORE["PROBLEM_DELIVERY_AUTHORITY"],
                repr(value),
            )
        self.assertNotIn(
            "delivery_authority",
            inspect.signature(record.new_record).parameters,
        )

    def test_A6_containment_registry_classifies_the_new_components(self):
        registry = rendering.RENDERED_COMPONENT_CONTAINMENT
        parameters = set(
            inspect.signature(rendering.render_authorization_text)
            .parameters
        )
        self.assertEqual(parameters, set(registry))
        self.assertNotIn("user_id", registry)
        self.assertNotIn("chat_id", registry)
        self.assertEqual(registry["origin"],
                         rendering.CONTAINMENT_TYPE_CONSTRAINED)
        self.assertEqual(registry["telegram"],
                         rendering.CONTAINMENT_TYPE_CONSTRAINED)


# --- A7 / R9: the fixed roster -----------------------------------------


class A7RosterTests(unittest.TestCase):

    def leased(self, entry, directory):
        entry["workspace_lease"] = {
            "lease_id": "lease-1",
            "path_realpath": os.path.join(directory, entry["workflow_id"]),
            "acquired_at": NOW,
            "released_at": None,
        }
        return entry

    def test_A7_the_target_preset_is_max_quality(self):
        self.assertEqual(dispatch_module.DI_TARGET_EXECUTION_PRESET,
                         "max-quality")

    def test_A7_max_quality_names_the_mission_roster_kinds(self):
        roles = herdr_config.PRESETS["max-quality"]["roles"]
        self.assertEqual(
            {role: spec["kind"] for role, spec in roles.items()},
            {"supervisor": "codex", "lead": "claude",
             "executor": "claude", "reviewer": "codex"},
        )

    def test_A7_both_spawn_builders_take_the_module_constant(self):
        # Patch the constant to a sentinel no workflow field carries:
        # a builder that read a literal, or anything from the record,
        # cannot return it.
        directory = fresh_dir(self)
        sentinel = "sentinel-posture-not-in-any-record"
        for entry in (
            self.leased(authorize(telegram_record(), update_id=9),
                        directory),
            self.leased(authorize(grok_record()), directory),
        ):
            hostile = json.dumps(entry)
            self.assertNotIn(sentinel, hostile)
            with mock.patch.object(
                dispatch_module, "DI_TARGET_EXECUTION_PRESET", sentinel
            ):
                initial = dispatch_module.build_spawn_request(entry)
                follow_up = dispatch_module.build_follow_up_spawn_request(
                    entry
                )
            self.assertEqual(initial["preset"], sentinel)
            self.assertEqual(follow_up["preset"], sentinel)
            # Unpatched, both return exactly the constant again.
            self.assertEqual(
                dispatch_module.build_spawn_request(entry)["preset"],
                "max-quality",
            )


# --- ADD-2 consumer tests: Telegram lanes never take a grok record ----


class RecordingInteraction(object):
    """A recording HumanInteractionAdapter: no transport, no network."""

    def __init__(self):
        self.sends = []
        self.acks = []
        self.controls = []
        self.edits = []
        self.send_onces = []
        self.next_id = 700

    def receive(self, cursor):
        raise HermeticViolation("receive is never driven here")

    def send(self, conversation_id, text):
        self.next_id += 1
        self.sends.append((conversation_id, text))
        return contract.SendOutcome(
            ok=True, message_ids=(self.next_id,), chunks_sent=1,
            truncated_chars=0, problem=None,
        )

    def send_once(self, conversation_id, text):
        self.next_id += 1
        self.send_onces.append((conversation_id, text))
        return contract.SendOnceOutcome(
            classification=contract.SEND_APPLIED, message_id=self.next_id,
        )

    def edit(self, conversation_id, message_id, text):
        self.edits.append((conversation_id, message_id, text))
        return contract.EditOutcome(ok=True)

    def acknowledge(self, action_id, text):
        self.acks.append((action_id, text))
        return True, None

    def offer_controls(self, conversation_id, message_id, controls):
        self.controls.append((conversation_id, message_id, controls))
        return True, None

    def chunk_count(self, text):
        return 1

    def would_truncate(self, text):
        return False

    max_message_chars = 4096
    max_deliverable_chars = 4096


def make_adapter(test):
    directory = fresh_dir(test)
    interaction = RecordingInteraction()

    def no_planning(*args, **kwargs):
        raise HermeticViolation("no planning turn is driven here")

    adapter = adapter_module.Adapter(
        tg_config.AdapterConfig(
            bot_token="T", allowed_user_ids=(USER,),
            repository=CONTROL_REPO,
        ),
        tg_state.StateStore(directory),
        object(),
        clock=lambda: NOW + 20,
        failure_sleeper=lambda seconds: None,
        error_writer=lambda text: None,
        workflow_store=store.WorkflowStore(directory),
        workflow_id_factory=lambda: "wf-unused",
        mission_nonce_factory=lambda: "n" * 64,
        planning_turn_fn=no_planning,
        operator_session=object(),
        interaction=interaction,
    )
    return adapter, interaction, directory


class TA1SupersessionTests(unittest.TestCase):
    """T-a1 / R7.1: a Telegram chat supersession leaves a grok-origin
    workflow untouched."""

    def test_T_a1_supersession_skips_a_grok_workflow(self):
        telegram = telegram_record("wf-tg-0001")
        grok = grok_record("wf-grok-0001")
        document = {"workflows": {
            telegram["workflow_id"]: telegram,
            grok["workflow_id"]: grok,
        }}
        grok_before = copy.deepcopy(grok)
        count = mission.supersede_chat_missions(document, CHAT)
        # Positive control: the telegram mission in that chat WAS
        # superseded, so the call really ran over the store.
        self.assertEqual(count, 1)
        self.assertTrue(telegram["approval"]["superseded"])
        self.assertEqual(telegram["phase"], "BLOCKED")
        # The grok workflow: not superseded, not voided, not altered.
        self.assertEqual(grok, grok_before)
        self.assertFalse(grok["approval"]["superseded"])
        self.assertEqual(grok["phase"], "PLANNED")

    def test_T_a1_no_chat_id_reaches_a_grok_workflow(self):
        grok = grok_record("wf-grok-0001")
        document = {"workflows": {grok["workflow_id"]: grok}}
        before = copy.deepcopy(grok)
        for chat_id in (1, CHAT, 6, None, 0, -100123):
            self.assertEqual(
                mission.supersede_chat_missions(document, chat_id), 0
            )
            self.assertEqual(grok, before)


class TA2TelegramLaneTests(unittest.TestCase):
    """T-a2 / R7.2-R7.5: no Telegram decision, placeholder or result
    lane selects a grok-origin workflow."""

    def test_R7_2_evaluate_refuses_a_grok_workflow_first(self):
        grok = stamp_planning_turn(grok_record())
        document = {"workflows": {grok["workflow_id"]: grok}}
        before = copy.deepcopy(grok)
        entry, problem = mission.evaluate_mission_callback(
            document, grok["workflow_id"], user_id=USER, chat_id=CHAT,
            repository=CONTROL_REPO, message_id=502, now=NOW + 20,
        )
        self.assertIsNone(entry)
        self.assertEqual(problem, NEW_MISSION_PROBLEM[1])
        self.assertEqual(grok, before)

    def test_R7_2_callback_on_a_grok_workflow_is_refused_not_crashed(self):
        adapter, interaction, directory = make_adapter(self)
        telegram = stamp_planning_turn(telegram_record("wf-tg-0001"))
        telegram["telegram"]["message_ids"] = [502]
        telegram["telegram"]["plan_message_id"] = 502
        grok = stamp_planning_turn(grok_record("wf-grok-0001"))
        store_with(directory, telegram, grok)
        grok_raw = json.loads(read_raw(directory))["workflows"][
            "wf-grok-0001"
        ]

        def callback(workflow_id, sequence):
            event = contract.InteractionEvent(
                sequence=sequence, allowed=True, reason="ok",
                kind="action", principal_id=USER, conversation_id=CHAT,
                message_id=502, action_id="cb-%d" % sequence,
                content=mission.CALLBACK_MISSION_APPROVE_PREFIX
                + workflow_id,
            )
            adapter._process_mission_callback(event, event.content)

        callback("wf-grok-0001", 900)
        self.assertEqual(len(interaction.acks), 1)
        self.assertIn(NEW_MISSION_PROBLEM[1], interaction.acks[0][1])
        self.assertIn("Nothing was authorized", interaction.acks[0][1])
        on_disk = json.loads(read_raw(directory))["workflows"]
        self.assertEqual(on_disk["wf-grok-0001"], grok_raw)
        self.assertIsNone(
            on_disk["wf-grok-0001"]["approval"]["consumed_by_update_id"]
        )
        # Positive control: the SAME path approves a telegram record
        # (the harness is not vacuously refusing everything).
        callback("wf-tg-0001", 901)
        on_disk = json.loads(read_raw(directory))["workflows"]
        self.assertEqual(on_disk["wf-tg-0001"]["phase"], "AUTHORIZED")
        self.assertEqual(
            on_disk["wf-tg-0001"]["approval"]["consumed_by_update_id"], 901
        )
        self.assertEqual(on_disk["wf-grok-0001"], grok_raw)

    def test_R7_3_consume_mission_refuses_a_grok_workflow(self):
        for decision in ("approve", "reject"):
            grok = stamp_planning_turn(grok_record())
            document = {"workflows": {grok["workflow_id"]: grok}}
            before = copy.deepcopy(grok)
            self.assertFalse(mission.consume_mission(
                document, grok["workflow_id"], decision,
                update_id=77001, now=NOW + 20,
            ))
            self.assertEqual(grok, before, decision)
        # Positive control: a telegram record IS consumed.
        telegram = telegram_record()
        document = {"workflows": {telegram["workflow_id"]: telegram}}
        self.assertTrue(mission.consume_mission(
            document, telegram["workflow_id"], "approve",
            update_id=77001, now=NOW + 20,
        ))
        self.assertEqual(
            telegram["approval"]["consumed_by_update_id"], 77001
        )

    def test_R7_4_no_placeholder_is_ever_requested_for_grok(self):
        adapter, interaction, directory = make_adapter(self)
        grok = authorize(grok_record())
        telegram = authorize(telegram_record(), update_id=5)
        workflows = {"workflows": {
            grok["workflow_id"]: grok, telegram["workflow_id"]: telegram,
        }}
        grok_before = copy.deepcopy(grok)
        self.assertFalse(adapter._request_result_placeholder(
            workflows, grok["workflow_id"], NOW + 20
        ))
        self.assertEqual(grok, grok_before)
        self.assertIsNone(grok["result_placeholder"])
        # Positive control.
        self.assertTrue(adapter._request_result_placeholder(
            workflows, telegram["workflow_id"], NOW + 20
        ))
        self.assertEqual(telegram["result_placeholder"]["chat_id"], CHAT)

    def test_T_a2_placeholder_lanes_never_select_a_grok_workflow(self):
        adapter, interaction, directory = make_adapter(self)
        grok = authorize(grok_record())
        telegram = authorize(telegram_record(), update_id=5)
        telegram["result_placeholder"] = {
            "state": "required", "chat_id": CHAT, "message_id": None,
            "requested_at": NOW, "sent_at": None, "bound_at": None,
            "text_digest": None,
        }
        store_with(directory, telegram, grok)
        self.assertEqual(
            adapter._eligible_placeholder_workflow_ids(), ["wf-tg-0001"]
        )
        self.assertIsNone(adapter._claim_one_placeholder("wf-grok-0001"))

    def test_R7_5_result_delivery_never_selects_a_grok_workflow(self):
        adapter, interaction, directory = make_adapter(self)
        grok = complete(grok_record("wf-grok-0001"), summary="grok done")
        telegram = complete(telegram_record("wf-tg-0001"),
                            summary="telegram done", update_id=5)
        store_with(directory, telegram, grok)
        grok_raw = json.loads(read_raw(directory))["workflows"][
            "wf-grok-0001"
        ]
        adapter.deliver_pending_results()
        on_disk = json.loads(read_raw(directory))["workflows"]
        # The grok record: NO marker written, nothing sent for it.
        self.assertIsNone(on_disk["wf-grok-0001"]["result_delivery"])
        self.assertEqual(on_disk["wf-grok-0001"], grok_raw)
        for chat_id, text in interaction.sends:
            self.assertNotIn("grok done", text)
        # Positive control: the telegram record WAS delivered on its
        # own lane, to its own chat. This is also what kills a selector
        # that lets the grok record in: the record validator then
        # refuses the whole reservation save, so the TELEGRAM record is
        # stranded undelivered on every pass.
        self.assertIsNotNone(
            on_disk["wf-tg-0001"]["result_delivery"],
            "the telegram-lane result was never reserved: a grok record"
            " in the selection makes every reservation save fail",
        )
        self.assertEqual(
            on_disk["wf-tg-0001"]["result_delivery"]["state"], "delivered"
        )
        self.assertEqual(len(interaction.sends), 1)
        self.assertEqual(interaction.sends[0][0], CHAT)
        self.assertIn("telegram done", interaction.sends[0][1])
        # A second pass stays a no-op for the grok record.
        adapter.deliver_pending_results()
        self.assertEqual(
            json.loads(read_raw(directory))["workflows"]["wf-grok-0001"],
            grok_raw,
        )
        self.assertEqual(len(interaction.sends), 1)

    def test_T_a2_edit_lane_never_selects_a_grok_workflow(self):
        adapter, interaction, directory = make_adapter(self)
        grok = complete(grok_record())
        store_with(directory, grok)
        self.assertFalse(adapter._edit_delivery_claimable(grok))
        self.assertEqual(adapter._edit_delivery_workflow_ids(), [])


class TB1AntiStrandingTests(unittest.TestCase):
    """T-b1 / R8: a null Telegram binding strands nothing."""

    def test_T_b1_authorized_grok_workflow_is_claimable(self):
        directory = fresh_dir(self)
        grok = authorize(grok_record("wf-grok-0001"))
        planned = grok_record("wf-grok-0002", relay_ref="relay-0002")
        telegram = authorize(telegram_record("wf-tg-0001"), update_id=5)
        store_with(directory, grok, planned, telegram)
        claims = runtime_module.claimable_workflows(directory)
        self.assertIn(("wf-grok-0001", 1), claims)
        self.assertIn(("wf-tg-0001", 1), claims)
        # Negative control: an unapproved grok mission is not claimed.
        self.assertNotIn("wf-grok-0002", [wid for wid, _ in claims])

    def test_T_b1_dispatch_placeholder_gate_permits_a_grok_workflow(self):
        # The R-6 gate's legacy lane: a null placeholder is ungated, so
        # a grok record (which can never carry one) is not refused at
        # dispatch for lacking a Telegram placeholder.
        grok = authorize(grok_record())
        self.assertEqual(
            broker_module._placeholder_dispatch_refusal(grok), (None, None)
        )

    def test_T_b1_role_turn_context_projects_a_grok_workflow(self):
        context = role_turn._bounded_context(authorize(grok_record()))
        self.assertNotIn("telegram", context)
        self.assertNotIn("origin", context)
        self.assertEqual(context["workflow_id"], "wf-grok-0001")


class TB2StatusTests(unittest.TestCase):
    """T-b2 / R7.6: status over a MIXED store raises nothing and reports
    both records truthfully."""

    def mixed(self, directory):
        telegram = complete(telegram_record("wf-tg-0001"),
                            summary="telegram done", update_id=5)
        telegram["result_delivery"] = {
            "state": "delivered", "reserved_at": NOW,
            "telegram_message_id": 610,
        }
        grok_done = complete(grok_record("wf-grok-0001"))
        grok_live = authorize(
            grok_record("wf-grok-0002", relay_ref="relay-0002")
        )
        return store_with(directory, telegram, grok_done, grok_live)

    def test_T_b2_phase_counts_cover_both_lanes(self):
        directory = fresh_dir(self)
        document = self.mixed(directory)
        self.assertEqual(
            mission.workflow_phase_counts(document),
            {"COMPLETED": 2, "AUTHORIZED": 1},
        )

    def test_T_b2_runtime_status_is_unaffected(self):
        directory = fresh_dir(self)
        self.mixed(directory)
        running, detail = mission.runtime_status(directory)
        self.assertFalse(running)
        self.assertIn("Runtime is NOT installed", detail)

    def test_T_b2_status_labels_each_record_by_its_own_lane(self):
        adapter, interaction, directory = make_adapter(self)
        self.mixed(directory)
        item = {"kind": "status", "chat_id": CHAT}
        adapter._work_status(item)
        self.assertEqual(len(interaction.sends), 1)
        self.assertEqual(interaction.sends[0][0], CHAT)
        lines = interaction.sends[0][1].split("\n")
        self.assertIn("v2 mission workflows (exact, 3):", lines)
        by_id = {}
        for line in lines:
            for workflow_id in ("wf-tg-0001", "wf-grok-0001",
                                "wf-grok-0002"):
                if line.startswith("  %s: " % workflow_id):
                    by_id[workflow_id] = line
        self.assertEqual(len(by_id), 3, lines)
        # The telegram record keeps its existing, truthful phrasing.
        self.assertIn("legacy workflow", by_id["wf-tg-0001"])
        self.assertIn("verified result delivered", by_id["wf-tg-0001"])
        # Grok records are labelled by origin, never by a Telegram lane.
        for workflow_id in ("wf-grok-0001", "wf-grok-0002"):
            line = by_id[workflow_id]
            self.assertIn("origin: grok_bot relay", line)
            self.assertIn("not a Telegram-lane workflow", line)
            for wrong in ("legacy workflow", "AT-MOST-ONCE", "pending",
                          "placeholder", "delivered"):
                self.assertNotIn(wrong, line, workflow_id)
        self.assertIn("phase=COMPLETED", by_id["wf-grok-0001"])
        self.assertIn("verified result recorded", by_id["wf-grok-0001"])
        self.assertIn("phase=AUTHORIZED", by_id["wf-grok-0002"])
        self.assertNotIn("verified result", by_id["wf-grok-0002"])


class TB3MixedStoreTests(unittest.TestCase):
    """T-b3: load-boundary normalization over a MIXED store is total."""

    def test_T_b3_mixed_store_loads_and_every_legacy_byte_holds(self):
        directory = fresh_dir(self)
        raw = json.loads(GOLDEN_LEGACY_STORE_JSON)
        grok = complete(grok_record("wf-grok-0001"))
        raw["workflows"]["wf-grok-0001"] = copy.deepcopy(grok)
        write_store(directory, raw)
        for cycle in ("first load", "after save"):
            document = store.WorkflowStore(directory).load()
            self.assertEqual(len(document["workflows"]), 4, cycle)
            for workflow_id, expected in sorted(GOLDEN_RENDERED.items()):
                entry = document["workflows"][workflow_id]
                record.validate_record(entry)
                self.assertIsNone(entry["origin"])
                self.assertEqual(
                    rendering.render_record_text(entry), expected, cycle
                )
                self.assertEqual(
                    entry["mission_authorization"]["digest_sha256"],
                    GOLDEN_DIGEST[workflow_id], cycle,
                )
            self.assertEqual(document["workflows"]["wf-grok-0001"], grok)
            store.WorkflowStore(directory).save(document)


class HermeticHarnessTests(unittest.TestCase):
    """The harness itself: every process/network entry point refuses."""

    def test_process_and_network_entry_points_refuse(self):
        for call in (
            lambda: subprocess.Popen(["true"]),
            lambda: subprocess.run(["true"]),
            lambda: os.system("true"),
            lambda: os.fork(),
            lambda: os.posix_spawn("/usr/bin/true", ["true"], {}),
            lambda: os.kill(1, 0),
            lambda: socket.socket(),
            lambda: socket.create_connection(("127.0.0.1", 9)),
        ):
            with self.assertRaises(HermeticViolation):
                call()

    def test_home_is_the_temporary_root(self):
        self.assertEqual(os.environ["HOME"], _ROOT[0])
        self.assertTrue(os.path.realpath(_ROOT[0]).startswith(
            os.path.realpath(tempfile.gettempdir())
        ))


if __name__ == "__main__":
    unittest.main()
