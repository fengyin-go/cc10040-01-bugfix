"""实验分群领域服务：管理实验分组规则、对照群设置和实验周期。

跨模块数据流: user_cohort -> experiment_group -> segment_compare
实验分组基于用户分群，结果用于分群对比分析。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService


class ExperimentGroupService(BaseModuleService):
    MODULE = "experiment_group"
    ENTITY = "分群规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "筛选条件", "群组大小", "实验周期", "对照群标识"]
    REQUIRED_FIELDS = ["规则名称", "筛选条件", "群组大小"]
    STATUS_ORDER = ["待生效", "已生效", "已结束"]
    ACTION_RULES = {"启用分群": "已生效", "结束分群": "已结束"}
    NEGATIVE_ACTIONS = []

    def get_experiment_groups(self, condition_cohort_name: str) -> list[dict[str, Any]]:
        """获取基于指定分群条件的所有实验分组。

        跨模块数据流: user_cohort -> experiment_group
        """
        all_groups = store.rows(self.MODULE)
        return [g for g in all_groups if condition_cohort_name in str(g.get("筛选条件", ""))]

    def evaluate_groups(self, group_a_id: int, group_b_id: int) -> dict[str, Any]:
        """评估两个实验分组并返回对比摘要。

        跨模块数据流: experiment_group -> segment_compare
        """
        group_a = store.find(self.MODULE, group_a_id)
        group_b = store.find(self.MODULE, group_b_id)

        if group_a is None or group_b is None:
            return {"error": "实验分组不存在"}

        try:
            a_size = int(group_a.get("群组大小", 0))
        except (ValueError, TypeError):
            a_size = 0
        try:
            b_size = int(group_b.get("群组大小", 0))
        except (ValueError, TypeError):
            b_size = 0

        # 模拟评估结果
        return {
            "group_a": {
                "name": group_a.get("规则名称"),
                "size": a_size,
                "is_control": group_a.get("对照群标识", "") == "是",
            },
            "group_b": {
                "name": group_b.get("规则名称"),
                "size": b_size,
                "is_control": group_b.get("对照群标识", "") == "是",
            },
            "size_ratio": round(b_size / max(1, a_size), 2),
            "analysis_status": "ready_for_compare",
        }