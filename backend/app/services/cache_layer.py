"""查询缓存层领域服务：管理查询缓存的TTL、失效策略和缓存命中率。

跨模块数据流: data_service/query_builder -> cache_layer -> query_journal
当口径版本变更或数据更新时触发缓存失效。
预热委托查询构建器做完整计算，缓存的明细与正式执行一致。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService
from app.services.query_builder import QueryBuilderService


class CacheLayerService(BaseModuleService):
    MODULE = "cache_layer"
    ENTITY = "缓存策略"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["策略名称", "关联查询模板", "缓存时长", "失效触发条件", "命中率"]
    REQUIRED_FIELDS = ["策略名称", "关联查询模板", "缓存时长"]
    STATUS_ORDER = ["待生效", "已生效", "已停用"]
    ACTION_RULES = {"启用策略": "已生效", "停用策略": "已停用"}
    NEGATIVE_ACTIONS = ["停用策略"]

    def _init_metrics(self) -> dict[str, int]:
        return {"hits": 0, "misses": 0, "invalidations": 0}

    def apply_cache_strategy(self, query_template_id: int, ttl_seconds: int) -> str:
        """为查询模板应用缓存策略。

        跨模块数据流: cache_layer -> query_builder
        """
        template = store.find("query_builder", query_template_id)
        if template is None:
            return f"查询模板 {query_template_id} 不存在"

        cache_key_prefix = f"query:{template.get('关联指标','')}"
        # 设置 TTL - 持久化到策略记录中
        cache_key = f"strategy:{query_template_id}"
        store.cache_set(cache_key, {"template_id": query_template_id,
                                     "ttl": ttl_seconds}, ttl_seconds * 2)
        return f"缓存策略已应用到模板 #{query_template_id}，TTL={ttl_seconds}秒"

    def invalidate_by_metric(self, metric_name: str) -> dict[str, Any]:
        """根据指标名称失效所有关联缓存。

        跨模块数据流: caliber_version/query_builder -> cache_layer
        口径版本变更或指标数据更新时触发。
        """
        count = store.cache_invalidate_by_prefix(f"query:{metric_name}")
        return {"metric": metric_name, "invalidated_entries": count}

    def invalidate_by_template(self, template_id: int) -> dict[str, Any]:
        """根据查询模板失效缓存。"""
        template = store.find("query_builder", template_id)
        if template is None:
            return {"error": f"查询模板 {template_id} 不存在"}
        count = store.cache_invalidate_by_prefix(
            f"query:{template.get('关联指标','')}")
        return {"template_id": template_id, "invalidated_entries": count}

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息。"""
        cache_entry_count = len(store._cache)
        return {
            "cached_entries": cache_entry_count,
            "strategy_count": len(store.rows(self.MODULE)),
        }

    def warm_cache(self, metric_name: str) -> dict[str, Any]:
        """预热缓存：对指标下已发布模板做完整计算并缓存查询明细。

        跨模块数据流: cache_layer -> query_builder (pre-execute and cache)
        预热写入的是与正式执行一致的完整明细（口径版本、统计粒度、维度列），
        按模板关联缓存策略配置的缓存时长失效；后台预热不写查询日志。
        """
        templates = store.find_by_field("query_builder", "关联指标", metric_name)
        builder = QueryBuilderService()
        warmed = 0
        outcomes = []
        for tmpl in templates:
            if tmpl.get("status") == "已发布":
                outcome = builder.warm_template(int(tmpl.get("id", 0)))
                outcomes.append(outcome)
                if outcome.get("warmed"):
                    warmed += 1
        return {"metric": metric_name, "warmed_templates": warmed,
                "templates": outcomes}

    def warm_by_strategy(self, strategy_id: int) -> dict[str, Any]:
        """从缓存策略列表发起预热：按策略关联的查询模板预热。

        预热结果按策略配置的缓存时长失效。
        """
        strategy = store.find(self.MODULE, strategy_id)
        if strategy is None:
            return {"error": f"缓存策略 {strategy_id} 不存在"}
        template = self._resolve_strategy_template(strategy)
        if template is None:
            return {"error": f"缓存策略 {strategy_id} 未关联有效的查询模板"}
        outcome = QueryBuilderService().warm_template(int(template.get("id", 0)))
        return {"strategy_id": strategy_id,
                "template_id": template.get("id"),
                "metric": template.get("关联指标", ""),
                **outcome}

    @staticmethod
    def _resolve_strategy_template(strategy: dict[str, Any]) -> dict[str, Any] | None:
        """按策略的「关联查询模板」定位模板，支持模板名称或模板ID。"""
        ref = str(strategy.get("关联查询模板", "")).strip()
        if not ref:
            return None
        for tmpl in store.rows("query_builder"):
            if ref in (str(tmpl.get("id", "")), str(tmpl.get("模板名称", ""))):
                return tmpl
        return None