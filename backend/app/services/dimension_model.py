"""维度建模领域服务：管理星型/雪花型维度模型的注册、粒度和事实表映射。

跨模块数据流: dimension_model -> drill_down -> pivot_table -> report_template
维度模型定义了下钻路径的基础和透视表聚合的维度来源。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService


class DimensionModelService(BaseModuleService):
    MODULE = "dimension_model"
    ENTITY = "维度模型"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["模型名称", "事实表", "维度列表", "粒度定义", "发布状态"]
    REQUIRED_FIELDS = ["模型名称", "事实表", "维度列表"]
    STATUS_ORDER = ["设计态", "已上线", "已下线"]
    ACTION_RULES = {"上线模型": "已上线", "下线模型": "已下线"}
    NEGATIVE_ACTIONS = ["下线模型"]

    def get_model_with_analytics(self, entry_id: int) -> dict[str, Any] | None:
        """获取维度模型及其关联的分析配置。

        跨模块数据流: dimension_model -> drill_down -> pivot_table
        """
        model_data = store.get_dimension_model_data(entry_id)
        if model_data is None:
            return None

        model = model_data["model"]
        return {
            "model_id": entry_id,
            "model_name": model.get("模型名称", ""),
            "fact_table": model.get("事实表", ""),
            "dimensions": model.get("维度列表", "").split(",") if model.get("维度列表") else [],
            "granularity": model.get("粒度定义", ""),
            "status": model.get("status", ""),
            "drill_paths": [
                {"id": p["id"], "name": p.get("路径名称", ""),
                 "dimension_sequence": p.get("下钻维度序列", ""),
                 "status": p.get("status", "")}
                for p in model_data["drill_paths"]
            ],
            "pivot_configs": [
                {"id": c["id"], "name": c.get("配置名称", ""),
                 "row_dim": c.get("行维度", ""), "col_dim": c.get("列维度", ""),
                 "status": c.get("status", "")}
                for c in model_data["pivot_configs"]
            ],
            "total_drill_paths": len(model_data["drill_paths"]),
            "total_pivot_configs": len(model_data["pivot_configs"]),
        }

    def get_dimension_hierarchy(self, entry_id: int) -> dict[str, Any] | None:
        """从维度模型构建完整的维度层次结构。

        跨模块数据流: dimension_model -> drill_down（构建下钻树）
        """
        model = store.find(self.MODULE, entry_id)
        if model is None:
            return None

        dimensions = [d.strip() for d in model.get("维度列表", "").split(",") if d.strip()]

        # 查找关联的下钻路径构建层次
        drill_paths = store.find_by_field("drill_down", "起始指标", model.get("模型名称", ""))

        hierarchy = {
            "model_name": model.get("模型名称"),
            "root": model.get("事实表", ""),
            "levels": [],
        }
        for i, dim in enumerate(dimensions):
            level = {"level": i + 1, "dimension": dim,
                     "drill_paths": len([p for p in drill_paths if dim in str(p.get("下钻维度序列", ""))]),
                     "granularity": model.get("粒度定义", "")}
            hierarchy["levels"].append(level)

        return hierarchy