# AI Panel Studio · 前端（Vue 3 + Vite + Element Plus）

当前阶段：**已接入后端（FastAPI + SSE）**。首页列表 / 发起讨论 / 演播厅 / 回看全部走真实接口：

| 页面能力 | 接口 |
|---|---|
| 首页列表 | `GET /api/sessions` |
| 发起讨论（服务端生成阵容） | `POST /api/sessions` |
| 演播厅初始化（议题 + 席位） | `GET /api/sessions/{id}` |
| 刷新续播 / 只读回看 | `GET /api/sessions/{id}/messages` + `GET /api/sessions/{id}/insights` |
| 实时讨论 | `GET /api/stream/{id}`（SSE：`message` / `status` / `consensus` / `phase` / `done`） |

`src/mock/data.js` 与 `src/composables/useMockPlayback.js` 是上一阶段（纯假数据）的产物，
页面已不再引用，保留仅作离线对照；真正的数据通路是 `src/api/client.js` +
`src/composables/useDiscussionStream.js`。

## 运行

后端（Mock 模式，无需真实 Key）：

```bash
# 仓库根目录
set MOCK_MODE=true                 # PowerShell: $env:MOCK_MODE='true'
python scripts/init_db.py          # 建表 + 演示数据
uvicorn backend.main:app --port 8000

# 也可以直接进 backend 目录启动（两种方式都支持，见 backend/main.py 顶部说明）
cd backend
uvicorn main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173 （/api 已代理到 127.0.0.1:8000）
npm run build    # 产物在 frontend/dist
npm run preview  # 预览构建产物 http://127.0.0.1:4173
```

后端不在默认端口时，用 `VITE_API_TARGET` 指定代理目标：

```bash
VITE_API_TARGET=http://127.0.0.1:8765 npm run dev
```

## 目录

```
src/
├─ main.js               # 入口：Element Plus 全量 + zh-cn 语言包
├─ router/index.js       # / 首页；/lineup/:id 阵容确认；/studio/:id 演播厅（直播 / 回看共用）
├─ constants.js          # 阶段 / 三态 / 状态文案 / 调色板 / 表单约束（唯一文案字典）
├─ api/client.js         # REST 客户端：listSessions / createSession / getSession / listMessages / listInsights / streamUrl
├─ composables/useDiscussionStream.js  # SSE 归约 + 打字机 + round 去重（直播 / 回看共用）
├─ mock/data.js          # 【历史】纯假数据（页面已不引用，仅离线对照）
├─ composables/useMockPlayback.js      # 【历史】假数据播放引擎
├─ components/
│  ├─ ExpertCard.vue     # 单个专家小窗（色块 + 姓名 + 职业/Title + 三态 + 关注点）
│  ├─ StatusWindow.vue   # 专家状态区（主持台置顶；窄屏折叠为横向滚动条）
│  ├─ SpeakerBubble.vue  # 发言气泡（专属色竖条 + 姓名 + 职业/Title + 打字机光标）
│  ├─ ConsensusPanel.vue # 共识 / 分歧两栏（各自独立滚动，新增条目高亮 3s）
│  ├─ SummaryPanel.vue   # 主持人总结（done 事件里的 SummaryOut.sections）
│  └─ PhaseBar.vue       # 阶段指示条：开场 / 自由讨论 / 收尾 / 已结束 + 轮次进度
├─ views/Home.vue        # 进行中 / 已结束列表 + 发起讨论表单 + 回看入口
├─ views/Lineup.vue      # 阵容确认页：主持台置顶 + 专家席（立场 / 配色均来自后端）
├─ views/Studio.vue      # 演播厅：三块区域独立滚动 + 三档响应式 + 控制条
└─ styles/base.css       # 深色基调、面板、细滚动条；body 永不滚动
```

## 关键约定

| 主题 | 做法 |
|---|---|
| 数据来源 | 一律来自后端；组件不做任何字段改名（`docs/API.md` 是唯一契约） |
| 颜色 | 一律读 `expert.color` / `message.expert_color`，前端不取色（PRD AC-2.5） |
| 事件归约 | `message→items`、`status→panelists`、`consensus→agree/disagree`、`phase→phase/round/maxRounds`、`done→finished/summary` |
| 幂等合并 | 发言按 `round` 去重：先拉 `messages` 补历史，再接 SSE 增量，刷新不会重复气泡 |
| 共识两栏 | 后端每次只推一栏（`type=agree` 或 `disagree`），另一栏保留上一版，禁止整组清空 |
| 只读回看 | `status=ended` 的场次不订阅 SSE（避免重跑讨论），直接读 `messages` + `insights` |
| 断线 | `EventSource` 自动重连并带 `Last-Event-ID`；失败时控制条出现「重新连接」 |
| 内部事件 | Transcript 只渲染「姓名 + 职业/Title + 正文」，不出现举手/抢答/调度/状态词（AC-6.2） |
| 三态 | 只有 `待机 / 准备发言 / 发言中`，同一时刻至多 1 人在发言 |
| 滚动 | `html/body/#app` 高度锁死 + `overflow:hidden`，滚动只发生在区域容器内（AC-1.9 / AC-6.5） |
| 响应式 | ≥1600px 三列；1024–1599 两列（右列上下分栏）；<1024px 单列（状态折叠横条） |
| 阶段 | `opening / debate / wrap_up` 来自 SSE `phase`，`ended` 为终态展示位 |

## 未完成（下一步）

- 阵容确认页的「重新生成」（依赖后端 `regenerate`；当前确认页只做前端确认门，不改后端状态）。
- 暂停 / 继续 / 结束讨论（依赖后端 `pause` / `resume` / `end`）；当前「暂停」只冻结前端打字机。
- 回看页的总结卡片（依赖后端持久化总结 + `GET /summary`）。
- 共识条目点击定位 Transcript（AC-5.8）。

## 端到端测试（Playwright）

`e2e/` 下是 Python 版 Playwright 用例，`conftest.py` 自动拉起 **Mock 后端（uvicorn）+ 前端（vite dev）**，
结束后按进程树回收；浏览器默认复用系统 Edge（`channel=msedge`），**无需** `playwright install`。

```bash
# 仓库根目录；Mock 模式 + 独立临时库，零模型调用
.\venv\Scripts\python.exe -m pytest e2e -q
# 失败时补截图：--screenshot=only-on-failure
# 服务日志：e2e/.artifacts/{uvicorn,vite}.log
```

- `test_full_flow.py`：发起讨论 → 阵容页确认 → 演播厅 → 主持人开场 → ≥5 条发言 → 共识有内容 → 讨论结束。
- `test_isolation.py`：两场不同话题跑完 → 打开第二场回看 → 断言第一场的议题 / 独有专家 / session_id / 发言与共识条目均未混入。

## 端到端冒烟（可选）

`scripts/ui_smoke.mjs` 用无头 Edge + CDP 跑一遍「首页 → 发起讨论 → 直播渲染 → 刷新回看」，
并断言发言条数、阶段、共识两栏、总结三段、只读标记等 18 项，同时输出截图：

```bash
# 后端 Mock 模式 + 前端 dev 均已启动
node scripts/ui_smoke.mjs http://127.0.0.1:5173/ 9333
# 全绿时最后一行打印 [result] PASS（退出码 0）
```

## 说明：与 PRD 技术栈基线的偏差

`docs/PRD.md` 技术栈基线写的是 React + Tailwind；本次按当前任务指令改用
**Vue 3 + Vite + Element Plus**。该偏差应作为一条 ADR 补记到 `docs/DECISIONS.md`。

