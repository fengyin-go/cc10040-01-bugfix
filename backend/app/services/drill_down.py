"""下钻分析领域服务：设计从汇总到明细的多层次下钻分析路径和联动规则。

跨模块数据流: dimension_model -> drill_down -> pivot_table -> report_template
下钻路径基于维度模型构建，结果可导出到透视表和报告模板。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService


class DrillDownService(BaseModuleService):
    MODULE = "drill_down"
    ENTITY = "下钻路径"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["路径名称", "起始指标", "下钻维度序列", "跳转规则", "默认展示"]
    REQUIRED_FIELDS = ["路径名称", "起始指标", "下钻维度序列"]
    STATUS_ORDER = ["待生效", "已生效", "已停用"]
    ACTION_RULES = {"启用路径": "已生效", "停用路径": "已停用"}
    NEGATIVE_ACTIONS = ["停用路径"]

    def execute_drill_down(self, entry_id: int, current_level: int = 0) -> dict[str, Any] | None:
        """执行下钻分析，从当前层级钻取到下一级明细。

        跨模块数据流: drill_down -> dimension_model（获取维度结构）
                     drill_down -> pivot_table（透视汇总结果）
        """
        path = store.find(self.MODULE, entry_id)
        if path is None:
            return {"error": f"下钻路径 {entry_id} 不存在"}
        if path["status"] != "已生效":
            return {"error": f"下钻路径 {entry_id} 未生效"}

        metric_name = path.get("起始指标", "")
        dimension_seq = [d.strip() for d in path.get("下钻维度序列", "").split(",") if d.strip()]

        if current_level >= len(dimension_seq):
            return {"error": "已达到最细粒度，无法继续下钻"}

        current_dimension = dimension_seq[current_level]

        # 查找关联的维度模型获取上下文
        dim_models = store.find_by_field("dimension_model", "模型名称", metric_name)
        model_dimensions = []
        if dim_models:
            model_dimensions = [
                d.strip() for d in dim_models[0].get("维度列表", "").split(",") if d.strip()
            ]

        # 模拟下钻结果
        drill_result = {
            "path_name": path.get("路径名称"),
            "current_level": current_level + 1,
            "dimension": current_dimension,
            "total_levels": len(dimension_seq),
            "next_dimension": dimension_seq[current_level + 1] if current_level + 1 < len(dimension_seq) else None,
            "model_dimensions": model_dimensions,
            "jump_rule": path.get("跳转规则", ""),
            "rows": [
                {"dimension_value": f"{current_dimension}_值{i+1}",
                 "metric_value": 100 * (i + 1) * (current_level + 1),
                 "can_drill_further": current_level + 1 < len(dimension_seq)}
                for i in range(5)
            ],
        }
        return drill_result

    def get_drill_tree(self, entry_id: int) -> dict[str, Any] | None:
        """构建下钻路径的完整树状结构。"""
        path = store.find(self.MODULE, entry_id)
        if path is None:
            return None

        dimension_seq = [d.strip() for d in path.get("下钻维度序列", "").split(",") if d.strip()]

        nodes = []
        for i, dim in enumerate(dimension_seq):
            nodes.append({
                "level": i + 1,
                "dimension": dim,
                "has_children": i + 1 < len(dimension_seq),
                "children_dimension": dimension_seq[i + 1] if i + 1 < len(dimension_seq) else None,
            })

        return {
            "path_id": entry_id,
            "path_name": path.get("路径名称"),
            "metric": path.get("起始指标"),
            "node_count": len(nodes),
            "tree": nodes,
        }

    def export_to_pivot(self, entry_id: int) -> dict[str, Any] | None:
        """将下钻路径的维度结构导出为透视表配置推荐。

        跨模块数据流: drill_down -> pivot_table
        """
        path = store.find(self.MODULE, entry_id)
        if path is None:
            return None

        dimension_seq = [d.strip() for d in path.get("下钻维度序列", "").split(",") if d.strip()]

        if len(dimension_seq) < 2:
            return {"error": "下钻路径维度少于两个，无法生成透视配置"}

        pivot_suggestion = {
            "source_path": path.get("路径名称"),
            "metric": path.get("起始指标"),
            "row_dimension": dimension_seq[0] if dimension_seq else "",
            "column_dimension": dimension_seq[1] if len(dimension_seq) > 1 else "",
            "aggregation_method": "SUM",
            "suggested_name": f"{path.get('路径名称')}_透视",
        }
        return pivot_suggestion