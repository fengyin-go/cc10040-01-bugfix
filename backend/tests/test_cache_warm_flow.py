"""缓存预热与口径一致性测试。

场景：给含省份列的已发布模板配置三十秒缓存时长，从缓存策略列表发起预热；
并发两轮预热时迟到的旧口径结果被丢弃；命中与回源计入性能洞察，
后台预热不增加查询数。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.services.cache_layer import CacheLayerService
from app.services.caliber_version import CaliberVersionService
from app.services.data_service import DataServiceService
from app.services.query_builder import QueryBuilderService
from app.services.query_journal import QueryJournalService
from app.store import store

METRIC = "预热验证指标_订单成交额"
TEMPLATE_NAME = "预热验证模板_省份明细"
API_NAME = "预热验证接口_省份明细"
STRATEGY_NAME = "预热验证策略_省份明细"
CACHE_KEY = f"query:{METRIC}:省份,渠道:"


def _find_or_add(module: str, field: str, value: str, row: dict) -> dict:
    found = store.find_by_field(module, field, value)
    if found:
        return found[0]
    store.rows(module).append(row)
    return row


@pytest.fixture()
def province_setup():
    """搭建含省份列的已发布模板、两版口径、数据服务接口和30秒缓存策略。"""
    metric = _find_or_add("metric_definition", "指标名称", METRIC, {
        "id": store.next_id("metric_definition"), "status": "已发布",
        "pending": False, "abnormal": False,
        "指标名称": METRIC, "计算口径": "sum(订单金额)", "统计粒度": "日",
        "数据源": "orders_fact", "业务域": "交易",
    })
    v1 = _find_or_add("caliber_version", "版本号", "v1.0", {
        "id": store.next_id("caliber_version"), "所属指标": METRIC,
        "版本号": "v1.0", "口径描述": "旧口径：含取消订单", "变更类型": "初始创建",
        "生效时间": "2026-10-01", "status": "已生效",
        "pending": True, "abnormal": False,
    })
    v2 = _find_or_add("caliber_version", "版本号", "v2.0", {
        "id": store.next_id("caliber_version"), "所属指标": METRIC,
        "版本号": "v2.0", "口径描述": "新口径：剔除取消订单", "变更类型": "口径调整",
        "生效时间": "2026-11-01", "status": "待生效",
        "pending": True, "abnormal": False,
    })
    # 只动本指标自己的口径版本，避免影响其他用例
    for row in store.rows("caliber_version"):
        if row.get("所属指标") == METRIC and row.get("版本号") == "v1.0":
            row["status"] = "已生效"
        if row.get("所属指标") == METRIC and row.get("版本号") == "v2.0":
            row["status"] = "待生效"

    template = _find_or_add("query_builder", "模板名称", TEMPLATE_NAME, {
        "id": store.next_id("query_builder"), "模板名称": TEMPLATE_NAME,
        "关联指标": METRIC, "维度组合": "省份,渠道", "过滤条件": "",
        "缓存策略": "", "status": "已发布", "pending": False, "abnormal": False,
    })
    template["status"] = "已发布"
    template["维度组合"] = "省份,渠道"
    template["过滤条件"] = ""

    api = _find_or_add("data_service", "接口名称", API_NAME, {
        "id": store.next_id("data_service"), "接口名称": API_NAME,
        "关联指标或模型": METRIC, "请求方式": "GET", "限流配额": "1000",
        "发布状态": "已发布", "status": "已发布", "pending": False, "abnormal": False,
    })
    api["status"] = "已发布"

    strategy = _find_or_add("cache_layer", "策略名称", STRATEGY_NAME, {
        "id": store.next_id("cache_layer"), "策略名称": STRATEGY_NAME,
        "关联查询模板": TEMPLATE_NAME, "缓存时长": "30秒",
        "失效触发条件": "到期失效", "命中率": "0",
        "status": "已生效", "pending": False, "abnormal": False,
    })
    strategy["缓存时长"] = "30秒"
    strategy["关联查询模板"] = TEMPLATE_NAME
    strategy["status"] = "已生效"

    store.cache_invalidate_by_prefix(f"query:{METRIC}")
    return SimpleNamespace(
        metric_id=metric["id"], v1_id=v1["id"], v2_id=v2["id"],
        template_id=template["id"], api_id=api["id"],
        strategy_id=strategy["id"], cache_key=CACHE_KEY,
    )


class TestCacheWarmFlow:
    """查询缓存层预热修复的验收场景"""

    def test_warm_writes_full_detail_with_strategy_ttl(self, province_setup):
        """从缓存策略列表发起预热：缓存完整明细并按三十秒时长失效，不写查询日志"""
        cache_svc = CacheLayerService()
        logs_before = len(store.query_logs())

        outcome = cache_svc.warm_by_strategy(province_setup.strategy_id)

        assert outcome.get("warmed") is True
        assert outcome.get("caliber") == "v1.0"
        assert outcome.get("cached_for_seconds") == 30
        # 后台预热不增加查询数
        assert len(store.query_logs()) == logs_before

        # 缓存里是完整查询明细：口径版本、统计粒度、维度列齐全
        cached = store.cache_get(province_setup.cache_key)
        assert isinstance(cached, dict)
        assert cached["caliber"] == "v1.0"
        assert cached["rows"], "预热应缓存明细行"
        for row in cached["rows"]:
            for column in ("口径版本", "计算口径", "统计粒度", "省份", "渠道"):
                assert column in row, f"预热明细缺列: {column}"
            assert row["口径版本"] == "v1.0"

        # 按策略配置的三十秒时长失效
        expires_at = store._cache[province_setup.cache_key][0]
        assert 0 < expires_at - time.time() <= 30

    def test_late_stale_warm_write_discarded(self, province_setup):
        """两轮预热并发：旧口径计算晚到被丢弃，数据服务取得新口径和省份明细"""
        builder = QueryBuilderService()
        caliber_svc = CaliberVersionService()
        cache_svc = CacheLayerService()
        data_svc = DataServiceService()

        # 暂停旧口径计算到写入前（只计算不写缓存）
        detail_v1 = builder.compute_template_detail(province_setup.template_id)
        assert detail_v1["caliber"] == "v1.0"

        # 启用新口径并完成新一轮预热
        entry, msg = caliber_svc.activate_version(province_setup.v2_id)
        assert entry is not None
        outcome = cache_svc.warm_by_strategy(province_setup.strategy_id)
        assert outcome.get("warmed") is True
        assert outcome.get("caliber") == "v2.0"

        # 放行旧计算：迟到结果不得覆盖新口径明细
        assert builder.store_computed_detail(province_setup.template_id, detail_v1) is False

        # 数据服务命中新口径明细，行内口径与响应标记一致
        result = data_svc.call_service(province_setup.api_id, operator="核对客户端")
        assert "error" not in result
        query_result = result["query_results"][0]
        assert query_result["source"] == "cache"
        assert query_result["caliber"] == "v2.0"
        assert query_result["rows"], "应返回明细行"
        for row in query_result["rows"]:
            assert "省份" in row
            assert row["口径版本"] == "v2.0"

    def test_placeholder_rows_not_served_as_hit(self, province_setup):
        """遗留占位行不能作为正式命中，应回源交付完整明细"""
        builder = QueryBuilderService()
        store.cache_set(province_setup.cache_key,
                        [{"序号": 1, "指标": METRIC, "预热": True}], 300)

        result = builder.execute_query(province_setup.template_id, operator="占位检查")

        assert result["source"] == "computed"
        assert result["caliber"] == "v1.0"
        assert result["rows"], "回源应交付完整明细"
        for row in result["rows"]:
            for column in ("口径版本", "计算口径", "统计粒度", "省份", "渠道"):
                assert column in row
            assert row["口径版本"] == result["caliber"]

    def test_hit_then_source_perf_insights(self, province_setup):
        """空日志下预热不计查询；命中一次、回源一次，性能洞察计两次查询一半命中"""
        cache_svc = CacheLayerService()
        data_svc = DataServiceService()
        builder = QueryBuilderService()
        journal = QueryJournalService()

        # 空日志核对：只预热不增查询数
        store.query_logs().clear()
        outcome = cache_svc.warm_by_strategy(province_setup.strategy_id)
        assert outcome.get("warmed") is True
        assert len(store.query_logs()) == 0

        # 三十秒内通过数据服务命中
        hit = data_svc.call_service(province_setup.api_id, operator="命中客户端")
        assert "error" not in hit
        assert hit["query_results"][0]["source"] == "cache"
        assert hit["query_results"][0]["caliber"] == "v1.0"

        # 超过策略时限：走查询构建器执行入口回源
        expires_at, value = store._cache[province_setup.cache_key]
        store._cache[province_setup.cache_key] = (time.time() - 1, value)
        miss = builder.execute_query(province_setup.template_id, operator="回源用户")
        assert miss["source"] == "computed"
        assert miss["cached_for_seconds"] == 30
        assert miss["rows"], "回源应交付完整明细"
        for row in miss["rows"]:
            assert row["口径版本"] == miss["caliber"]
            assert "省份" in row

        # 性能洞察：两次查询、一半命中率
        insights = journal.get_performance_insights()
        assert insights["total_queries"] == 2
        assert insights["cache_hit_ratio"] == 0.5
