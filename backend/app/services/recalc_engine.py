"""口径重算引擎领域服务：根据新口径版本重算历史数据。

跨模块数据流: metric_definition -> caliber_version -> recalc_engine -> query_builder(缓存失效)
口径变更时触发重算，重算完成后失效相关缓存。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store
from app.services.base import BaseModuleService


class RecalcEngineService(BaseModuleService):
    MODULE = "recalc_engine"
    ENTITY = "重算任务"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["关联指标", "新口径版本", "历史时间范围", "重算状态", "产出差异"]
    REQUIRED_FIELDS = ["关联指标", "新口径版本", "历史时间范围"]
    STATUS_ORDER = ["待调度", "重算中", "已完成", "重算失败"]
    ACTION_RULES = {"启动重算": "重算中", "取消重算": "重算失败"}
    NEGATIVE_ACTIONS = []

    def start_recalc(self, entry_id: int) -> dict[str, Any] | None:
        """启动重算任务。

        跨模块数据流: meter_definition -> caliber_version -> recalc_engine
        """
        task = store.find(self.MODULE, entry_id)
        if task is None:
            return {"error": f"重算任务 {entry_id} 不存在"}

        metric_name = task.get("关联指标", "")
        new_version = task.get("新口径版本", "")
        time_range = task.get("历史时间范围", "")

        # 查找指标及其当前口径
        calibers = store.find_by_field("caliber_version", "所属指标", metric_name)
        active_caliber = next((c for c in calibers if c.get("status") == "已生效"), None)
        old_version = active_caliber.get("版本号") if active_caliber else "unknown"

        # 模拟重算 - 计算差异
        task["status"] = "重算中"
        task["pending"] = True

        diff_summary = f"口径从 {old_version} 变更为 {new_version}，覆盖时间范围 {time_range}"
        diff_percentage = 5.5  # 模拟差异百分比

        # 完成重算
        task["status"] = "已完成"
        task["pending"] = False
        task["产出差异"] = f"{diff_summary}，历史数据差异约 {diff_percentage}%"

        # 记录重算历史
        store.record_recalc(
            metric_id=0,
            old_version=old_version,
            new_version=new_version,
            time_range=time_range,
            diff_summary=task.get("产出差异", ""),
        )

        # 失效该指标所有缓存
        from app.services.cache_layer import CacheLayerService
        cache_svc = CacheLayerService()
        invalidation = cache_svc.invalidate_by_metric(metric_name)

        return {
            "task_id": entry_id,
            "metric": metric_name,
            "old_caliber": old_version,
            "new_caliber": new_version,
            "time_range": time_range,
            "diff_summary": task.get("产出差异"),
            "status": "已完成",
            "cache_invalidated": invalidation.get("invalidated_entries", 0),
        }

    def get_recalc_history(self, metric_name: str | None = None) -> list[dict[str, Any]]:
        """获取重算历史记录。"""
        history = store.recalc_history()
        if metric_name:
            history = [h for h in history if h.get("关联指标") == metric_name]
        return history

    def estimate_recalc_impact(self, entry_id: int) -> dict[str, Any] | None:
        """预估算重算影响范围。

        跨模块数据流: recalc_engine -> query_builder（预估缓存失效范围）
                     recalc_engine -> data_service（预估接口影响）
        """
        task = store.find(self.MODULE, entry_id)
        if task is None:
            return {"error": f"重算任务 {entry_id} 不存在"}

        metric_name = task.get("关联指标", "")

        # 查找关联的查询模板和服务接口
        queries = store.find_by_field("query_builder", "关联指标", metric_name)
        services = [row for row in store.rows("data_service")
                    if metric_name in str(row.get("关联指标或模型", ""))]
        subscriptions = store.find_by_field("data_subscription", "关联指标", metric_name)

        return {
            "metric": metric_name,
            "affected_query_templates": len(queries),
            "affected_service_apis": len(services),
            "affected_subscriptions": len(subscriptions),
            "estimated_cache_invalidation": len([q for q in queries if q.get("缓存策略")]),
            "recommended_action": "confirm_and_proceed",
        }