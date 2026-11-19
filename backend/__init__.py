"""AI Panel Studio —— 后端应用包。

模块划分：

- ``config``     统一配置入口（环境变量 / .env），密钥只在此处读取
- ``database``   SQLAlchemy engine / SessionLocal / Base
- ``models``     四张表的 ORM 模型（sessions / experts / messages / consensus_items）
- ``schemas``    Pydantic 契约模型（REST 出入参 + SSE 五种事件）
- ``flows``      LLM 流程层（``flows.prompts`` 集中管理系统 Prompt）
- ``llm``        模型客户端（openai SDK 封装 + Mock 数据池 + 流式输出）
- ``services``   领域服务（阵容生成 / 举手调度 / 共识提炼 / 讨论引擎）
- ``api``        HTTP 接口层（SSE 事件流；其余 REST 端点按 PRD 补齐）
- ``main``       FastAPI 应用入口（``app = create_app()``）
- ``tests``      后端测试（pytest：用例 + 数据工厂 + 夹具）
"""

__all__ = [
    "api",
    "config",
    "database",
    "flows",
    "llm",
    "main",
    "models",
    "schemas",
    "services",
    "tests",
]

