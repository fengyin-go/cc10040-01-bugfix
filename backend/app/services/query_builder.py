"""查询构建器领域服务：构建和缓存多维组合查询模板以加速分析。

消费口径版本的指标定义，生成查询模板供数据服务调用。
跨模块数据流: caliber_version -> query_builder -> data_service -> cache_layer -> query_journal

缓存约定：
- 缓存值是一份完整查询明细 {rows, caliber, dimensions, ...}，
  明细、维度列和口径标记来自同一次完整计算；
- 预热（cache_layer 发起）与正式回源共用同一份计算与写入逻辑；
- 写入前校验明细口径仍是当前生效口径，并发预热中迟到的旧口径结果直接丢弃；
- 命中前校验缓存明细完整且口径与当前生效口径一致，
  占位残留和口径错位的缓存不能作为正式命中。
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.store import store
from app.services.base import BaseModuleService

# 「30秒」「30」「5分钟」「1小时」等缓存时长配置
_TTL_PATTERN = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(秒|分钟|分|小时|s|sec|m|min|h)?\s*$",
    re.IGNORECASE,
)

DEFAULT_CACHE_TTL = 300


def parse_ttl_seconds(raw: Any) -> int | None:
    """把缓存时长配置解析成秒，无法解析时返回 None。"""
    text = str(raw or "").strip()
    match = _TTL_PATTERN.match(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "秒").lower()
    if unit in ("小时", "h"):
        value *= 3600
    elif unit in ("分钟", "分", "m", "min"):
        value *= 60
    seconds = int(value)
    return seconds if seconds > 0 else None


class QueryBuilderService(BaseModuleService):
    MODULE = "query_builder"
    ENTITY = "查询模板"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["模板名称", "关联指标", "维度组合", "过滤条件", "缓存策略"]
    REQUIRED_FIELDS = ["模板名称", "关联指标", "维度组合"]
    STATUS_ORDER = ["草稿", "已发布", "已过期"]
    ACTION_RULES = {"发布模板": "已发布", "过期模板": "已过期"}
    NEGATIVE_ACTIONS = []

    # ── 查询执行入口（数据服务接口共用） ──

    def execute_query(self, entry_id: int, operator: str = "系统",
                      use_cache: bool = True) -> dict[str, Any] | None:
        """执行查询模板并返回结果与日志。

        跨模块数据流: query_builder -> caliber_version(口径验证) -> cache_layer(缓存) -> query_journal(日志)
        命中时行内口径与响应标记来自同一份缓存明细；回源按请求执行时的
        生效口径交付完整明细（口径版本、统计粒度、维度列）。
        """
        template = store.find(self.MODULE, entry_id)
        if template is None:
            return {"error": f"查询模板 {entry_id} 不存在"}
        if template["status"] != "已发布":
            return {"error": f"查询模板 {entry_id} 未发布"}

        metric_name = template.get("关联指标", "")
        if not metric_name:
            return {"error": "查询模板未关联指标"}

        # 检查指标口径版本
        active_caliber = self._active_caliber(metric_name)
        if active_caliber is None:
            return {"error": f"指标 {metric_name} 没有已生效的口径版本"}

        cache_key = self._cache_key(template)

        # 尝试读取缓存：只接受完整且口径一致的明细
        if use_cache:
            cached = store.cache_get(cache_key)
            if cached is not None:
                detail = self._validated_detail(cached, template, active_caliber)
                if detail is not None:
                    t_start = time.time()
                    store.log_query(
                        metric_id=template.get("id", 0),
                        operator=operator,
                        sql_summary=f"缓存命中: {template.get('模板名称')}",
                        row_count=len(detail["rows"]),
                        duration_ms=(time.time() - t_start) * 1000,
                        status="正常",
                    )
                    return {"source": "cache", "template": template.get("模板名称"),
                            "caliber": detail.get("caliber"), "rows": detail["rows"]}
                # 占位残留或口径错位的缓存不能作为正式命中，清掉后回源
                store.cache_invalidate(cache_key)

        # 回源：完整计算查询明细
        t_start = time.time()
        detail = self.compute_template_detail(entry_id)
        if "error" in detail:
            return detail

        duration_ms = (time.time() - t_start) * 1000
        status = "慢查询" if duration_ms > 100 else "正常"

        # 写入查询日志
        store.log_query(
            metric_id=template.get("id", 0),
            operator=operator,
            sql_summary=f"查询模板: {template.get('模板名称')}, 维度: {template.get('维度组合')}",
            row_count=len(detail["rows"]),
            duration_ms=duration_ms,
            status=status,
        )

        # 写入缓存：按模板配置的缓存时长失效，迟到的旧口径结果不写
        stored = self.store_computed_detail(entry_id, detail)
        cache_ttl = self._resolve_cache_ttl(template)

        return {"source": "computed", "template": template.get("模板名称"),
                "caliber": detail.get("caliber"), "duration_ms": duration_ms,
                "cached_for_seconds": cache_ttl if stored else 0,
                "rows": detail["rows"]}

    # ── 完整明细计算（预热与回源共用） ──

    def compute_template_detail(self, entry_id: int) -> dict[str, Any]:
        """完整计算模板的查询明细：口径版本、统计粒度与维度列来自同一次计算。

        只计算，不写缓存、不记查询日志；预热和正式回源共用这份明细。
        """
        template = store.find(self.MODULE, entry_id)
        if template is None:
            return {"error": f"查询模板 {entry_id} 不存在"}

        metric_name = template.get("关联指标", "")
        if not metric_name:
            return {"error": "查询模板未关联指标"}

        active_caliber = self._active_caliber(metric_name)
        if active_caliber is None:
            return {"error": f"指标 {metric_name} 没有已生效的口径版本"}

        metric_data = store.find_by_field("metric_definition", "指标名称", metric_name)
        dimensions = [dim.strip()
                      for dim in str(template.get("维度组合", "")).split(",")
                      if dim.strip()]
        caliber = active_caliber.get("版本号", "")

        rows = []
        for idx, m in enumerate(metric_data):
            row = {"序号": idx + 1, "指标": metric_name,
                   "口径版本": caliber,
                   "计算口径": m.get("计算口径", ""),
                   "统计粒度": m.get("统计粒度", "")}
            for dim in dimensions:
                row[dim] = f"维度值_{idx + 1}"
            rows.append(row)

        return {"template_id": entry_id, "metric": metric_name,
                "caliber": caliber, "dimensions": dimensions,
                "rows": rows, "computed_at": time.time()}

    def store_computed_detail(self, entry_id: int, detail: dict[str, Any]) -> bool:
        """把完整明细写入缓存，按模板配置的缓存时长失效。

        写入前校验明细口径仍是当前生效口径：同一模板两轮预热并发时，
        晚到的旧口径结果直接丢弃，保证下一笔请求命中的是有效明细。
        """
        template = store.find(self.MODULE, entry_id)
        if template is None or not isinstance(detail, dict) or "rows" not in detail:
            return False
        cache_key = self._cache_key(template)
        ttl = self._resolve_cache_ttl(template)
        metric_name = template.get("关联指标", "")
        caliber = str(detail.get("caliber", ""))
        return store.cache_set_guarded(
            cache_key, detail, ttl,
            lambda: self._caliber_still_active(metric_name, caliber))

    def warm_template(self, entry_id: int) -> dict[str, Any]:
        """预热单个已发布模板：完整计算并缓存查询明细。

        后台预热不写查询日志、不增加查询数；中途失败不留下可用缓存。
        """
        template = store.find(self.MODULE, entry_id)
        if template is None:
            return {"warmed": False, "error": f"查询模板 {entry_id} 不存在"}
        if template.get("status") != "已发布":
            return {"warmed": False, "error": f"查询模板 {entry_id} 未发布，不能预热"}

        detail = self.compute_template_detail(entry_id)
        if "error" in detail:
            return {"warmed": False, "error": detail["error"]}

        stored = self.store_computed_detail(entry_id, detail)
        result: dict[str, Any] = {
            "warmed": stored,
            "template_id": entry_id,
            "caliber": detail.get("caliber"),
            "row_count": len(detail.get("rows", [])),
            "cached_for_seconds": self._resolve_cache_ttl(template) if stored else 0,
        }
        if not stored:
            result["error"] = "计算完成时口径已变更，迟到结果未写入缓存"
        return result

    # ── 缓存失效 ──

    def invalidate_query_cache(self, metric_name: str) -> int:
        """当口径版本变更时，失效指标相关的所有查询缓存。

        跨模块数据流: caliber_version -> query_builder -> cache_layer
        """
        return store.cache_invalidate_by_prefix(f"query:{metric_name}:")

    # ── 内部辅助 ──

    @staticmethod
    def _active_caliber(metric_name: str) -> dict[str, Any] | None:
        calibers = store.find_by_field("caliber_version", "所属指标", metric_name)
        return next((c for c in calibers if c.get("status") == "已生效"), None)

    @staticmethod
    def _caliber_still_active(metric_name: str, caliber: str) -> bool:
        active = QueryBuilderService._active_caliber(metric_name)
        return active is not None and str(active.get("版本号", "")) == caliber

    @staticmethod
    def _cache_key(template: dict[str, Any]) -> str:
        return (f"query:{template.get('关联指标', '')}:"
                f"{template.get('维度组合', '')}:{template.get('过滤条件', '')}")

    def _resolve_cache_ttl(self, template: dict[str, Any]) -> int:
        """按模板配置的缓存时长（缓存策略列表）解析 TTL。

        优先取 cache_layer 中关联该模板的策略「缓存时长」（已生效优先），
        其次回落到模板「缓存策略」文本，最后使用默认时长。
        """
        refs = {str(template.get("id", "")), str(template.get("模板名称", ""))} - {""}
        strategies = [
            row for row in store.rows("cache_layer")
            if str(row.get("关联查询模板", "")) in refs
        ]
        strategies.sort(key=lambda row: 0 if row.get("status") == "已生效" else 1)
        for strategy in strategies:
            ttl = parse_ttl_seconds(strategy.get("缓存时长"))
            if ttl:
                return ttl

        cache_policy = str(template.get("缓存策略", ""))
        if "长期" in cache_policy:
            return 3600
        if "短期" in cache_policy:
            return 60
        return DEFAULT_CACHE_TTL

    @staticmethod
    def _validated_detail(cached: Any, template: dict[str, Any],
                          active_caliber: dict[str, Any]) -> dict[str, Any] | None:
        """校验缓存明细可作为正式命中：结构完整且口径与当前生效口径一致。

        占位行、缺列明细和旧口径残留一律判为无效。
        """
        if not isinstance(cached, dict):
            return None
        rows = cached.get("rows")
        caliber = cached.get("caliber")
        if not isinstance(rows, list) or not isinstance(caliber, str) or not caliber:
            return None
        if caliber != str(active_caliber.get("版本号", "")):
            return None
        dimensions = [dim.strip()
                      for dim in str(template.get("维度组合", "")).split(",")
                      if dim.strip()]
        required = {"序号", "指标", "口径版本", "计算口径", "统计粒度", *dimensions}
        for row in rows:
            if not isinstance(row, dict) or not required.issubset(row.keys()):
                return None
            if str(row.get("口径版本", "")) != caliber:
                return None
        return cached
