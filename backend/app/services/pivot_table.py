"""透视表配置领域服务：管理多维透视表的行列维度、聚合方式和共享配置。

跨模块数据流: drill_down -> pivot_table -> report_template
透视表消费下钻路径的维度结构，产出可嵌入报告模板的汇总视图。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService


class PivotTableService(BaseModuleService):
    MODULE = "pivot_table"
    ENTITY = "透视配置"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["配置名称", "数据源指标", "行维度", "列维度", "聚合方式"]
    REQUIRED_FIELDS = ["配置名称", "数据源指标", "行维度"]
    STATUS_ORDER = ["草稿", "已保存", "已共享"]
    ACTION_RULES = {"保存配置": "已保存", "共享配置": "已共享"}
    NEGATIVE_ACTIONS = []

    def compute_pivot(self, entry_id: int) -> dict[str, Any] | None:
        """执行透视表计算，返回二维汇总数据。

        跨模块数据流: dimension_model -> drill_down -> pivot_table
        """
        config = store.find(self.MODULE, entry_id)
        if config is None:
            return {"error": f"透视配置 {entry_id} 不存在"}

        metric = config.get("数据源指标", "")
        row_dim = config.get("行维度", "")
        col_dim = config.get("列维度", "")
        agg_method = config.get("聚合方式", "COUNT")

        # 查找关联维度模型
        dim_models = store.find_by_field("dimension_model", "模型名称", metric)
        dimensions_available = []
        if dim_models:
            dimensions_available = [
                d.strip() for d in dim_models[0].get("维度列表", "").split(",") if d.strip()
            ]

        # 模拟透视表计算
        row_values = [f"{row_dim}_{i+1}" for i in range(3)]
        col_values = [f"{col_dim}_{j+1}" for j in range(3)]

        grid = []
        for rv in row_values:
            for cv in col_values:
                grid.append({
                    "row": rv,
                    "column": cv,
                    "value": 50 * (row_values.index(rv) + 1) * (col_values.index(cv) + 1),
                })

        # 计算行汇总
        row_totals = {}
        for rv in row_values:
            row_totals[rv] = sum(g["value"] for g in grid if g["row"] == rv)

        return {
            "config_name": config.get("配置名称"),
            "metric": metric,
            "row_dimension": row_dim,
            "column_dimension": col_dim,
            "aggregation": agg_method,
            "grid": grid,
            "row_totals": row_totals,
            "row_count": len(row_values),
            "column_count": len(col_values),
            "available_dimensions": dimensions_available,
        }

    def share_to_report(self, entry_id: int, report_template_id: int) -> dict[str, Any]:
        """将透视表共享到报告模板。

        跨模块数据流: pivot_table -> report_template
        """
        config = store.find(self.MODULE, entry_id)
        if config is None:
            return {"error": f"透视配置 {entry_id} 不存在"}

        report = store.find("report_template", report_template_id)
        if report is None:
            return {"error": f"报告模板 {report_template_id} 不存在"}

        # 计算透视结果
        pivot_result = self.compute_pivot(entry_id)
        if "error" in pivot_result:
            return pivot_result

        # 将透视结果注入报告模板
        report["透视表结果"] = pivot_result

        return {
            "pivot_name": config.get("配置名称"),
            "report_name": report.get("模板名称"),
            "status": "已关联",
            "pivot_summary": f"行={pivot_result.get('row_count')} x 列={pivot_result.get('column_count')}",
        }