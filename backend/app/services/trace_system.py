"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class TraceSystemService(BaseModuleService):
    MODULE = "trace_system"
    ENTITY = "追溯链条"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["追溯ID", "起点指标", "中间步骤", "最终数据源", "追溯状态"]
    REQUIRED_FIELDS = ["追溯ID", "起点指标", "中间步骤"]
    STATUS_ORDER = ["待追溯", "追溯中", "已完成", "追溯失败"]
    ACTION_RULES = {"发起追溯": "追溯中", "中断追溯": "追溯失败"}
    NEGATIVE_ACTIONS = []
