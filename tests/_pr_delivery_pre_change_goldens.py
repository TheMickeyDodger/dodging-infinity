"""PRE-CHANGE golden pr_delivery records: compatibility evidence, NOT
test data to edit (task 20261009-185923-53d267, Lead addendum A6 case 19).

Generated ONCE, before any source change for the ``pr_update`` delivery
kind, from the UNMODIFIED baseline tree 8b53bf48880ed325436b75a56578e6e53539e359
(a ``git archive`` extraction of that commit, proven byte-equal to the
then-clean worktree's ``pr_delivery/`` and ``workflow_authority/``). The
generator drove the baseline's OWN code with a scripted in-memory
repository (no process, no network, no repository touched):

- ``local_terminal_record``: ``cli.assemble_authority`` + ``cli._mint``;
- ``local_terminal_record_complete``: that record driven by the baseline
  ``DeliveryMachine`` to COMPLETE (BASE_REFRESH ``not_needed``; COMMIT,
  PUSH and PR_CREATE receipts succeeded; a pull request recorded);
- ``present_dots_document``: ``cli.present_dots_cmd``'s presented
  proposal and its digest;
- ``dots_record``: ``cli.attest_dots_cmd`` of exactly that proposal, with
  ``attest_args`` (the Operator's digest link and references).

Every ``authority_digest_sha256``, receipt digest, candidate identity and
proposal digest below is the value the PRE-CHANGE code computed. A later
build must accept these records unchanged; regenerating this file with a
later build would defeat its purpose, so ``GOLDEN_JSON_SHA256`` pins the
text as generated. The repository paths name a directory that does not
exist (``/nonexistent/di-golden/work``), so nothing here can address a
real repository.
"""

import json

GOLDEN_JSON_SHA256 = (
    "3470af3735f96916d03936857fa920cd45a92614206c125bf90ea3d051d1c9e2"
)

GOLDEN_JSON = r'''{
 "attest_args": {
  "proposal_digest": "e0cb1511d2871d4b23e2a3d7e1345f2ec4a788e1f96e8016d579843ece79083d",
  "relay_ref": "golden-relay-1",
  "reply_to": "golden-chat-message-1"
 },
 "dots_record": {
  "allowed_actions": [
   "BASE_REFRESH",
   "COMMIT",
   "PUSH",
   "PR_CREATE"
  ],
  "authority_digest_sha256": "cd5caea0d166090f6f1016bc9d4e18a48ed9407f601e15db85d3645624c67638",
  "base_state": {
   "advance_after_commit": null,
   "current_base_oid": "1111111111111111111111111111111111111111",
   "refreshed_at": null
  },
  "blocker": null,
  "candidate": {
   "entries": [
    {
     "blob": "3333333333333333333333333333333333333333",
     "mode": "100644",
     "path": "keep.txt",
     "status": "M"
    },
    {
     "blob": "4444444444444444444444444444444444444444",
     "mode": "100644",
     "path": "old.txt",
     "status": "D"
    },
    {
     "blob": "5555555555555555555555555555555555555555",
     "mode": "100644",
     "path": "src/pkg.py",
     "status": "A"
    },
    {
     "blob": "6666666666666666666666666666666666666666",
     "mode": "100755",
     "path": "tool.sh",
     "status": "M"
    }
   ],
   "entry_count": 4,
   "identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3"
  },
  "committer": {
   "email": "human@example.com",
   "name": "Delivery Human"
  },
  "delivery_id": "prd-5681b33c56922c6b4de58a11",
  "evidence": {
   "engineering_complete": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "recorded_at": 1799999000,
    "status": "COMPLETE",
    "task_id": "20260904-150441-159120",
    "task_state_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
   },
   "independent_verification": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "command_argv": [
     "python3",
     "-m",
     "nothing",
     "--serial"
    ],
    "exit_status": 0,
    "log_bytes": 10,
    "log_sha256": "180afa68b90bc00ff28b9619c3ddb78d30e3740c0608d57ef3d6837c7494745d",
    "ran_at": 1799999500.0,
    "recorded_at": 1800000000.0
   },
   "reviewer_approve": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "decision": "APPROVE",
    "recorded_at": 1799999000,
    "review_file_name": "20260904-150441-159120-round-02.md",
    "review_file_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "round": 2,
    "task_id": "20260904-150441-159120"
   }
  },
  "expiration": {
   "expires_at": 1800003600.0,
   "policy": "absolute_deadline"
  },
  "human_authorization": {
   "attestation": {
    "confirmation": "operator_relayed",
    "presented_at": 1800000000.0,
    "proposal_digest_sha256": "e0cb1511d2871d4b23e2a3d7e1345f2ec4a788e1f96e8016d579843ece79083d",
    "provenance": "operator_attested_not_independently_verified",
    "relay_ref": "golden-relay-1",
    "relayed_reply": "approved",
    "reply_to": "golden-chat-message-1",
    "residual_risk": "operator-attested: the Outer Operator reports that the human replied with this delivery approval; this layer did not verify the human, and a mistaken or malicious same-user operator or local process could fabricate it. Every digest and reference here was supplied by the Operator: Operator attestation, not verified authorship. The full binding ties the decision to one exact candidate; it does not bind the reply to a human cryptographically"
   },
   "authorized_at": 1800000060.0,
   "confirmation_digest_sha256": "82abe05f05be8652847908ed6087839ce83a6fba7793b32ca06c43e28c9c9395",
   "identity": "operator_attested_relay:outer_operator_relay",
   "source": "dots_operator_attested"
  },
  "mission": null,
  "mode": "pull_request",
  "original_baseline": {
   "commit_sha": "1111111111111111111111111111111111111111",
   "ref": "refs/heads/main"
  },
  "phase": "AUTHORIZED",
  "pr_content": {
   "architecture_notes": "One bounded state machine.",
   "nonblocking_risks": "None known.",
   "objective": "Deliver the reviewed candidate exactly once.",
   "title": "Golden: pre-change pull_request"
  },
  "previous_delivery_id": null,
  "pull_request": null,
  "remote": {
   "name": "origin",
   "repository_url": "https://github.com/octo/repo",
   "url_exact": "https://github.com/octo/repo.git",
   "url_fetch": "https://github.com/octo/repo.git",
   "url_push": "https://github.com/octo/repo.git"
  },
  "repository": {
   "canonical_host": "github.com",
   "git_dir_realpath": "/nonexistent/di-golden/work/.git",
   "owner": "octo",
   "realpath": "/nonexistent/di-golden/work",
   "repo": "repo",
   "repository_url": "https://github.com/octo/repo"
  },
  "reverification": {
   "argv": [
    "python3",
    "-m",
    "nothing",
    "--serial"
   ]
  },
  "revision": 1,
  "revocation": {
   "reason": null,
   "revoked": false,
   "revoked_at": null,
   "revoked_by": null
  },
  "schema_version": 1,
  "source": {
   "branch": "feature/golden",
   "ref": "refs/heads/feature/golden"
  },
  "steps": {
   "BASE_REFRESH": {
    "receipt": null,
    "state": "pending",
    "voided": []
   },
   "COMMIT": {
    "receipt": null,
    "state": "pending",
    "voided": []
   },
   "PR_CREATE": {
    "receipt": null,
    "state": "pending",
    "voided": []
   },
   "PUSH": {
    "receipt": null,
    "state": "pending",
    "voided": []
   }
  },
  "target_base": {
   "branch": "main",
   "ref": "refs/heads/main"
  },
  "updated_at": 1800000060.0,
  "workflow_identity": {
   "engineering_task_id": "20260904-150441-159120",
   "workflow_id": "wf-golden"
  }
 },
 "local_terminal_record": {
  "allowed_actions": [
   "BASE_REFRESH",
   "COMMIT",
   "PUSH",
   "PR_CREATE"
  ],
  "authority_digest_sha256": "4c71849594f95ea7259d4399711ef29e8b2b3bf431f41a1a575a7604229f01d6",
  "base_state": {
   "advance_after_commit": null,
   "current_base_oid": "1111111111111111111111111111111111111111",
   "refreshed_at": null
  },
  "blocker": null,
  "candidate": {
   "entries": [
    {
     "blob": "3333333333333333333333333333333333333333",
     "mode": "100644",
     "path": "keep.txt",
     "status": "M"
    },
    {
     "blob": "4444444444444444444444444444444444444444",
     "mode": "100644",
     "path": "old.txt",
     "status": "D"
    },
    {
     "blob": "5555555555555555555555555555555555555555",
     "mode": "100644",
     "path": "src/pkg.py",
     "status": "A"
    },
    {
     "blob": "6666666666666666666666666666666666666666",
     "mode": "100755",
     "path": "tool.sh",
     "status": "M"
    }
   ],
   "entry_count": 4,
   "identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3"
  },
  "committer": {
   "email": "human@example.com",
   "name": "Delivery Human"
  },
  "delivery_id": "prd-ce85dfd3219b9527aecf91ef",
  "evidence": {
   "engineering_complete": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "recorded_at": 1799999000,
    "status": "COMPLETE",
    "task_id": "20260904-150441-159120",
    "task_state_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
   },
   "independent_verification": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "command_argv": [
     "python3",
     "-m",
     "nothing",
     "--serial"
    ],
    "exit_status": 0,
    "log_bytes": 10,
    "log_sha256": "180afa68b90bc00ff28b9619c3ddb78d30e3740c0608d57ef3d6837c7494745d",
    "ran_at": 1799999500.0,
    "recorded_at": 1800000000.0
   },
   "reviewer_approve": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "decision": "APPROVE",
    "recorded_at": 1799999000,
    "review_file_name": "20260904-150441-159120-round-02.md",
    "review_file_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "round": 2,
    "task_id": "20260904-150441-159120"
   }
  },
  "expiration": {
   "expires_at": 1800003600.0,
   "policy": "absolute_deadline"
  },
  "human_authorization": {
   "authorized_at": 1800000000.0,
   "confirmation_digest_sha256": "27b7f4481ba2e89656546ba4d9c1af95e032bb3d4a6010922a71232c0f490eb0",
   "identity": "golden-human",
   "source": "local_terminal"
  },
  "mission": null,
  "mode": "pull_request",
  "original_baseline": {
   "commit_sha": "1111111111111111111111111111111111111111",
   "ref": "refs/heads/main"
  },
  "phase": "AUTHORIZED",
  "pr_content": {
   "architecture_notes": "One bounded state machine.",
   "nonblocking_risks": "None known.",
   "objective": "Deliver the reviewed candidate exactly once.",
   "title": "Golden: pre-change pull_request"
  },
  "previous_delivery_id": null,
  "pull_request": null,
  "remote": {
   "name": "origin",
   "repository_url": "https://github.com/octo/repo",
   "url_exact": "https://github.com/octo/repo.git",
   "url_fetch": "https://github.com/octo/repo.git",
   "url_push": "https://github.com/octo/repo.git"
  },
  "repository": {
   "canonical_host": "github.com",
   "git_dir_realpath": "/nonexistent/di-golden/work/.git",
   "owner": "octo",
   "realpath": "/nonexistent/di-golden/work",
   "repo": "repo",
   "repository_url": "https://github.com/octo/repo"
  },
  "reverification": {
   "argv": [
    "python3",
    "-m",
    "nothing",
    "--serial"
   ]
  },
  "revision": 1,
  "revocation": {
   "reason": null,
   "revoked": false,
   "revoked_at": null,
   "revoked_by": null
  },
  "schema_version": 1,
  "source": {
   "branch": "feature/golden",
   "ref": "refs/heads/feature/golden"
  },
  "steps": {
   "BASE_REFRESH": {
    "receipt": null,
    "state": "pending",
    "voided": []
   },
   "COMMIT": {
    "receipt": null,
    "state": "pending",
    "voided": []
   },
   "PR_CREATE": {
    "receipt": null,
    "state": "pending",
    "voided": []
   },
   "PUSH": {
    "receipt": null,
    "state": "pending",
    "voided": []
   }
  },
  "target_base": {
   "branch": "main",
   "ref": "refs/heads/main"
  },
  "updated_at": 1800000000.0,
  "workflow_identity": {
   "engineering_task_id": "20260904-150441-159120",
   "workflow_id": "wf-golden"
  }
 },
 "local_terminal_record_complete": {
  "allowed_actions": [
   "BASE_REFRESH",
   "COMMIT",
   "PUSH",
   "PR_CREATE"
  ],
  "authority_digest_sha256": "4c71849594f95ea7259d4399711ef29e8b2b3bf431f41a1a575a7604229f01d6",
  "base_state": {
   "advance_after_commit": null,
   "current_base_oid": "1111111111111111111111111111111111111111",
   "refreshed_at": null
  },
  "blocker": null,
  "candidate": {
   "entries": [
    {
     "blob": "3333333333333333333333333333333333333333",
     "mode": "100644",
     "path": "keep.txt",
     "status": "M"
    },
    {
     "blob": "4444444444444444444444444444444444444444",
     "mode": "100644",
     "path": "old.txt",
     "status": "D"
    },
    {
     "blob": "5555555555555555555555555555555555555555",
     "mode": "100644",
     "path": "src/pkg.py",
     "status": "A"
    },
    {
     "blob": "6666666666666666666666666666666666666666",
     "mode": "100755",
     "path": "tool.sh",
     "status": "M"
    }
   ],
   "entry_count": 4,
   "identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3"
  },
  "committer": {
   "email": "human@example.com",
   "name": "Delivery Human"
  },
  "delivery_id": "prd-ce85dfd3219b9527aecf91ef",
  "evidence": {
   "engineering_complete": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "recorded_at": 1799999000,
    "status": "COMPLETE",
    "task_id": "20260904-150441-159120",
    "task_state_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
   },
   "independent_verification": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "command_argv": [
     "python3",
     "-m",
     "nothing",
     "--serial"
    ],
    "exit_status": 0,
    "log_bytes": 10,
    "log_sha256": "180afa68b90bc00ff28b9619c3ddb78d30e3740c0608d57ef3d6837c7494745d",
    "ran_at": 1799999500.0,
    "recorded_at": 1800000000.0
   },
   "reviewer_approve": {
    "base_oid": "1111111111111111111111111111111111111111",
    "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
    "decision": "APPROVE",
    "recorded_at": 1799999000,
    "review_file_name": "20260904-150441-159120-round-02.md",
    "review_file_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "round": 2,
    "task_id": "20260904-150441-159120"
   }
  },
  "expiration": {
   "expires_at": 1800003600.0,
   "policy": "absolute_deadline"
  },
  "human_authorization": {
   "authorized_at": 1800000000.0,
   "confirmation_digest_sha256": "27b7f4481ba2e89656546ba4d9c1af95e032bb3d4a6010922a71232c0f490eb0",
   "identity": "golden-human",
   "source": "local_terminal"
  },
  "mission": null,
  "mode": "pull_request",
  "original_baseline": {
   "commit_sha": "1111111111111111111111111111111111111111",
   "ref": "refs/heads/main"
  },
  "phase": "COMPLETE",
  "pr_content": {
   "architecture_notes": "One bounded state machine.",
   "nonblocking_risks": "None known.",
   "objective": "Deliver the reviewed candidate exactly once.",
   "title": "Golden: pre-change pull_request"
  },
  "previous_delivery_id": null,
  "pull_request": {
   "base_ref": "refs/heads/main",
   "head_sha": "7777777777777777777777777777777777777777",
   "number": 41,
   "url": "https://github.com/octo/repo/pull/41"
  },
  "remote": {
   "name": "origin",
   "repository_url": "https://github.com/octo/repo",
   "url_exact": "https://github.com/octo/repo.git",
   "url_fetch": "https://github.com/octo/repo.git",
   "url_push": "https://github.com/octo/repo.git"
  },
  "repository": {
   "canonical_host": "github.com",
   "git_dir_realpath": "/nonexistent/di-golden/work/.git",
   "owner": "octo",
   "realpath": "/nonexistent/di-golden/work",
   "repo": "repo",
   "repository_url": "https://github.com/octo/repo"
  },
  "reverification": {
   "argv": [
    "python3",
    "-m",
    "nothing",
    "--serial"
   ]
  },
  "revision": 1,
  "revocation": {
   "reason": null,
   "revoked": false,
   "revoked_at": null,
   "revoked_by": null
  },
  "schema_version": 1,
  "source": {
   "branch": "feature/golden",
   "ref": "refs/heads/feature/golden"
  },
  "steps": {
   "BASE_REFRESH": {
    "receipt": null,
    "state": "not_needed",
    "voided": []
   },
   "COMMIT": {
    "receipt": {
     "attempt": 1,
     "binding": {
      "branch": "feature/golden",
      "candidate_identity_digest": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
      "committer_email": "human@example.com",
      "committer_name": "Delivery Human",
      "expected_tree_oid": "8888888888888888888888888888888888888888",
      "git_dir_realpath": "/nonexistent/di-golden/work/.git",
      "head_before": "1111111111111111111111111111111111111111",
      "message_sha256": "ffd0af77e23f33db2e27985640aaa6194964ba657a43967db6639242978f7392",
      "repository_realpath": "/nonexistent/di-golden/work",
      "source_ref": "refs/heads/feature/golden",
      "staged_sha256": "9999999999999999999999999999999999999999999999999999999999999999"
     },
     "delivery_id": "prd-ce85dfd3219b9527aecf91ef",
     "derived_at": 1800000010.0,
     "observed": {
      "commit_oid": "7777777777777777777777777777777777777777"
     },
     "parent_authority_digest_sha256": "4c71849594f95ea7259d4399711ef29e8b2b3bf431f41a1a575a7604229f01d6",
     "receipt_digest_sha256": "be0bc756e381fd651d049f082beb920f903c142758069af1b375c4cc7a502aeb",
     "receipt_id": "rcpt-406a6d1a4b4d0bcf33bb6e22",
     "state": "succeeded",
     "step": "COMMIT"
    },
    "state": "succeeded",
    "voided": []
   },
   "PR_CREATE": {
    "receipt": {
     "attempt": 1,
     "binding": {
      "base_branch": "main",
      "body_sha256": "c48ca4dab46f5e3c88c58d789f2023e730514756ca995d6f543bf24bc2bd9e92",
      "candidate_identity_digest": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
      "head_branch": "feature/golden",
      "head_sha": "7777777777777777777777777777777777777777",
      "owner": "octo",
      "remote_url_exact": "https://github.com/octo/repo.git",
      "repo": "repo",
      "title_sha256": "ff833f51d0fbf3b9674dd05418ddc06f7e6abb2aa8a3c7c9bbf88115e27a8953"
     },
     "delivery_id": "prd-ce85dfd3219b9527aecf91ef",
     "derived_at": 1800000010.0,
     "observed": {
      "number": 41,
      "reconciled": false,
      "url": "https://github.com/octo/repo/pull/41"
     },
     "parent_authority_digest_sha256": "4c71849594f95ea7259d4399711ef29e8b2b3bf431f41a1a575a7604229f01d6",
     "receipt_digest_sha256": "565a921ae01bda4719202395597b7774d9986c43028075475dddd79cf34bd315",
     "receipt_id": "rcpt-70b4a2724d2bbcb1c7c7c4bd",
     "state": "succeeded",
     "step": "PR_CREATE"
    },
    "state": "succeeded",
    "voided": []
   },
   "PUSH": {
    "receipt": {
     "attempt": 1,
     "binding": {
      "candidate_identity_digest": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
      "destination_ref": "refs/heads/feature/golden",
      "expected_remote_old_oid": "0000000000000000000000000000000000000000",
      "remote_name": "origin",
      "remote_url_exact": "https://github.com/octo/repo.git",
      "remote_url_push": "https://github.com/octo/repo.git",
      "repository_realpath": "/nonexistent/di-golden/work",
      "source_commit": "7777777777777777777777777777777777777777",
      "source_ref": "refs/heads/feature/golden"
     },
     "delivery_id": "prd-ce85dfd3219b9527aecf91ef",
     "derived_at": 1800000010.0,
     "observed": {
      "reconciled": false,
      "remote_oid": "7777777777777777777777777777777777777777"
     },
     "parent_authority_digest_sha256": "4c71849594f95ea7259d4399711ef29e8b2b3bf431f41a1a575a7604229f01d6",
     "receipt_digest_sha256": "5c4af5c9264ff377e3341b4ab3cc50ee336b4a55156c6d3e329b66ae408a7cc2",
     "receipt_id": "rcpt-52efb18db30733bbe3a7d576",
     "state": "succeeded",
     "step": "PUSH"
    },
    "state": "succeeded",
    "voided": []
   }
  },
  "target_base": {
   "branch": "main",
   "ref": "refs/heads/main"
  },
  "updated_at": 1800000010.0,
  "workflow_identity": {
   "engineering_task_id": "20260904-150441-159120",
   "workflow_id": "wf-golden"
  }
 },
 "present_dots_document": {
  "delivery_proposal": {
   "binding": {
    "allowed_actions": [
     "BASE_REFRESH",
     "COMMIT",
     "PUSH",
     "PR_CREATE"
    ],
    "candidate": {
     "entries": [
      {
       "blob": "3333333333333333333333333333333333333333",
       "mode": "100644",
       "path": "keep.txt",
       "status": "M"
      },
      {
       "blob": "4444444444444444444444444444444444444444",
       "mode": "100644",
       "path": "old.txt",
       "status": "D"
      },
      {
       "blob": "5555555555555555555555555555555555555555",
       "mode": "100644",
       "path": "src/pkg.py",
       "status": "A"
      },
      {
       "blob": "6666666666666666666666666666666666666666",
       "mode": "100755",
       "path": "tool.sh",
       "status": "M"
      }
     ],
     "entry_count": 4,
     "identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3"
    },
    "committer": {
     "email": "human@example.com",
     "name": "Delivery Human"
    },
    "evidence": {
     "engineering_complete": {
      "base_oid": "1111111111111111111111111111111111111111",
      "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
      "recorded_at": 1799999000,
      "status": "COMPLETE",
      "task_id": "20260904-150441-159120",
      "task_state_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
     },
     "independent_verification": {
      "base_oid": "1111111111111111111111111111111111111111",
      "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
      "command_argv": [
       "python3",
       "-m",
       "nothing",
       "--serial"
      ],
      "exit_status": 0,
      "log_bytes": 10,
      "log_sha256": "180afa68b90bc00ff28b9619c3ddb78d30e3740c0608d57ef3d6837c7494745d",
      "ran_at": 1799999500.0,
      "recorded_at": 1800000000.0
     },
     "reviewer_approve": {
      "base_oid": "1111111111111111111111111111111111111111",
      "candidate_identity_digest_sha256": "cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3",
      "decision": "APPROVE",
      "recorded_at": 1799999000,
      "review_file_name": "20260904-150441-159120-round-02.md",
      "review_file_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "round": 2,
      "task_id": "20260904-150441-159120"
     }
    },
    "mission": null,
    "mode": "pull_request",
    "original_baseline": {
     "commit_sha": "1111111111111111111111111111111111111111",
     "ref": "refs/heads/main"
    },
    "pr_content": {
     "architecture_notes": "One bounded state machine.",
     "nonblocking_risks": "None known.",
     "objective": "Deliver the reviewed candidate exactly once.",
     "title": "Golden: pre-change pull_request"
    },
    "previous_delivery_id": null,
    "remote": {
     "name": "origin",
     "repository_url": "https://github.com/octo/repo",
     "url_exact": "https://github.com/octo/repo.git",
     "url_fetch": "https://github.com/octo/repo.git",
     "url_push": "https://github.com/octo/repo.git"
    },
    "repository": {
     "canonical_host": "github.com",
     "git_dir_realpath": "/nonexistent/di-golden/work/.git",
     "owner": "octo",
     "realpath": "/nonexistent/di-golden/work",
     "repo": "repo",
     "repository_url": "https://github.com/octo/repo"
    },
    "reverification": {
     "argv": [
      "python3",
      "-m",
      "nothing",
      "--serial"
     ]
    },
    "revision": 1,
    "source": {
     "branch": "feature/golden",
     "ref": "refs/heads/feature/golden"
    },
    "target_base": {
     "branch": "main",
     "ref": "refs/heads/main"
    },
    "workflow_identity": {
     "engineering_task_id": "20260904-150441-159120",
     "workflow_id": "wf-golden"
    }
   },
   "expires_at": 1800003600.0,
   "presented_at": 1800000000.0
  },
  "display": "\nPR DELIVERY AUTHORIZATION REQUEST\n---------------------------------\nRepository    : /nonexistent/di-golden/work\nRemote        : origin = https://github.com/octo/repo.git\n  fetches from: https://github.com/octo/repo.git\n  pushes to   : https://github.com/octo/repo.git\nSource branch : feature/golden (refs/heads/feature/golden)\nTarget base   : main (remote at 1111111111111111111111111111111111111111)\nBaseline      : 1111111111111111111111111111111111111111\nCandidate     : 4 entries, identity cc713ede9a6d47414da94bbc4ec9e1ff06b9b416126ce80a238b4bd4491b76a3\n  M 100644 keep.txt\n  D 100644 old.txt\n  A 100644 src/pkg.py\n  M 100755 tool.sh\nEngineering   : task 20260904-150441-159120 COMPLETE\nReviewer      : APPROVE round 2 (20260904-150441-159120-round-02.md)\nVerification  : python3 -m nothing --serial -> exit 0, log sha256 180afa68b90bc00ff28b9619c3ddb78d30e3740c0608d57ef3d6837c7494745d\nReverify with : python3 -m nothing --serial\nCommitter     : Delivery Human <human@example.com> (unsigned: commit.gpgsign=false on the argv)\nAllowed       : BASE_REFRESH, COMMIT, PUSH, PR_CREATE\nNot allowed   : merge, auto-merge, tag, release, deploy, publish, force push\nExpires       : 2027-01-15T09:00:00Z (absolute; 3600 seconds from presentation, not from approval)\nApprover      : the human, by a simple reply relayed by the Outer Operator (operator-attested, not independently verified)",
  "proposal_digest_sha256": "e0cb1511d2871d4b23e2a3d7e1345f2ec4a788e1f96e8016d579843ece79083d",
  "reply": "Reply exactly 'approved' or 'approve' to authorize THIS delivery; no digest is ever typed",
  "residual_risk": "operator-attested: the Outer Operator reports that the human replied with this delivery approval; this layer did not verify the human, and a mistaken or malicious same-user operator or local process could fabricate it. Every digest and reference here was supplied by the Operator: Operator attestation, not verified authorship. The full binding ties the decision to one exact candidate; it does not bind the reply to a human cryptographically",
  "source": "dots_operator_attested"
 }
}
'''


def goldens():
    """A fresh, independent copy of every golden document."""
    return json.loads(GOLDEN_JSON)
