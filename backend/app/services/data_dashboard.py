"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class DataDashboardService(BaseModuleService):
    MODULE = "data_dashboard"
    ENTITY = "看板配置"
    OPERATION_TERM = "归档"
    LIST_FIELDS = ["看板名称", "包含图表", "刷新间隔", "数据范围", "发布状态"]
    REQUIRED_FIELDS = ["看板名称", "包含图表", "刷新间隔"]
    STATUS_ORDER = ["草稿", "已发布", "已归档"]
    ACTION_RULES = {"发布看板": "已发布", "归档看板": "已归档"}
    NEGATIVE_ACTIONS = []
