"""Committed DI-REMOTE-2 regression surfaces.

These expectations replace the authoring-time working-tree diff.  They are
part of the test fixture, so a clean clone and an unrelated dirty path see the
same domain.  Historical Git objects and ``.herd/state`` are deliberately not
inputs.
"""

import hashlib
import re


DI_REMOTE_2_PRODUCTION_PYTHON = (
    "codex_gateway/role_turn.py",
    "herdr/heartbeat.py",
    "herdr/identity.py",
    "herdr/lifecycle.py",
    "herdr/observe.py",
    "herdr/tasks.py",
    "herdr/turns.py",
    "herdr/vintage.py",
    "target_runtime/broker.py",
    "target_runtime/cli.py",
    "target_runtime/dispatch.py",
    "target_runtime/evidence.py",
    "target_runtime/evidence_preservation.py",
    "target_runtime/ownership.py",
    "target_runtime/process_ownership.py",
    "target_runtime/readiness.py",
    "target_runtime/runtime.py",
    "target_runtime/spawn_stamp.py",
    "target_runtime/workspace_ownership.py",
    "target_runtime/workspace_trust.py",
    "telegram_operator/protocol.py",
)

DI_REMOTE_2_TEST_PYTHON = (
    "tests/__init__.py",
    "tests/_scope_hygiene.py",
    "tests/test_codex_gateway.py",
    "tests/test_docs_i8.py",
    "tests/test_guard_collapse.py",
    "tests/test_health.py",
    "tests/test_hermetic_git.py",
    "tests/test_identity.py",
    "tests/test_lifecycle.py",
    "tests/test_mission.py",
    "tests/test_release_narrative.py",
    "tests/test_model_substitution.py",
    "tests/test_observe.py",
    "tests/test_ownership.py",
    "tests/test_parity.py",
    "tests/test_readiness.py",
    "tests/test_reconcile_audit.py",
    "tests/test_role_turn.py",
    "tests/test_static.py",
    "tests/test_target_runtime.py",
    "tests/test_telegram_operator.py",
    "tests/test_vintage.py",
    "tests/test_workflow_authority.py",
    "tests/test_workspace_trust.py",
)

DI_REMOTE_2_PYTHON = (
    DI_REMOTE_2_PRODUCTION_PYTHON + DI_REMOTE_2_TEST_PYTHON
)

# The source-reading checks admitted by DI-REMOTE-2.  Function identity is a
# stable structural boundary: line movement does not change it, while removal
# of the source read or of the function makes the closure fail.
DI_REMOTE_2_SOURCE_CHECKS = frozenset({
    ("tests/test_docs_i8.py", "ClaimPinMapI8Tests", "test_every_mapped_claim_is_present_in_its_document"),
    ("tests/test_docs_i8.py", "ClaimPinMapI8Tests", "test_every_added_unit_has_a_row"),
    ("tests/test_docs_i8.py", "SchemaSurfaceIsProductionsTests", "test_readme_schema_version_is_the_one_production_emits"),
    ("tests/test_docs_i8.py", "SchemaSurfaceIsProductionsTests", "test_changelog_schema_version_is_the_one_production_emits"),
    ("tests/test_docs_i8.py", "SchemaSurfaceIsProductionsTests", "test_readme_top_level_keys_are_the_keys_production_emits"),
    ("tests/test_docs_i8.py", "SchemaSurfaceIsProductionsTests", "test_readme_named_new_sections_are_in_the_projection"),
    ("tests/test_docs_i8.py", "ModelHonestyIsProductionsTests", "test_readme_model_key_is_the_key_production_emits"),
    ("tests/test_docs_i8.py", "ModelHonestyIsProductionsTests", "test_readme_render_qualifier_is_the_one_production_writes"),
    ("tests/test_docs_i8.py", "ModelHonestyIsProductionsTests", "test_readme_forbidden_field_names_are_absent_from_the_projection"),
    ("tests/test_docs_i8.py", "ModelHonestyIsProductionsTests", "test_readme_quoted_diagnostic_is_the_delivered_sentence"),
    ("tests/test_docs_i8.py", "ModelHonestyIsProductionsTests", "test_readme_unset_marker_is_the_one_production_writes"),
    ("tests/test_docs_i8.py", "ModelHonestyIsProductionsTests", "test_changelog_model_key_and_qualifier_are_productions"),
    ("tests/test_docs_i8.py", "OperatorLimitsAreProductionsTests", "test_operator_model_key_is_the_key_production_emits"),
    ("tests/test_docs_i8.py", "OperatorLimitsAreProductionsTests", "test_operator_skew_label_is_the_one_the_render_emits"),
    ("tests/test_docs_i8.py", "OperatorLimitsAreProductionsTests", "test_operator_omitted_label_is_the_one_the_render_emits"),
    ("tests/test_docs_i8.py", "BoundsAreProductionsTests", "test_readme_string_bound_is_the_one_production_enforces"),
    ("tests/test_docs_i8.py", "BoundsAreProductionsTests", "test_readme_probe_bound_is_the_one_production_enforces"),
    ("tests/test_guard_collapse.py", "DelegationTests", "test_each_collapsed_guard_calls_the_package"),
    ("tests/test_identity.py", "BootstrapProducerTests", "test_the_producer_is_CALLED_BY_BOOTSTRAP"),
    ("tests/test_identity.py", "BootstrapProducerTests", "test_NO_TEST_IN_THIS_CLASS_hand_builds_a_binding"),
    ("tests/test_ownership.py", "WorkspaceOwnershipTests", "test_nothing_in_the_module_calls_production_close"),
    ("tests/test_ownership.py", "SpawnStampExecutableResolutionTests", "test_exec_handoff_is_PATH_aware_and_has_no_shell_fallback"),
    ("tests/test_ownership.py", "DestructiveOrderingClosureTests", "test_the_domain_is_a_FLOOR_and_its_bounds_are_named"),
    ("tests/test_ownership.py", "ReaperFunctionClosureTests", "test_every_declared_reaper_still_exists"),
    ("tests/test_ownership.py", "ReaperFunctionClosureTests", "test_the_domain_is_a_FLOOR_and_its_bounds_are_named"),
    ("tests/test_ownership.py", "SpawnSiteClosureTests", "test_the_count_is_a_FLOOR_and_the_depth_is_named"),
    ("tests/test_parity.py", "DuplicationCensusTests", "test_bootstrap_text_is_not_called_unimported_in_the_cli"),
    ("tests/test_parity.py", "GuardCollapseBiteTests", "test_a_fresh_hand_copy_of_a_collapsed_guard_is_reported"),
    ("tests/test_parity.py", "GuardCollapseBiteTests", "test_a_fresh_hand_copy_would_FAIL_the_guard"),
    ("tests/test_reconcile_audit.py", "SurvivedFalsificationTests", "test_listed_carries_what_reconciliation_actually_reads"),
    ("tests/test_reconcile_audit.py", "FalsifiedTests", "test_the_refusal_message_no_longer_claims_a_comparison_it_did_not_make"),
    ("tests/test_reconcile_audit.py", "EquivalentSpellingTests", "test_the_predicate_is_what_the_broker_runs"),
    ("tests/test_workspace_trust.py", "AtomicityAndConcurrencyTests", "test_di_never_heartbeats_its_lock"),
    ("tests/test_workspace_trust.py", "LockDiagnosticTests", "test_the_reclaim_window_is_disclosed"),
    ("tests/test_workspace_trust.py", "BrokerSourceOrderTests", "test_establishment_sits_between_materialize_and_ready"),
    ("tests/test_workspace_trust.py", "PopulationBoundaryTests", "test_no_launching_test_touches_the_real_configuration"),
    ("tests/test_workspace_trust.py", "PopulationBoundaryTests", "test_population_B_writes_no_copy_of_the_real_file"),
    ("tests/test_workspace_trust.py", "PopulationBoundaryTests", "test_population_B_launches_nothing"),
    ("tests/test_workspace_trust.py", "ZZSourceLevelClosureTests", "test_the_enumeration_is_derived_and_not_vacuous"),
    ("tests/test_workspace_trust.py", "ZZSourceLevelClosureTests", "test_the_vocabulary_covers_every_reflection_primitive_in_the_diff"),
    ("tests/test_workspace_trust.py", "ZZSourceLevelClosureTests", "test_a_plain_source_read_counts_as_a_primitive"),
    ("tests/test_workspace_trust.py", "ZZSourceLevelClosureTests", "test_the_census_follows_one_hop_of_helper_indirection"),
    ("tests/test_workspace_trust.py", "ZZSourceLevelClosureTests", "test_the_census_labels_its_number_a_FLOOR"),
    ("tests/test_workspace_trust.py", "ZZSourceLevelClosureTests", "test_every_source_level_check_declares_its_executed_pin"),
    ("tests/test_workspace_trust.py", "ZZMustHaveRunTests", "test_no_environment_variable_can_disable_the_arms"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_every_mapped_claim_is_present_in_its_document"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_doc_key_name_is_the_key_production_writes"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_doc_boundary_derivation_is_the_one_production_uses"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_doc_lock_path_is_the_one_production_creates"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_doc_receipt_prefix_is_the_one_production_writes"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_three_part_boundary_key_is_the_key_production_writes"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_three_part_failure_receipt_is_the_one_production_writes"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_operator_doc_key_name_is_the_key_production_writes"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_doc_revocation_matches_the_present_revocation_surface"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_doc_cli_version_is_the_one_the_module_derived_from"),
    ("tests/test_workspace_trust.py", "ClaimPinMapTests", "test_doc_blocked_phase_is_the_one_production_reaches"),
})


def unit_digest(text):
    """Stable identity for one canonical document unit."""
    flattened = re.sub(r"\s+", " ", text).lower()
    return hashlib.sha256(flattened.encode()).hexdigest()


# Fingerprints of the independently-derived normative units protected by the
# I1 and I8 maps.  The prose stays in its real document; the fixture holds only
# identity, so prose in a test cannot become evidence for a document claim.
I1_DOCUMENT_UNIT_DIGESTS = {
    "SECURITY.md": frozenset({
        "7ae8eb931b4ad3b4fbeb8b2dfa6be46426e6cfdc46f2c0c1f8ffe748a2c4973d", "39793ac28f79ed001554a017069cb7e8de8182aafa0ea6f4cca8660049295f6d", "da2057d040c9067199d2fdcc1ecbe37d2a0ce3ed38e26b058f7368f909bc2fb4", "12299407b09f8aa191997f315bc4cca992fe4890921dc1521f3acb6bb1187c72", "2ea8cc9e43a120cca457ad8f9e9bdc97db7a0f18975508b5eb385e94949f778c", "84ba8746e239b2bcaec664bd6ce0696d515868aff4852c5cd0dacf245f6da459", "8f2e6b172b464e25eb04ed20b14b845f5bc3d2c155e8f5a9f4d8ed90ec3fd967", "b769528e381fee08ee992b9ee292d856249f637253c02728f0a8f8e2c4930dc2", "ec24296f0c18a12ee4d7410110027f4684415fde97829b1eebda87209171c317", "18a850c96ded7d0e5fc52fb8af8459c4c763bd65eb6c5bb6b146fa762c7fde10", "a8e369b7c91eaac59553f4399b32f9197fd1422ea24147304b20116764ddab1e", "de3da7b7542346b1cfd93b5874c91c2e91df0507317881ee83e7de9a00b24c21", "0540d45ce318ecc28877d2246e276979fe428dc37b4a7cda28ea739c1a4f7e5d", "e1e324a27dd05890b66f93737b82ab1184066ba76efb08ca1b56e4676b7d81c4", "e078537dca24984c6df8afb8744fd61ef5cc60bc312a86bd394b6e2d679a74c1", "665f1dd1770d5f74221f3968b09496c80256fde029b8b119496a98a35b8a2bbb", "10275a61be75264e5894ec50742f79d2e8759047deacf7cfc2bd40ab472cbb98", "97fc8e145525c18d21e8ee91f54377392d4e204c4a8a7f82b79bb8c13708704a", "f73e80d98c3ba6155ce4edadb8d8847237d7f78c73fe1eab776f8ff57624c8f9", "13ed7daf21f7deb38b4b44207c1444a8ee721a3ccbdec3b6cb2a3790ccbc97f1", "d89ef347dcf9b2a72597d839f25e0244b30a1741ad3236fe90f44b929b8862e7", "a3421cef3ba5b9f81d61db6c9536851139ef889b822f73cf3d4b70a4a2b88b2a", "47690d2a5572c6e7fa604d1a3f386cf95dae95de7f09ca4593494fa2467bf53a", "8d79184fa44c58ef3d7b570026d677f4a5d3a21c7f5175a708d845d69889e7f2",
    }),
    "OPERATOR_PROTOCOL.md": frozenset({
        "dadb15e66ec6f3e229995c4db03cee8e37e1df5718c44764032fa484ef713a54", "aa6d92bd2bf5c103bf497e26c03ec29a4e81b8bdc8a69810cc3ff2c38f7f7252", "85ea812a8dd74f9f26543a86c58b7a21b9ebe0d20283b969cc19e2834720b849", "b2d7e5fc5fa6632d73cb694f0e2403d8cd52df14ba9ae9648710f5cd4b6772c4", "ca2ebbfcfdf3fe22361fae3500ba71b3fa4618b0c77dbad62650b7a20ddd5338", "8d04c43deac1bb28af6e6ed2ee80f66a7ac3f49676bdd0eaa18760306fa9df77", "83646ba798a6c5c4cfd8e8c9290968801f610b984f83207fa6dd4ed09ae4b718", "bdd43abfe1da592eb6fb25790e4838df108a1ef1b8234b391abc9d6e5564fd82", "5eb733693dca0c7496a35c4f8e2d2f455252017f021931b15ba01610335a3e15", "6cdf21a402fee0a420407d305cc5b01d23f04b4d5aa926ef32ef01be7cab2282", "ed98edca5284400190b801430cd688c640527b93fd0fefbd0b4010c301c759ce", "093a7ea74af9be08494da5f92f6c21a4c0b3792dec9e66c59a3f0c69db7acc4e",
    }),
}

I8_DOCUMENT_UNIT_DIGESTS = {
    # The exactly-once caveat bullet and the observability units moved out of
    # README byte-for-byte during the product-story rewrite, and moved again,
    # byte-for-byte, into the consolidated docs/operations.md; the digests are
    # unchanged because the text is.
    "docs/operations.md": frozenset({
        "ffbd6266ae2091f31b5dddeb6bf2a74a0a9a1b9922576d1a4f77a1a5e3144229",
        "e843bcad540db023866357a159aba7355e43d61fb923c1e4e3c42059c3c85001", "ef642453a8384806ee3447f4bb65bb15f4a0d855cedae9f81657b084bc97ae9b", "c58203f1ee8c3117e88abc0a4fa058d130176a7e5771e0e9ed368442bb6eca7f", "1e4d44205b7bf99ec6bcbae873b0ef35b856cc57760a05cfb62c28b8ae3f3a0a", "1dd632c26f655ea893da84ca68d328d0045aa3fef5543ebf1eee7b86a3391b1b", "972f8f4fc3dc629f537a14c8db94b421a3e7090806eb827952a5782e1e6c8043", "82fc7cc0401f4d709f67f55194c4108aa66082d9ad127725d81b6c509bbf76f8", "f655f5c1b1d8039502cbe15a385aee31a40651a2d6bc2f543153ea84409920e6", "f6dbdac7ac58392f8c25dc2f0bad3bcdadfd4a8ce8a63e7f338ae9b17b3aaee7", "7e4477cb2c823e03761899642ac17dafd3f13563ac5c4c7ee2a07d3d7e6e57ea", "864c2d2e76e77e190ca718066efe08ebd8e9b14785c05527a6f5e16aec11e9ad",
    }),
    "CHANGELOG.md": frozenset({
        "bca1007cdc1f5b8960f24b3bf716dfffcaeb03376d7962adbe9c1a8541bd6263", "bf5bf14bf1c4e3ebb0ad8a3bcb108bd14fe4971f0b230520e5bfd526575521d3", "37d34e283c88fab8f6c92293ee4b278c5ff66568" "02ba4c3dd13bfe3b0c02" "8022",
    }),
    "SECURITY.md": frozenset({
        "39793ac28f79ed001554a017069cb7e8de8182aafa0ea6f4cca8660049295f6d", "da2057d040c9067199d2fdcc1ecbe37d2a0ce3ed38e26b058f7368f909bc2fb4", "12299407b09f8aa191997f315bc4cca992fe4890921dc1521f3acb6bb1187c72", "2ea8cc9e43a120cca457ad8f9e9bdc97db7a0f18975508b5eb385e94949f778c",
    }),
    "OPERATOR_PROTOCOL.md": frozenset({
        "dadb15e66ec6f3e229995c4db03cee8e37e1df5718c44764032fa484ef713a54", "aa6d92bd2bf5c103bf497e26c03ec29a4e81b8bdc8a69810cc3ff2c38f7f7252", "85ea812a8dd74f9f26543a86c58b7a21b9ebe0d20283b969cc19e2834720b849", "b2d7e5fc5fa6632d73cb694f0e2403d8cd52df14ba9ae9648710f5cd4b6772c4", "ca2ebbfcfdf3fe22361fae3500ba71b3fa4618b0c77dbad62650b7a20ddd5338",
    }),
}


def protected_document_units(text, expected_digests, splitter):
    """Return protected current units and any expected identities missing."""
    current = {
        unit_digest(unit): unit
        for unit in splitter(text) if len(unit) >= 40
    }
    return (
        [current[digest] for digest in expected_digests if digest in current],
        sorted(expected_digests - set(current)),
    )
