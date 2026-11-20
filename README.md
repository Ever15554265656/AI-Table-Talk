# AI 圆桌演播厅 · AI Panel Studio

多位 AI 专家围绕同一个议题实时辩论的演播厅：议题自动生成互补阵容，专家按均衡轮转依次发言，
屏幕上实时看到「谁在说、说了什么、共识与分歧如何变化」，结束后留下可回看的总结与只读现场。

> **零成本演示**：默认 `MOCK_MODE=true` 时全部内容由本地 Mock 生成器产出，
> 不发起任何模型请求、不消耗任何 API 额度，前端、后端、SSE、测试都可完整跑通。

## 核心特性

- **五大页面/区块**：① 首页（发起议题 + 进行中 / 已结束列表）② 阵容确认（主持人 + 专家席位）
  ③ 演播厅直播 ④ 只读回看（同一页面，`status=ended` 自动切换）⑤ 共识分歧与总结面板。
- **真实 / MOCK 双模式**：Mock 用于演示、开发与测试（零成本）；关闭 `MOCK_MODE` 并配置
  DeepSeek Key 后切换真实模式。
- **均衡轮转调度**：每一轮所有专家各发言一次（轮内顺序随机、相邻轮不同、同一人不连说），
  主持人不参与轮转，只在开场 / 收尾 / 阶段转折出现；全场每人发言次数差距 ≤ 1。
- **SSE 实时流**：六种事件 `phase / status / typing / message / consensus / done`，
  带 `seq` 作为 `Last-Event-ID` 水位，刷新或断线重连只补增量、不重复气泡。
- **「正在发言」实时提示**：`typing` 事件先亮出「XXX 正在发言…」占位卡与专家卡片高亮，
  正文落屏后替换为正式气泡；回看模式不显示。
- **专家状态统一**：主持人与专家同一张卡片、同一尺寸，状态四档
  「聆听中 / 正在发言 / 已发言 / 已总结」，并显示立场标签与已发言次数。
- **议题驱动的阵容与论证**：按议题关键词识别领域（技术 / 职场 / 教育 / 城市 / 青少年 / 通用），
  每个领域有互斥职业、互补立场与域级论证素材；发言按「陈述 → 交锋 → 深化 → 修正」分层，
  反驳只复述对方 ≤10 字的关键判断词并立即转折，不复读整句。
- **共识与分歧**：讨论中按节奏提炼并落库，收尾给出主持人总结（共识 / 分歧 / 结论建议）。
- **断点续播与只读回看**：进程重启或页面刷新都能从既有进度继续，已发出的发言不会再生成一遍；
  已结束场次不再触发生成，接口只读。

## 技术栈

| 层 | 选型 | 用途 |
|---|---|---|
| Web 框架 | FastAPI（Python 3.13） | REST + SSE 端点、依赖注入、OpenAPI |
| 数据层 | SQLAlchemy 2.x + SQLite | 会话 / 席位 / 发言 / 共识落库 |
| 契约 | Pydantic v2 | 出入参与 SSE 事件的唯一真源 |
| 前端 | Vue 3 + Vite + Element Plus | 三大页面、组件化状态展示（仓库实际实现为 Vue 3） |
| 实时通信 | SSE（`EventSource`） | 发言 / 状态 / 阶段 / 共识 / 结束事件推送 |
| 模型 | DeepSeek（OpenAI 兼容接口） | 真实模式下生成阵容、发言、共识与总结 |
| 测试 | pytest + pytest-asyncio + httpx | 后端单测与 E2E（ASGI 内联，不占端口） |
| 可选 E2E | Playwright（Python） | 浏览器级全流程（复用系统 Edge，非必装） |

## 本地运行三步

前置：Python 3.13、Node.js 18+。以下命令在**仓库根目录**执行。

### 1. 安装依赖

```bash
python -m venv venv
# Windows PowerShell
.\venv\Scripts\Activate.ps1
# macOS / Linux
# source venv/bin/activate

pip install -r requirements.txt

cd frontend
npm install
cd ..
```

### 2. 配置环境变量

```bash
# Windows PowerShell
Copy-Item .env.example .env
# macOS / Linux
# cp .env.example .env
```

`.env` 里保持 `MOCK_MODE=true`（默认值）即可**零成本运行**；不需要填任何真实 API Key。

### 3. 启动前后端

```bash
# 终端 A：建表（含演示数据）+ 启动后端（Mock 模式）
python scripts/init_db.py
uvicorn backend.main:app --reload --port 8000

# 终端 B：启动前端（/api 已代理到 127.0.0.1:8000）
cd frontend
npm run dev
```

打开 <http://127.0.0.1:5173> → 输入议题 → 「发起讨论」→ 阵容确认 → 演播厅直播。
后端不在 8000 端口时，用 `VITE_API_TARGET=http://127.0.0.1:<端口> npm run dev` 指定代理目标。

其他常用命令：

```bash
cd frontend && npm run build     # 生产构建（产物 frontend/dist）
cd frontend && npm run preview   # 预览构建产物
```

## 双模式说明

| 维度 | MOCK 模式（默认） | DeepSeek 真实模式 |
|---|---|---|
| 触发条件 | `MOCK_MODE=true`，或未配置 `DEEPSEEK_API_KEY` 时自动降级 | `MOCK_MODE=false` 且配置了有效 `DEEPSEEK_API_KEY` |
| 内容来源 | 本地生成器（阵容 / 发言 / 共识 / 总结全部按议题域现场合成） | 调用 `https://api.deepseek.com`（OpenAI 兼容接口）生成 |
| 成本 | **零 API 调用、零费用** | 按 token 计费，取决于 `LLM_MAX_TOKENS` 与场次规模 |
| 适用场景 | 本地开发、演示答辩、CI 与自动化测试 | 需要更自由的措辞与更深推理的正式演示 |
| 差别 | 措辞由规则引擎决定，结构稳定、可复现 | 内容由模型决定，更自然但存在波动与延迟 |
| 失败处理 | 不涉及远端，永不失败 | 单次调用最多重试 2 次（间隔 1 秒）；仍失败则本次内容改用 Mock 兜底，`done.degraded=true`，演播厅顶部提示「本次生成降级（Mock 兜底）」 |
| 超时 | — | 由 `LLM_TIMEOUT_SECONDS`（默认 45 秒）控制单次请求超时 |

真实模式最小配置（`.env`）：

```dotenv
MOCK_MODE=false
DEEPSEEK_API_KEY=<你的 Key>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

安全约定：Key 只在后端读取，永不下发浏览器、不写日志（日志里只出现脱敏结果）。

## 目录结构

```text
.
├─ backend/
│  ├─ main.py              # FastAPI 入口（REST + SSE）
│  ├─ config.py            # 环境变量 → 不可变 settings（含 MOCK 开关与讨论节奏）
│  ├─ models.py            # ORM：会话 / 席位 / 发言 / 共识
│  ├─ schemas.py           # Pydantic 契约（REST 出入参 + 六种 SSE 事件）
│  ├─ discussion.py        # 讨论主循环：开场 → 按轮推进 → typing → 落库 → 总结
│  ├─ stream.py            # SSE 广播中枢（backlog + seq 水位，支持重连不重复）
│  ├─ topics.py            # 议题域识别 / 席位方案 / 立场标签
│  ├─ arguments.py         # 议题参数化的论证链（分层 + 句式库 + 关键判断词）
│  ├─ llm.py               # LLM 客户端：Mock 生成器 + DeepSeek 调用
│  ├─ flows/               # 阵容 / 举手调度 / 均衡轮转 / 开场 / 发言 / 共识 / 总结 / 解析
│  ├─ routers/             # sessions / experts / messages / insights / stream
│  └─ tests/               # pytest 用例（含 E2E 全链路）
├─ frontend/
│  ├─ src/views/           # Home（首页）/ Lineup（阵容确认）/ Studio（直播 + 回看）
│  ├─ src/components/      # 发言气泡 / 专家卡片 / 状态区 / 阶段条 / 共识 / 总结 / typing 占位
│  ├─ src/composables/     # useDiscussionStream（SSE 归约 + 打字机 + 断点续播）
│  └─ src/api/client.js    # REST 客户端（唯一出口，字段不做改名）
├─ scripts/
│  ├─ init_db.py           # 建表 + 演示数据
│  ├─ clean_sessions.py    # 清理历史会话（--keep N / --yes）
│  └─ ui_smoke.mjs         # 无头 Edge 冒烟（可选）
├─ docs/                   # PRD / API 契约 / ER 图 / 需求清单
├─ e2e/                    # Playwright 浏览器级用例（可选）
└─ data/app.db             # SQLite 数据库（不入库）
```

## 测试

全部自动化测试都在 **MOCK 模式**下运行：不占端口、不出网、零模型调用。

```bash
# 后端单测 + E2E（当前 153 条，含 8 条全链路用例与 6 条降级/超时用例）
python -m pytest backend/tests -q

# 只跑全链路 E2E
python -m pytest backend/tests/test_e2e_flow.py -q
```

前端为**纯 JavaScript（Vue 3 + Vite + Element Plus）**：不含 TypeScript，也**暂无 ESLint 等 lint 门禁**，
质量门禁以 `npm run build`（Vite 构建通过）为准；如需接入 lint，建议后续单独加 ESLint flat config。

`backend/tests/test_e2e_flow.py` 用 `httpx.ASGITransport` 走真实 HTTP / SSE 路径，逐场景覆盖：

| 场景 | 断言要点 |
|---|---|
| 1 待开演 | `POST /api/sessions` 后状态 `created`、阵容已生成、职业与配色互斥、立场不一边倒、无任何发言 |
| 2 开演 | 首条发言来自主持人、`round=1`、开场明确引用议题原文 |
| 3 讨论中 | 发言总条数、主持人仅 2 条插话、每人次数差 ≤1、无同一人连说、每条发言含议题关键词 |
| 3' 反驳 | 指名道姓、只复述 ≤10 字判断词、与上一条发言无 12 字以上重合片段 |
| 3'' 实时流 | `typing` 与 `message` 一一对应、`seq` 连续、六种事件齐全、`done` 收尾 |
| 4 结束 | 状态 `ended`、总结含共识/分歧、共识分歧落库且可追溯到真实发言人、条数与库一致 |
| 5 回看 | 重复订阅只回放既有事件；LLM 调用记录为空（零生成）；写入口被拒绝；状态仍为 `ended` |
| 6 中断恢复 | 模拟重启（清空进程内任务表与广播中枢）后从既有进度续播：不重放开场、已发发言原样保留、只为新发言发 `typing` |

可选（需要本机浏览器，默认复用 Edge，非必需）：

```bash
python -m pytest e2e -q                      # Playwright 全流程
node scripts/ui_smoke.mjs http://127.0.0.1:5173/ 9333   # 需前后端已启动
```

## 数据清理

```bash
python scripts/clean_sessions.py            # 交互确认后删除全部历史会话
python scripts/clean_sessions.py --keep 3   # 只保留最近 3 场
python scripts/clean_sessions.py --yes      # 跳过确认（脚本/CI）
```

只删「会话 / 席位 / 发言 / 共识」数据，不改表结构。

<!-- 此处可替换截图：演播厅直播态（发言流 + 专家状态 + 共识分歧 + 阶段进度条） -->
<!-- ![演播厅截图](docs/screenshot-studio.png) -->

## 已知边界

- 阵容确认页目前只做「确认门」，尚未提供「重新生成阵容」按钮（后端未暴露该动作）。
- 暂停 / 继续只冻结前端打字机渲染，后端产出不会因此中断；讨论仍会走完并落库。
- 回看的总结**不单独持久化**：前端从最后一条主持人发言（三段用空行分隔）重建「共识 / 分歧 / 结论建议」，
  若后端将来提供 `/summary`，会优先使用库内结构化数据。
- 真实模式的单次调用含 2 次重试；重试后仍失败会降级 Mock 并在顶部提示，但**降级内容不写库标记**
  （仅 `done.degraded` 事件可见）。
