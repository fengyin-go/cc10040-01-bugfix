"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class DataSubscriptionService(BaseModuleService):
    MODULE = "data_subscription"
    ENTITY = "订阅任务"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["订阅名称", "关联指标", "推送条件", "推送渠道", "生效状态"]
    REQUIRED_FIELDS = ["订阅名称", "关联指标", "推送条件"]
    STATUS_ORDER = ["待生效", "已生效", "已暂停"]
    ACTION_RULES = {"启用订阅": "已生效", "暂停订阅": "已暂停"}
    NEGATIVE_ACTIONS = []
