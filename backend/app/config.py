"""运行配置：端口、跨域、运行环境。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    app_name: str = "多维分析查询与指标平台"
    env: str = "local"
    port: int = 8015
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5195",
            "http://localhost:5195",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200


settings = Settings()
