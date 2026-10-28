"""Contract registry (plan-v2 §5.2): K1-K10 kernel contracts + C1-C10 semantic.

Every entry must be covered by at least one *concrete* test.  The meta-test
``tests/test_contract_registry.py`` asserts the matrix has no empty or unknown
entries and that every referenced platform test actually exists in source.

``platform`` entries are node ids inside this repository; ``kernel`` entries
are node ids inside the ontoRK repository (``../ontoRK``), checked only when
that checkout is present.
"""

from __future__ import annotations

CONTRACTS: list[dict] = [
    {
        "id": "K1",
        "name": "编译产物被内核 parse_schema 接受",
        "platform": ["tests/test_ontology_compiler.py::test_kernel_loader_accepts_compiled_payload"],
        "kernel": ["tests/test_loader.py::test_parse_minimal_ontology"],
    },
    {
        "id": "K2",
        "name": "内核 schema 语义被执行（required/enum/range/身份/唯一）",
        "platform": ["tests/test_constraints_platform.py::test_enum_and_range_rejected_at_write_time"],
        "kernel": [
            "tests/test_constraints.py::test_required_enforced_on_create",
            "tests/test_constraints.py::test_type_enum_range_and_unit_are_enforced",
            "tests/test_constraints.py::test_identity_is_enforced",
            "tests/test_constraints.py::test_unique_is_enforced_on_create_and_update",
        ],
    },
    {
        "id": "K3",
        "name": "状态写入是单事务且失败原子（delta / invoke）",
        "platform": ["tests/test_constraints_platform.py::test_delta_is_single_transaction"],
        "kernel": [
            "tests/test_apply_delta.py::test_apply_delta_is_atomic_on_invalid_object",
            "tests/test_runtime.py::test_business_error_is_atomic",
        ],
    },
    {
        "id": "K4",
        "name": "执行结果三态（succeeded/failed/unknown）",
        "platform": ["tests/test_idempotency.py::test_timeout_is_unknown_and_retried"],
        "kernel": ["tests/test_temporal.py::test_invoke_timeout_reports_unknown_status"],
    },
    {
        "id": "K5",
        "name": "投影从源端同步到运行时（增量 + 删除传播 + 幂等）",
        "platform": [
            "tests/test_projection.py::test_projection_rerun_is_idempotent",
            "tests/test_contract_verifier.py::test_source_deletion_propagates",
        ],
        "kernel": ["tests/test_delete.py::test_sqlite_store_deletes_objects_and_relations"],
    },
    {
        "id": "K6",
        "name": "Release 不可变 / 部署指针可回滚 / 失败不残留",
        "platform": [
            "tests/test_release_events.py::test_failed_publish_leaves_no_release",
            "tests/test_release_events.py::test_rollback_points_to_previous_release",
        ],
        "kernel": [],
    },
    {
        "id": "K7",
        "name": "领域事件与状态同事务（outbox 可重投）",
        "platform": [
            "tests/test_release_events.py::test_event_committed_with_state_and_dispatched",
            "tests/test_release_events.py::test_failed_subscriber_is_retried",
        ],
        "kernel": [],
    },
    {
        "id": "K8",
        "name": "provider/执行器超时与错误分类不崩溃",
        "platform": [
            "tests/test_failure_injection.py::test_llm_timeout_is_unknown_not_failure",
            "tests/test_failure_injection.py::test_provider_429_is_a_failed_run",
        ],
        "kernel": ["tests/test_python_executor.py::test_timeout_error_is_not_wrapped"],
    },
    {
        "id": "K9",
        "name": "权限边界：越权写被拒绝且审计",
        "platform": [
            "tests/test_governance.py::test_editor_can_edit_but_not_manage_members",
            "tests/test_governance.py::test_outsider_denied",
        ],
        "kernel": [],
    },
    {
        "id": "K10",
        "name": "并发：不丢更新、幂等键只变一次、超时 unknown",
        "platform": [
            "tests/test_coordination.py::test_execution_lease_prevents_lost_updates",
            "tests/test_idempotency.py::test_same_key_applies_once",
        ],
        "kernel": [],
    },
    {
        "id": "C1",
        "name": "三态结果不被渲染为二态",
        "platform": ["tests/test_values_errors.py::test_error_taxonomy_is_stable"],
        "kernel": ["tests/test_temporal.py::test_function_result_status_is_validated"],
    },
    {
        "id": "C2",
        "name": "幂等：同 key 重复调用世界只变一次",
        "platform": ["tests/test_idempotency.py::test_same_key_applies_once"],
        "kernel": [],
    },
    {
        "id": "C3",
        "name": "事件 envelope 稳定（event_id/type/version/ts/aggregate）",
        "platform": ["tests/test_release_events.py::test_unknown_event_type_is_rejected"],
        "kernel": [],
    },
    {
        "id": "C4",
        "name": "能力后置条件由独立 verifier 判定",
        "platform": [
            "tests/test_contract_verifier.py::test_postcondition_verified",
            "tests/test_contract_verifier.py::test_postcondition_violation_reported",
        ],
        "kernel": [],
    },
    {
        "id": "C5",
        "name": "授权矩阵：动作/目标/角色",
        "platform": ["tests/test_governance_matrix.py::test_action_matrix_requires_the_documented_role"],
        "kernel": [],
    },
    {
        "id": "C6",
        "name": "发布原子性：失败不半发布、不移动指针",
        "platform": ["tests/test_release_events.py::test_failed_publish_leaves_no_release"],
        "kernel": [],
    },
    {
        "id": "C7",
        "name": "本体 profile/语义校验（身份必填、状态机、基数、事件体裁）",
        "platform": [
            "tests/test_ontology_semantics.py::test_identity_must_name_required_scalar_fields",
            "tests/test_ontology_semantics.py::test_relation_cardinality_and_inverse_are_validated",
        ],
        "kernel": ["tests/test_constraints.py::test_one_to_one_rejects_second_edge_on_either_end"],
    },
    {
        "id": "C8",
        "name": "身份一致性（跨来源冲突被检出）",
        "platform": ["tests/test_constraints_platform.py::test_identity_collision_detected_across_sources"],
        "kernel": ["tests/test_apply_delta.py::test_apply_delta_enforces_identity"],
    },
    {
        "id": "C9",
        "name": "约束可执行（编译进内核而不止声明）",
        "platform": ["tests/test_ontology_compiler.py::test_compiles_to_kernel_schema_dict"],
        "kernel": ["tests/test_constraints.py::test_loader_parses_constraints"],
    },
    {
        "id": "C10",
        "name": "版本兼容性：收窄变更被拒绝并给迁移建议",
        "platform": [
            "tests/test_ontology_revision.py::test_narrowing_revision_is_rejected_with_migration_advice",
            "tests/test_ontology_revision.py::test_widening_revision_is_publishable",
        ],
        "kernel": [],
    },
]
