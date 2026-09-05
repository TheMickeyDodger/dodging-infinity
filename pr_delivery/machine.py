"""The bounded, durable PR delivery state machine.

Four steps in a fixed order — BASE_REFRESH, COMMIT, PUSH, PR_CREATE —
each driven by one derived receipt, each persisted as ``executing``
(fsync) BEFORE its external effect so a crash at any point reconciles
forward from durable state: the exact expected commit is adopted, never
recreated; the exact remote ref is adopted, never re-pushed to a
different value; the exact existing head/base pull request is adopted,
never duplicated. Reconciliation can prove an effect PRESENT (receipt
``succeeded``), prove it ABSENT (receipt ``void``, and only then may a
fresh receipt be derived, up to the attempt bound), or be unable to
decide — which blocks durably with a named problem. Nothing here
guesses.

Before every derivation and again immediately before every external
effect the record is re-read from disk and checked for revocation and
expiry, so a revocation always stops the NEXT effect while every effect
already proven stays recorded.

Base drift (request: "routine fast-forward target-base advancement"):

- BEFORE the commit (phase AUTHORIZED) the remote base is compared with
  the base the candidate sits on. A disjoint fast-forward advance is
  applied automatically: fetch, prove ancestry, prove the base-changed
  path set is disjoint from the candidate (exact and directory-prefix
  overlap both ways), ``read-tree -m -u`` (git's own two-way merge is a
  second, independent refusal on overlap), compare-and-swap the source
  ref, recompute the candidate identity against the NEW base and require
  it to equal the bound identity, check the base's CI, and re-run the
  human-bound reverification argv. Anything else blocks durably.
- AFTER the commit (Lead M2; phases COMMITTED and PUSHED) the source ref
  can no longer be moved without rewriting the commit, which is
  forbidden. The explicit answer: a DISJOINT fast-forward advance is
  RECORDED (``base_state.advance_after_commit``) and delivery continues —
  a pull request targets a base ref, not a base OID, and the recorded
  proof (ancestry, disjointness, candidate identity re-match against the
  commit, base CI) is exactly what makes that continuation safe. The
  reverification argv is NOT re-run post-commit, because running it
  against the merged result would require constructing a merge, which
  this machine has no verb for; that limit is stated in the projection.
  A NON-disjoint post-commit advance blocks durably with
  ``pr_delivery_base_advanced_overlapping_after_commit``.

Delivery ends at PR_OPENED -> COMPLETE. There is no merge step, no merge
verb, and no way to widen the action set from here.
"""

import os

from workflow_authority import canonical
from workflow_authority.digest import framed_digest, sha256_hex

from pr_delivery import authorization as auth
from pr_delivery import candidate as candidate_module
from pr_delivery import pr_text
from pr_delivery import receipts
from pr_delivery.errors import DeliveryTransportError
from pr_delivery.store import DeliveryStore

PROBLEM_WRONG_REPOSITORY = "pr_delivery_wrong_repository"
PROBLEM_BRANCH_NOT_CHECKED_OUT = "pr_delivery_branch_not_checked_out"
PROBLEM_WRONG_REMOTE = "pr_delivery_wrong_remote"
PROBLEM_HEAD_NOT_AT_BASE = "pr_delivery_head_not_at_base"
PROBLEM_CANDIDATE_UNSTAGED = "pr_delivery_candidate_unstaged"
PROBLEM_BASE_REF_MISSING = "pr_delivery_base_ref_missing"
PROBLEM_BASE_NOT_FAST_FORWARD = "pr_delivery_base_not_fast_forward"
PROBLEM_BASE_OVERLAP = "pr_delivery_base_overlap"
PROBLEM_BASE_CI_RED = "pr_delivery_base_ci_red"
PROBLEM_REVERIFICATION_FAILED = "pr_delivery_reverification_failed"
PROBLEM_UNEXPECTED_REF_MOVEMENT = "pr_delivery_unexpected_ref_movement"
PROBLEM_COMMIT_RESULT_MISMATCH = "pr_delivery_commit_result_mismatch"
PROBLEM_PR_AMBIGUOUS = "pr_delivery_pr_ambiguous"
PROBLEM_PR_NOT_OPEN = "pr_delivery_pr_not_open"
PROBLEM_PR_RESULT_MISMATCH = "pr_delivery_pr_result_mismatch"
PROBLEM_TRANSPORT = "pr_delivery_transport_failure"
PROBLEM_BASE_ADVANCED_OVERLAPPING_AFTER_COMMIT = (
    "pr_delivery_base_advanced_overlapping_after_commit"
)
PROBLEM_MISSING_RECORD = "pr_delivery_missing_record"

BASE_CI_GREEN = "green"
BASE_CI_RED = "red"
BASE_CI_PENDING = "pending"
BASE_CI_NONE = "none"
_RED_CONCLUSIONS = (
    "failure", "timed_out", "cancelled", "action_required",
    "startup_failure",
)

OUTCOME_ADVANCED = "advanced"
OUTCOME_BLOCKED = "blocked"
OUTCOME_RETRY = "retry"
OUTCOME_REVOKED = "revoked"
OUTCOME_COMPLETE = "complete"
OUTCOME_NO_STEP = "no_step"


class MachineError(Exception):
    def __init__(self, message, problem):
        super(MachineError, self).__init__(message)
        self.problem = problem


class _Block(Exception):
    """Internal: stop the step durably with a named problem."""

    def __init__(self, problem, detail):
        super(_Block, self).__init__(detail)
        self.problem = problem
        self.detail = detail


class _Retry(Exception):
    """Internal: the step's transport failed in a way that may be safely
    retried after reconciliation; nothing is proven either way yet."""

    def __init__(self, detail):
        super(_Retry, self).__init__(detail)
        self.detail = detail


def _porcelain_unstaged(text):
    """The first porcelain line that is not a fully staged A/M/D entry."""
    for line in text.splitlines():
        if len(line) < 3:
            continue
        if line.startswith("??") or line[0] not in "AMD" or line[1] != " ":
            return line
    return None


class DeliveryMachine(object):
    """Drives one delivery record through its steps. Takes the store and
    the transport at construction; constructs neither."""

    def __init__(self, store, transport, clock):
        if not isinstance(store, DeliveryStore):
            raise MachineError("store must be a DeliveryStore",
                               auth.PROBLEM_BAD_TYPE)
        self.store = store
        self.transport = transport
        self.clock = clock

    # -- persistence --------------------------------------------------

    def load(self, delivery_id):
        document = self.store.load()
        record = document["deliveries"].get(delivery_id)
        if record is None:
            raise MachineError(
                "no PR delivery %r in the store" % (delivery_id,),
                PROBLEM_MISSING_RECORD,
            )
        return record

    def _persist(self, record):
        record["updated_at"] = self.clock()
        auth.validate_authorization(record)
        with self.store.lock():
            document = self.store.load()
            document["deliveries"][record["delivery_id"]] = record
            self.store.save(document)

    def _refresh(self, record):
        """Re-read the record from disk and fold the durable revocation
        state into the working copy. Returns True when revoked/expired
        (the working record is left as loaded from disk)."""
        fresh = self.load(record["delivery_id"])
        record["revocation"] = fresh["revocation"]
        if auth.is_revoked(record):
            return True
        return auth.is_expired(record, self.clock())

    # -- public drive -------------------------------------------------

    def revoke(self, delivery_id, by, reason):
        with self.store.lock():
            document = self.store.load()
            record = document["deliveries"].get(delivery_id)
            if record is None:
                raise MachineError("no PR delivery %r" % (delivery_id,),
                                   PROBLEM_MISSING_RECORD)
            now = self.clock()
            if not record["revocation"]["revoked"]:
                record["revocation"] = {
                    "revoked": True, "revoked_at": now, "revoked_by": by,
                    "reason": reason,
                }
            if record["phase"] not in auth.TERMINAL_PHASES:
                auth.apply_transition(record, auth.PHASE_REVOKED, now)
            record["updated_at"] = now
            auth.validate_authorization(record)
            self.store.save(document)
            return record

    def advance(self, delivery_id):
        """Run steps until the record is terminal, blocked, or a retryable
        failure is recorded. Returns the last outcome."""
        outcome = OUTCOME_NO_STEP
        while True:
            outcome = self.advance_once(delivery_id)
            if outcome != OUTCOME_ADVANCED:
                return outcome

    def advance_once(self, delivery_id):
        record = self.load(delivery_id)
        now = self.clock()
        if record["phase"] in auth.TERMINAL_PHASES:
            return (
                OUTCOME_COMPLETE
                if record["phase"] == auth.PHASE_COMPLETE else OUTCOME_BLOCKED
            )
        if auth.is_revoked(record):
            auth.apply_transition(record, auth.PHASE_REVOKED, now)
            self._persist(record)
            return OUTCOME_REVOKED
        if auth.is_expired(record, now):
            self._block(record, receipts.PROBLEM_EXPIRED,
                        "the authorization expired")
            return OUTCOME_BLOCKED
        step = auth.STEP_FOR_PHASE[record["phase"]]
        handler = {
            auth.STEP_BASE_REFRESH: self._step_base_refresh,
            auth.STEP_COMMIT: self._step_commit,
            auth.STEP_PUSH: self._step_push,
            auth.STEP_PR_CREATE: self._step_pr_create,
        }[step]
        try:
            handler(record)
        except _Block as block:
            self._block(record, block.problem, block.detail)
            return OUTCOME_BLOCKED
        except _Retry as retry:
            self._retry(record, step, retry.detail)
            return OUTCOME_RETRY
        except receipts.ReceiptError as exc:
            if exc.problem == receipts.PROBLEM_REVOKED:
                auth.apply_transition(record, auth.PHASE_REVOKED,
                                      self.clock())
                self._persist(record)
                return OUTCOME_REVOKED
            self._block(record, exc.problem, str(exc))
            return OUTCOME_BLOCKED
        except candidate_module.CandidateError as exc:
            self._block(record, exc.problem, str(exc))
            return OUTCOME_BLOCKED
        except DeliveryTransportError as exc:
            self._retry(record, step, "transport: %s" % exc)
            return OUTCOME_RETRY
        if record["phase"] == auth.PHASE_REVOKED:
            return OUTCOME_REVOKED
        return OUTCOME_ADVANCED

    # -- outcome recording ------------------------------------------

    def _block(self, record, problem, detail):
        now = self.clock()
        record["blocker"] = {
            "problem": problem,
            "detail": str(detail)[:auth.MAX_EVIDENCE_TEXT_CHARS],
            "recorded_at": now,
        }
        step = auth.STEP_FOR_PHASE.get(record["phase"])
        if step is not None:
            entry = record["steps"][step]
            if entry["state"] != auth.STEP_SUCCEEDED:
                entry["state"] = auth.STEP_BLOCKED
        if record["phase"] not in auth.TERMINAL_PHASES:
            auth.apply_transition(record, auth.PHASE_BLOCKED, now)
        self._persist(record)

    def _retry(self, record, step, detail):
        entry = record["steps"][step]
        receipt = entry["receipt"]
        if receipt is not None and receipt["state"] == auth.RECEIPT_EXECUTING:
            receipt["state"] = auth.RECEIPT_FAILED_RETRYABLE
            receipt["observed"] = {
                "error": str(detail)[:auth.MAX_EVIDENCE_TEXT_CHARS],
            }
            entry["state"] = auth.STEP_FAILED_RETRYABLE
        elif receipt is None or receipt["state"] in (
            auth.RECEIPT_VOID, auth.RECEIPT_SUCCEEDED,
        ):
            # Failure before any receipt was executing: nothing to void;
            # the step simply stays pending and the reason is durable in
            # the blocker-free retry note.
            if entry["state"] not in (auth.STEP_SUCCEEDED,
                                      auth.STEP_NOT_NEEDED):
                entry["state"] = auth.STEP_PENDING
        record["blocker"] = None
        self._persist(record)

    def _void(self, record, step):
        entry = record["steps"][step]
        receipt = entry["receipt"]
        if receipt is not None:
            receipt["state"] = auth.RECEIPT_VOID
            entry["voided"].append(receipt["receipt_id"])
            entry["receipt"] = None
        entry["state"] = auth.STEP_PENDING

    def _start(self, record, step, binding):
        """Derive, persist as executing, then re-check revocation. Returns
        the receipt, or raises ReceiptError(revoked) after voiding."""
        receipt = receipts.derive(record, step, binding, self.clock())
        entry = record["steps"][step]
        receipt["state"] = auth.RECEIPT_EXECUTING
        entry["receipt"] = receipt
        entry["state"] = auth.STEP_EXECUTING
        self._persist(record)
        if self._refresh(record):
            self._void(record, step)
            self._persist(record)
            raise receipts.ReceiptError(
                "revoked or expired before the %s effect" % step,
                receipts.PROBLEM_REVOKED if auth.is_revoked(record)
                else receipts.PROBLEM_EXPIRED,
            )
        return receipt

    def _succeed(self, record, step, observed, new_phase):
        entry = record["steps"][step]
        receipt = entry["receipt"]
        receipt["state"] = auth.RECEIPT_SUCCEEDED
        receipt["observed"] = observed
        entry["state"] = auth.STEP_SUCCEEDED
        auth.apply_transition(record, new_phase, self.clock())
        self._persist(record)

    # -- live facts ---------------------------------------------------

    def _check_repository(self, record, require_branch=True):
        path = record["repository"]["realpath"]
        transport = self.transport
        toplevel = transport.toplevel(path)
        if canonical_realpath(toplevel) != path:
            raise _Block(PROBLEM_WRONG_REPOSITORY,
                         "toplevel %r is not the authorized repository %r"
                         % (toplevel, path))
        git_dir = canonical_realpath(transport.git_dir(path))
        if git_dir != record["repository"]["git_dir_realpath"]:
            raise _Block(PROBLEM_WRONG_REPOSITORY,
                         "git dir %r is not the authorized %r"
                         % (git_dir, record["repository"]["git_dir_realpath"]))
        name = record["remote"]["name"]
        for label, live, bound in (
            ("configured URL", transport.remote_url(path, name),
             record["remote"]["url_exact"]),
            ("fetch URL", transport.remote_fetch_url(path, name),
             record["remote"]["url_fetch"]),
            ("push URL", transport.remote_push_url(path, name),
             record["remote"]["url_push"]),
        ):
            if live != bound:
                raise _Block(PROBLEM_WRONG_REMOTE,
                             "remote %r %s is %r, not the authorized %r"
                             % (name, label, live, bound))
        if require_branch:
            head_ref = transport.symbolic_ref_head(path)
            if head_ref != record["source"]["ref"]:
                raise _Block(PROBLEM_BRANCH_NOT_CHECKED_OUT,
                             "HEAD is %r, not the authorized %r"
                             % (head_ref, record["source"]["ref"]))

    def _live_candidate(self, record, base_oid):
        path = record["repository"]["realpath"]
        unstaged = _porcelain_unstaged(self.transport.status_porcelain(path))
        if unstaged is not None:
            raise _Block(
                PROBLEM_CANDIDATE_UNSTAGED,
                "the working tree is not exactly the staged candidate"
                " (porcelain %r)" % unstaged,
            )
        raw = self.transport.diff_index_raw(path, base_oid)
        return candidate_module.parse_raw_z(raw)

    def _require_candidate_matches(self, entries, record):
        problem, detail = candidate_module.compare(
            record["candidate"]["entries"], entries,
        )
        if problem is not None:
            raise _Block(problem, detail)

    def _remote_base(self, record):
        oid = self.transport.ls_remote(
            record["repository"]["realpath"], record["remote"]["name"],
            record["target_base"]["ref"],
        )
        if oid is None:
            raise _Block(PROBLEM_BASE_REF_MISSING,
                         "remote %r has no %r" % (
                             record["remote"]["name"],
                             record["target_base"]["ref"]))
        return oid

    def _prove_disjoint_advance(self, record, old_oid, new_oid):
        """Fetch the new base, prove fast-forward, prove disjointness.
        Returns the digest of the sorted base-changed path list."""
        path = record["repository"]["realpath"]
        self.transport.fetch_ref(path, record["remote"]["name"],
                                 record["target_base"]["ref"])
        if self.transport.rev_parse(path, new_oid) != new_oid:
            raise _Block(PROBLEM_BASE_REF_MISSING,
                         "fetched base %s is not present locally" % new_oid)
        if not self.transport.is_ancestor(path, old_oid, new_oid):
            raise _Block(
                PROBLEM_BASE_NOT_FAST_FORWARD,
                "base moved from %s to %s and that is not a fast-forward"
                % (old_oid, new_oid),
            )
        changed = candidate_module.paths_from_raw_z(
            self.transport.diff_tree_raw(path, old_oid, new_oid)
        )
        mine = [entry["path"] for entry in record["candidate"]["entries"]]
        conflicts = candidate_module.overlaps(mine, changed)
        if conflicts:
            raise _Block(
                PROBLEM_BASE_OVERLAP,
                "base advance touches candidate path(s): %s"
                % ", ".join("%r/%r" % pair for pair in conflicts[:8]),
            )
        return framed_digest(
            sorted(item.encode("utf-8") for item in changed)
        )

    def _base_ci(self, record, oid):
        try:
            runs = self.transport.gh_check_runs(
                record["repository"]["owner"], record["repository"]["repo"],
                oid,
            )
        except DeliveryTransportError as exc:
            raise _Retry("base CI could not be read: %s" % exc)
        if not runs:
            return BASE_CI_NONE
        red = [run for run in runs if run["conclusion"] in _RED_CONCLUSIONS]
        if red:
            raise _Block(
                PROBLEM_BASE_CI_RED,
                "base %s has red check(s): %s"
                % (oid, ", ".join(run["name"] for run in red[:8])),
            )
        if any(run["status"] != "completed" for run in runs):
            return BASE_CI_PENDING
        return BASE_CI_GREEN

    def _reverify(self, record):
        path = record["repository"]["realpath"]
        code, log, truncated = self.transport.run_reverification(
            list(record["reverification"]["argv"]), path,
        )
        observed = {
            "reverification_exit_status": code,
            "reverification_log_sha256": sha256_hex(log),
            "reverification_log_bytes": len(log),
            "reverification_log_truncated": truncated,
        }
        if code != 0:
            raise _Block(
                PROBLEM_REVERIFICATION_FAILED,
                "reverification exited %d (log sha256 %s)"
                % (code, observed["reverification_log_sha256"]),
            )
        return observed

    # -- BASE_REFRESH -------------------------------------------------

    def _step_base_refresh(self, record):
        step = auth.STEP_BASE_REFRESH
        self._check_repository(record)
        path = record["repository"]["realpath"]
        entry = record["steps"][step]
        current = record["base_state"]["current_base_oid"]
        receipt = entry["receipt"]
        if receipt is not None and receipt["state"] in (
            auth.RECEIPT_EXECUTING, auth.RECEIPT_FAILED_RETRYABLE,
        ):
            self._reconcile_base_refresh(record, receipt)
            return
        head = self.transport.head_oid(path)
        if head != current:
            raise _Block(PROBLEM_HEAD_NOT_AT_BASE,
                         "HEAD %s is not the current base %s"
                         % (head, current))
        self._require_candidate_matches(
            self._live_candidate(record, current), record,
        )
        remote_base = self._remote_base(record)
        if remote_base == current:
            entry["state"] = auth.STEP_NOT_NEEDED
            auth.apply_transition(record, auth.PHASE_BASE_CURRENT,
                                  self.clock())
            self._persist(record)
            return
        changed_digest = self._prove_disjoint_advance(record, current,
                                                      remote_base)
        binding = {
            "repository_realpath": path,
            "git_dir_realpath": record["repository"]["git_dir_realpath"],
            "remote_name": record["remote"]["name"],
            "remote_url_exact": record["remote"]["url_exact"],
            "remote_url_fetch": record["remote"]["url_fetch"],
            "source_ref": record["source"]["ref"],
            "base_ref": record["target_base"]["ref"],
            "old_base_oid": current,
            "new_base_oid": remote_base,
            "fast_forward": True,
            "base_changed_paths_digest": changed_digest,
            "candidate_identity_digest": record["candidate"][
                "identity_digest_sha256"
            ],
        }
        self._start(record, step, binding)
        try:
            self.transport.read_tree_two_way(path, current, remote_base)
            self.transport.update_ref(path, record["source"]["ref"],
                                      remote_base, current)
        except DeliveryTransportError as exc:
            raise _Retry("base refresh effect failed: %s" % exc)
        self._finish_base_refresh(record, remote_base)

    def _reconcile_base_refresh(self, record, receipt):
        path = record["repository"]["realpath"]
        old = receipt["binding"]["old_base_oid"]
        new = receipt["binding"]["new_base_oid"]
        head = self.transport.head_oid(path)
        if head == new:
            receipt["state"] = auth.RECEIPT_EXECUTING
            self._finish_base_refresh(record, new)
            return
        if head != old:
            raise _Block(PROBLEM_UNEXPECTED_REF_MOVEMENT,
                         "source ref is at %s, neither %s nor %s"
                         % (head, old, new))
        # Ref still at old: either nothing happened, or read-tree ran and
        # the compare-and-swap did not. The index tells which.
        live_new = candidate_module.parse_raw_z(
            self.transport.diff_index_raw(path, new)
        )
        if candidate_module.compare(record["candidate"]["entries"],
                                    live_new) == (None, None):
            receipt["state"] = auth.RECEIPT_EXECUTING
            try:
                self.transport.update_ref(path, record["source"]["ref"],
                                          new, old)
            except DeliveryTransportError as exc:
                raise _Retry("base refresh ref move failed: %s" % exc)
            self._finish_base_refresh(record, new)
            return
        live_old = candidate_module.parse_raw_z(
            self.transport.diff_index_raw(path, old)
        )
        if candidate_module.compare(record["candidate"]["entries"],
                                    live_old) == (None, None):
            self._void(record, auth.STEP_BASE_REFRESH)
            self._persist(record)
            return
        raise _Block(auth.PROBLEM_CANDIDATE_IDENTITY,
                     "after an interrupted base refresh the index matches"
                     " the candidate against neither base")

    def _finish_base_refresh(self, record, new_base):
        path = record["repository"]["realpath"]
        self._require_candidate_matches(
            self._live_candidate(record, new_base), record,
        )
        ci = self._base_ci(record, new_base)
        observed = self._reverify(record)
        observed["base_ci"] = ci
        observed["new_base_oid"] = new_base
        record["base_state"]["current_base_oid"] = new_base
        record["base_state"]["refreshed_at"] = self.clock()
        self._succeed(record, auth.STEP_BASE_REFRESH, observed,
                      auth.PHASE_BASE_CURRENT)

    # -- COMMIT -------------------------------------------------------

    def _step_commit(self, record):
        step = auth.STEP_COMMIT
        self._check_repository(record)
        path = record["repository"]["realpath"]
        entry = record["steps"][step]
        receipt = entry["receipt"]
        if receipt is not None and receipt["state"] in (
            auth.RECEIPT_EXECUTING, auth.RECEIPT_FAILED_RETRYABLE,
        ):
            if self._reconcile_commit(record, receipt):
                return
        current = record["base_state"]["current_base_oid"]
        head = self.transport.head_oid(path)
        if head != current:
            raise _Block(PROBLEM_HEAD_NOT_AT_BASE,
                         "HEAD %s is not the current base %s"
                         % (head, current))
        self._require_candidate_matches(
            self._live_candidate(record, current), record,
        )
        message = pr_text.revision_message(record)
        binding = {
            "repository_realpath": path,
            "git_dir_realpath": record["repository"]["git_dir_realpath"],
            "branch": record["source"]["branch"],
            "source_ref": record["source"]["ref"],
            "head_before": current,
            "staged_sha256": self.transport.staged_diff_sha256(path),
            "candidate_identity_digest": record["candidate"][
                "identity_digest_sha256"
            ],
            "expected_tree_oid": self.transport.write_tree(path),
            "committer_name": record["committer"]["name"],
            "committer_email": record["committer"]["email"],
            "message_sha256": pr_text.message_digest(record),
        }
        self._start(record, step, binding)
        try:
            self.transport.commit(
                path, record["committer"]["name"],
                record["committer"]["email"], message,
            )
        except DeliveryTransportError as exc:
            raise _Retry("commit effect failed: %s" % exc)
        self._observe_commit(record, binding)

    def _observe_commit(self, record, binding):
        path = record["repository"]["realpath"]
        new_head = self.transport.head_oid(path)
        parent, tree = self.transport.commit_parent_and_tree(path, new_head)
        if parent != binding["head_before"] or tree != binding[
            "expected_tree_oid"
        ]:
            raise _Block(
                PROBLEM_COMMIT_RESULT_MISMATCH,
                "HEAD %s (parent %s, tree %s) is not the exact expected"
                " result (parent %s, tree %s)"
                % (new_head, parent, tree, binding["head_before"],
                   binding["expected_tree_oid"]),
            )
        live = candidate_module.parse_raw_z(
            self.transport.diff_tree_raw(path, binding["head_before"],
                                         new_head)
        )
        self._require_candidate_matches(live, record)
        self._succeed(record, auth.STEP_COMMIT, {"commit_oid": new_head},
                      auth.PHASE_COMMITTED)

    def _reconcile_commit(self, record, receipt):
        """True when the step was settled (succeeded); False when the
        receipt was voided and a fresh derivation may proceed."""
        path = record["repository"]["realpath"]
        binding = receipt["binding"]
        head = self.transport.head_oid(path)
        if head == binding["head_before"]:
            self._void(record, auth.STEP_COMMIT)
            self._persist(record)
            return False
        parent, tree = self.transport.commit_parent_and_tree(path, head)
        if parent == binding["head_before"] and tree == binding[
            "expected_tree_oid"
        ]:
            receipt["state"] = auth.RECEIPT_EXECUTING
            self._observe_commit(record, binding)
            return True
        raise _Block(PROBLEM_UNEXPECTED_REF_MOVEMENT,
                     "HEAD moved to %s, which is not the expected commit"
                     % head)

    # -- post-commit base advance (M2) --------------------------------

    def _handle_post_commit_advance(self, record, commit_oid):
        current = record["base_state"]["current_base_oid"]
        remote_base = self._remote_base(record)
        if remote_base == current:
            return
        try:
            self._prove_disjoint_advance(record, current, remote_base)
        except _Block as block:
            if block.problem == PROBLEM_BASE_OVERLAP:
                raise _Block(PROBLEM_BASE_ADVANCED_OVERLAPPING_AFTER_COMMIT,
                             block.detail)
            raise
        path = record["repository"]["realpath"]
        live = candidate_module.parse_raw_z(
            self.transport.diff_tree_raw(path, current, commit_oid)
        )
        self._require_candidate_matches(live, record)
        self._base_ci(record, remote_base)
        record["base_state"]["advance_after_commit"] = {
            "old_base_oid": current,
            "new_base_oid": remote_base,
            "recorded_at": self.clock(),
        }
        record["base_state"]["current_base_oid"] = remote_base
        self._persist(record)

    # -- PUSH ---------------------------------------------------------

    def _commit_oid(self, record):
        receipt = record["steps"][auth.STEP_COMMIT]["receipt"]
        observed = receipt["observed"] if receipt else None
        oid = observed.get("commit_oid") if observed else None
        if not isinstance(oid, str) or len(oid) != 40:
            raise _Block(PROBLEM_COMMIT_RESULT_MISMATCH,
                         "no recorded commit to deliver")
        return oid

    def _step_push(self, record):
        step = auth.STEP_PUSH
        self._check_repository(record)
        path = record["repository"]["realpath"]
        commit_oid = self._commit_oid(record)
        entry = record["steps"][step]
        receipt = entry["receipt"]
        if receipt is not None and receipt["state"] in (
            auth.RECEIPT_EXECUTING, auth.RECEIPT_FAILED_RETRYABLE,
        ):
            if self._reconcile_push(record, receipt):
                return
        head = self.transport.head_oid(path)
        if head != commit_oid:
            raise _Block(PROBLEM_UNEXPECTED_REF_MOVEMENT,
                         "HEAD %s is not the delivered commit %s"
                         % (head, commit_oid))
        self._handle_post_commit_advance(record, commit_oid)
        remote = record["remote"]["name"]
        destination = record["source"]["ref"]
        expected_old = self.transport.ls_remote(path, remote, destination)
        if expected_old is None:
            expected_old = auth.ZERO_OID
        elif expected_old != commit_oid and not self.transport.is_ancestor(
            path, expected_old, commit_oid,
        ):
            # The destination already holds something this delivery
            # did not produce and cannot fast-forward: never attempt.
            raise _Block(PROBLEM_UNEXPECTED_REF_MOVEMENT,
                         "remote %r is at %s, which is not an ancestor of"
                         " the delivered commit %s"
                         % (destination, expected_old, commit_oid))
        binding = {
            "repository_realpath": path,
            "remote_name": remote,
            "remote_url_exact": record["remote"]["url_exact"],
            "remote_url_push": record["remote"]["url_push"],
            "source_ref": record["source"]["ref"],
            "source_commit": commit_oid,
            "destination_ref": destination,
            "expected_remote_old_oid": expected_old,
            "candidate_identity_digest": record["candidate"][
                "identity_digest_sha256"
            ],
        }
        if expected_old == commit_oid:
            # Already on the remote exactly: adopt, never re-push.
            self._start(record, step, binding)
            self._succeed(record, step, {"reconciled": True,
                                         "remote_oid": commit_oid},
                          auth.PHASE_PUSHED)
            return
        self._start(record, step, binding)
        try:
            self.transport.push(path, remote, record["source"]["ref"],
                                destination)
        except DeliveryTransportError as exc:
            raise _Retry("push effect failed: %s" % exc)
        self._observe_push(record, binding)

    def _observe_push(self, record, binding):
        path = record["repository"]["realpath"]
        remote_oid = self.transport.ls_remote(
            path, binding["remote_name"], binding["destination_ref"],
        )
        if remote_oid != binding["source_commit"]:
            raise _Block(PROBLEM_UNEXPECTED_REF_MOVEMENT,
                         "after the push %r is at %s, not %s"
                         % (binding["destination_ref"], remote_oid,
                            binding["source_commit"]))
        self._succeed(record, auth.STEP_PUSH,
                      {"reconciled": False, "remote_oid": remote_oid},
                      auth.PHASE_PUSHED)

    def _reconcile_push(self, record, receipt):
        path = record["repository"]["realpath"]
        binding = receipt["binding"]
        remote_oid = self.transport.ls_remote(
            path, binding["remote_name"], binding["destination_ref"],
        )
        if remote_oid == binding["source_commit"]:
            receipt["state"] = auth.RECEIPT_EXECUTING
            self._succeed(record, auth.STEP_PUSH,
                          {"reconciled": True, "remote_oid": remote_oid},
                          auth.PHASE_PUSHED)
            return True
        if (remote_oid or auth.ZERO_OID) == binding["expected_remote_old_oid"]:
            self._void(record, auth.STEP_PUSH)
            self._persist(record)
            return False
        raise _Block(PROBLEM_UNEXPECTED_REF_MOVEMENT,
                     "%r is at %s, neither the expected old %s nor the"
                     " delivered commit" % (binding["destination_ref"],
                                            remote_oid,
                                            binding["expected_remote_old_oid"]))

    # -- PR_CREATE ----------------------------------------------------

    def _matching_prs(self, record, head_sha):
        """``(exact_open, other_open, exact_not_open)`` over EVERY state.

        An exact pull request (same head branch, base branch and head
        commit) that is closed or merged is the deliberate stop
        (round-01 B3): a human closed it, or it already landed, and a
        second equivalent pull request would be the duplicate the
        request forbids. It blocks with ``pr_delivery_pr_not_open`` and
        the projection names it; re-authorization is the human's call.
        Other-head pull requests count only while open."""
        listed = self.transport.gh_pr_list(
            record["repository"]["owner"], record["repository"]["repo"],
            record["source"]["branch"], record["target_base"]["branch"],
        )
        exact, other, exact_not_open = [], [], []
        for item in listed:
            if not isinstance(item, dict):
                continue
            same_refs = (
                item.get("headRefName") == record["source"]["branch"]
                and item.get("baseRefName") == record["target_base"]["branch"]
            )
            if not same_refs:
                continue
            is_open = str(item.get("state", "")).upper() == "OPEN"
            if item.get("headRefOid") == head_sha:
                (exact if is_open else exact_not_open).append(item)
            elif is_open:
                other.append(item)
        return exact, other, exact_not_open

    def _pr_disposition(self, record, head_sha):
        """Adopt the one exact open pull request, report absence, or
        block: closed/merged exact => not open; anything else => ambiguous.
        Returns the item to adopt or None when none exists."""
        exact, other, exact_not_open = self._matching_prs(record, head_sha)
        if exact_not_open:
            item = exact_not_open[0]
            raise _Block(PROBLEM_PR_NOT_OPEN,
                         "pull request #%s for this exact head/base is %s;"
                         " a second equivalent pull request is never"
                         " created" % (item.get("number"),
                                       str(item.get("state")).lower()))
        if len(exact) == 1 and not other:
            return exact[0]
        if exact or other:
            raise _Block(PROBLEM_PR_AMBIGUOUS,
                         "%d exact and %d other open pull request(s) for"
                         " this head/base exist" % (len(exact), len(other)))
        return None

    def _record_pull_request(self, record, item):
        try:
            target = canonical.canonicalize_target_url(str(item.get("url")))
        except canonical.CanonicalizationError as exc:
            raise _Block(PROBLEM_PR_RESULT_MISMATCH,
                         "pull request URL did not canonicalize (%s)"
                         % exc.problem)
        if target.kind != canonical.KIND_PR or not canonical.same_repository_identity(
            target,
            canonical.canonicalize_repository_url(
                record["repository"]["repository_url"]
            ),
        ):
            raise _Block(PROBLEM_PR_RESULT_MISMATCH,
                         "pull request URL %r names another target"
                         % target.canonical_url)
        record["pull_request"] = {
            "number": target.number,
            "url": target.canonical_url,
            "head_sha": str(item.get("headRefOid")),
            "base_ref": record["target_base"]["ref"],
        }

    def _step_pr_create(self, record):
        step = auth.STEP_PR_CREATE
        self._check_repository(record, require_branch=False)
        path = record["repository"]["realpath"]
        head_sha = self._commit_oid(record)
        entry = record["steps"][step]
        receipt = entry["receipt"]
        if receipt is not None and receipt["state"] in (
            auth.RECEIPT_EXECUTING, auth.RECEIPT_FAILED_RETRYABLE,
        ):
            if self._reconcile_pr_create(record, receipt):
                return
        self._handle_post_commit_advance(record, head_sha)
        remote_oid = self.transport.ls_remote(
            path, record["remote"]["name"], record["source"]["ref"],
        )
        if remote_oid != head_sha:
            raise _Block(PROBLEM_UNEXPECTED_REF_MOVEMENT,
                         "remote %r is at %s, not the pushed %s"
                         % (record["source"]["ref"], remote_oid, head_sha))
        binding = {
            "owner": record["repository"]["owner"],
            "repo": record["repository"]["repo"],
            "remote_url_exact": record["remote"]["url_exact"],
            "head_branch": record["source"]["branch"],
            "head_sha": head_sha,
            "base_branch": record["target_base"]["branch"],
            "title_sha256": pr_text.title_digest(record),
            "body_sha256": pr_text.body_digest(record),
            "candidate_identity_digest": record["candidate"][
                "identity_digest_sha256"
            ],
        }
        self._start(record, step, binding)
        existing = self._pr_disposition(record, head_sha)
        if existing is not None:
            self._adopt_pr(record, existing, reconciled=True)
            return
        try:
            self.transport.gh_pr_create(
                binding["owner"], binding["repo"], binding["head_branch"],
                binding["base_branch"], pr_text.title(record),
                pr_text.body(record),
            )
        except DeliveryTransportError as exc:
            raise _Retry("pull request creation failed: %s" % exc)
        created = self._pr_disposition(record, head_sha)
        if created is None:
            raise _Block(PROBLEM_PR_RESULT_MISMATCH,
                         "after creation no exact open pull request exists")
        self._adopt_pr(record, created, reconciled=False)

    def _adopt_pr(self, record, item, reconciled):
        viewed = self.transport.gh_pr_view(
            record["repository"]["owner"], record["repository"]["repo"],
            int(item.get("number")),
        )
        binding = record["steps"][auth.STEP_PR_CREATE]["receipt"]["binding"]
        if (
            viewed.get("headRefOid") != binding["head_sha"]
            or viewed.get("baseRefName") != binding["base_branch"]
            or viewed.get("headRefName") != binding["head_branch"]
        ):
            raise _Block(PROBLEM_PR_RESULT_MISMATCH,
                         "pull request %r does not carry the exact head"
                         " and base" % item.get("number"))
        self._record_pull_request(record, viewed)
        self._succeed(record, auth.STEP_PR_CREATE,
                      {"reconciled": reconciled,
                       "number": record["pull_request"]["number"],
                       "url": record["pull_request"]["url"]},
                      auth.PHASE_PR_OPENED)
        auth.apply_transition(record, auth.PHASE_COMPLETE, self.clock())
        self._persist(record)

    def _reconcile_pr_create(self, record, receipt):
        head_sha = receipt["binding"]["head_sha"]
        existing = self._pr_disposition(record, head_sha)
        if existing is not None:
            receipt["state"] = auth.RECEIPT_EXECUTING
            self._adopt_pr(record, existing, reconciled=True)
            return True
        self._void(record, auth.STEP_PR_CREATE)
        self._persist(record)
        return False


def canonical_realpath(path):
    return os.path.realpath(str(path))
