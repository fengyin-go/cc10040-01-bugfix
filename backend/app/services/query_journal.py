"""查询日志领域服务：记录和审计所有分析查询的调用链路和性能。

跨模块数据流: query_builder/data_service -> query_journal（记录查询）
              cache_layer -> query_journal（区分缓存与实时查询）
"""
from __future__ import annotations

from typing import Any
import time

from app.store import store
from app.services.base import BaseModuleService


class QueryJournalService(BaseModuleService):
    MODULE = "query_journal"
    ENTITY = "查询日志"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["查询时间", "操作人", "涉及指标", "查询语句摘要", "返回行数"]
    REQUIRED_FIELDS = ["查询时间", "操作人", "涉及指标"]
    STATUS_ORDER = ["正常", "慢查询", "异常终止"]
    ACTION_RULES = {"标记慢查询": "慢查询", "忽略告警": "正常"}
    NEGATIVE_ACTIONS = ["忽略告警"]

    def get_slow_queries(self, threshold_ms: float = 100) -> list[dict[str, Any]]:
        """获取慢查询列表。"""
        logs = store.query_logs()
        return [log for log in logs if log.get("duration_ms", 0) > threshold_ms]

    def get_query_stats_by_metric(self) -> dict[str, Any]:
        """按指标统计查询情况。

        跨模块数据流: query_journal -> metric_definition（关联指标分析）
        """
        logs = store.query_logs()
        by_metric: dict[str, dict[str, Any]] = {}
        for log in logs:
            metric_key = log.get("涉及指标", "未知")
            if metric_key not in by_metric:
                by_metric[metric_key] = {"count": 0, "slow": 0, "avg_duration_ms": 0.0,
                                          "total_duration_ms": 0.0}
            stats = by_metric[metric_key]
            stats["count"] += 1
            stats["total_duration_ms"] += log.get("duration_ms", 0)
            if log.get("status") == "慢查询":
                stats["slow"] += 1

        for metric_key, stats in by_metric.items():
            stats["avg_duration_ms"] = round(stats["total_duration_ms"] / max(1, stats["count"]), 2)
            del stats["total_duration_ms"]

        return by_metric

    def get_performance_insights(self) -> dict[str, Any]:
        """获取性能洞察：跨模块查询效率分析。"""
        logs = store.query_logs()
        if not logs:
            return {"message": "暂无查询日志"}

        total = len(logs)
        slow = sum(1 for log in logs if log.get("status") == "慢查询")
        cached = sum(1 for log in logs if "缓存" in str(log.get("查询语句摘要", "")))
        errors = sum(1 for log in logs if log.get("status") == "异常终止")

        return {
            "total_queries": total,
            "slow_query_ratio": round(slow / max(1, total), 2),
            "cache_hit_ratio": round(cached / max(1, total), 2),
            "error_count": errors,
            "recommendations": [
                f"{slow} 条慢查询建议优化口径或增加缓存" if slow > 0 else None,
                f"{cached} 条缓存命中，减少重复计算" if cached > 0 else "暂无缓存命中" if total > 0 else None,
            ]
        }