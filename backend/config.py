"""应用配置。

设计原则：
1. **所有配置只从环境变量 / ``.env`` 读取**，代码中不出现任何密钥字面量；
2. 对外只暴露 ``settings`` 这一份不可变对象，便于测试时 ``get_settings.cache_clear()``；
3. 密钥永不进入日志：需要打印配置时统一用 ``Settings.as_log_dict``（自动脱敏）。

环境变量清单见 ``.env.example``。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 路径与常量

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_FILE: Path = PROJECT_ROOT / ".env"
DATA_DIR: Path = PROJECT_ROOT / "data"
DEFAULT_DB_FILE: Path = DATA_DIR / "app.db"

#: 加载 .env（不覆盖已存在的真实环境变量，便于 CI / 生产注入）
load_dotenv(ENV_FILE, override=False)

#: 专家人数取值范围（不含主持人）
EXPERT_COUNT_MIN: int = 2
EXPERT_COUNT_MAX: int = 8
DEFAULT_EXPERT_COUNT: int = 4

#: 话题长度范围（裁剪空白后）
TOPIC_MIN_LEN: int = 1
TOPIC_MAX_LEN: int = 200

#: 轮次上限取值范围
ROUNDS_MIN: int = 6
ROUNDS_MAX: int = 40
DEFAULT_ROUNDS: int = 20

#: 席位配色板：9 色，均满足深色底对比度 ≥4.5:1。
#: 由**后端**分配给嘉宾并落库，前端只读取，禁止自行取色（见 PRD AC-2.5）。
PANEL_COLOR_PALETTE: tuple[str, ...] = (
    "#F87171",  # 珊瑚红
    "#FBBF24",  # 琥珀
    "#34D399",  # 翡翠绿
    "#60A5FA",  # 天蓝
    "#A78BFA",  # 薰衣草紫
    "#F472B6",  # 玫粉
    "#22D3EE",  # 青
    "#A3E635",  # 黄绿
    "#FB923C",  # 橙
)

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


# ---------------------------------------------------------------- 解析工具


def _env_str(key: str, default: str = "") -> str:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip()


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    logger.warning("环境变量 %s=%r 不是合法布尔值，回退为 %s", key, raw, default)
    return default


def _env_int(
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，回退为 %s", key, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("环境变量 %s=%s 小于下限 %s，已裁剪", key, value, minimum)
        value = minimum
    if maximum is not None and value > maximum:
        logger.warning("环境变量 %s=%s 超过上限 %s，已裁剪", key, value, maximum)
        value = maximum
    return value


def _env_float(
    key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法数字，回退为 %s", key, raw, default)
        return default
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def _env_csv(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    items = tuple(part.strip() for part in raw.split(",") if part.strip())
    return items or default


def _default_database_url() -> str:
    """SQLite 绝对路径 URL；Windows 盘符同样适用 ``sqlite:///D:/.../app.db``。"""
    return f"sqlite:///{DEFAULT_DB_FILE.as_posix()}"


# ---------------------------------------------------------------- 配置对象


@dataclass(frozen=True, slots=True)
class Settings:
    """全局不可变配置。字段与 ``.env.example`` 一一对应。"""

    # --- 应用 ---
    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    # --- 数据库 ---
    database_url: str = field(default_factory=_default_database_url)
    sql_echo: bool = False

    # --- 大模型（密钥仅后端可见）---
    deepseek_api_key: str = ""
    deepseek_base_url: str = DEFAULT_BASE_URL
    model_name: str = DEFAULT_MODEL
    temperature: float = 0.85
    max_tokens: int = 512
    llm_timeout_seconds: float = 45.0

    # --- 运行模式 ---
    mock_mode: bool = False
    mock_latency_ms: int = 180

    # --- 讨论节奏 ---
    max_rounds: int = DEFAULT_ROUNDS
    insight_every_n_messages: int = 3
    speak_interval_seconds: float = 1.4
    max_consensus_items: int = 10

    # ------------------------------------------------------------ 派生属性

    @property
    def has_api_key(self) -> bool:
        """后端是否拿到了可用密钥（仅后端可见，绝不下发前端）。"""
        return bool(self.deepseek_api_key)

    @property
    def effective_mock_mode(self) -> bool:
        """实际是否走 Mock：显式开启，或缺少密钥时自动降级。

        保证「没有 Key 也能本地启动、演示、跑测试」（PRD 约束 C-06）。
        """
        return self.mock_mode or not self.has_api_key

    @property
    def max_seats(self) -> int:
        """单场最多座位数（主持人 + 专家）。"""
        return EXPERT_COUNT_MAX + 1

    @property
    def is_testing(self) -> bool:
        return self.app_env.lower() in {"test", "testing", "ci"}

    # ------------------------------------------------------------ 安全输出

    def masked_api_key(self) -> str:
        """返回脱敏后的密钥，仅用于日志。"""
        key = self.deepseek_api_key
        if not key:
            return ""
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}****{key[-4:]}"

    def as_log_dict(self) -> dict[str, object]:
        """用于启动日志的脱敏配置快照。"""
        return {
            "app_env": self.app_env,
            "host": self.host,
            "port": self.port,
            "database_url": self.database_url,
            "model_name": self.model_name,
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_api_key": self.masked_api_key() or "<未配置>",
            "mock_mode": self.mock_mode,
            "effective_mock_mode": self.effective_mock_mode,
            "max_rounds": self.max_rounds,
            "insight_every_n_messages": self.insight_every_n_messages,
            "speak_interval_seconds": self.speak_interval_seconds,
        }


# ---------------------------------------------------------------- 配置装配


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """构建（并缓存）配置对象。测试可用 ``get_settings.cache_clear()`` 重置缓存。"""
    return Settings(
        app_env=_env_str("APP_ENV", "development"),
        host=_env_str("HOST", "127.0.0.1") or "127.0.0.1",
        port=_env_int("PORT", 8000, minimum=1, maximum=65535),
        cors_origins=_env_csv(
            "CORS_ORIGINS", ("http://localhost:5173", "http://127.0.0.1:5173")
        ),
        database_url=_env_str("DATABASE_URL") or _default_database_url(),
        sql_echo=_env_bool("SQL_ECHO", False),
        deepseek_api_key=_env_str("DEEPSEEK_API_KEY"),
        deepseek_base_url=_env_str("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
        model_name=_env_str("DEEPSEEK_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
        temperature=_env_float("LLM_TEMPERATURE", 0.85, minimum=0.0, maximum=2.0),
        max_tokens=_env_int("LLM_MAX_TOKENS", 512, minimum=64, maximum=8192),
        llm_timeout_seconds=_env_float(
            "LLM_TIMEOUT_SECONDS", 45.0, minimum=3.0, maximum=180.0
        ),
        mock_mode=_env_bool("MOCK_MODE", False),
        mock_latency_ms=_env_int("MOCK_LATENCY_MS", 180, minimum=0, maximum=5000),
        max_rounds=_env_int(
            "MAX_ROUNDS", DEFAULT_ROUNDS, minimum=ROUNDS_MIN, maximum=ROUNDS_MAX
        ),
        insight_every_n_messages=_env_int(
            "INSIGHT_EVERY_N_MESSAGES", 3, minimum=1, maximum=10
        ),
        speak_interval_seconds=_env_float(
            "SPEAK_INTERVAL_SECONDS", 1.4, minimum=0.0, maximum=15.0
        ),
        max_consensus_items=_env_int("MAX_CONSENSUS_ITEMS", 10, minimum=2, maximum=40),
    )


#: 进程级配置单例（模块导入即完成 .env 读取）
settings: Settings = get_settings()

__all__ = [
    "DEFAULT_EXPERT_COUNT",
    "DEFAULT_ROUNDS",
    "EXPERT_COUNT_MAX",
    "EXPERT_COUNT_MIN",
    "PANEL_COLOR_PALETTE",
    "PROJECT_ROOT",
    "ROUNDS_MAX",
    "ROUNDS_MIN",
    "Settings",
    "TOPIC_MAX_LEN",
    "TOPIC_MIN_LEN",
    "get_settings",
    "settings",
]
