# 开发工作流（SDD → DDD → TDD → E2E）

本项目按四个开发范式分阶段推进：先把「契约与数据」定死，再让「页面与组件」跟着领域模型长出来，
然后用测试锁住行为，最后用端到端链路验证真实用户体验。本文件说明每一阶段的**输入 / 产出 / 门禁**，
以及日常改动时的执行顺序，供后续维护与评审对照。

---

## 1. 阶段总览

| 阶段 | 目标 | 主要产物 | 进入下一阶段的门禁 |
|---|---|---|---|
| **SDD**（Spec/契约驱动） | 把需求翻译成可验收的契约与数据模型 | `docs/PRD.md`、`docs/ER.md`、`docs/API.md`、`backend/models.py`、`backend/schemas.py` | 契约能自洽：REST 出入参、SSE 事件、数据库字段三处对得上 |
| **DDD**（领域/展示驱动） | 按领域拆页面与组件，让状态流可见 | `backend/topics.py`、`backend/arguments.py`、`frontend/src/views/*`、`frontend/src/components/*` | 页面只读后端字段，前端不自行取色、不渲染内部事件 |
| **TDD**（测试先行） | 用可断言的行为锁住核心逻辑 | `backend/tests/test_*.py`（路由 / 流程 / 调度 / 发言 / 共识） | 用例零网络、零等待，`pytest` 全绿 |
| **E2E**（系统级验证） | 校验真实 HTTP/SSE 链路的完整生命周期 | `backend/tests/test_e2e_flow.py`、`scripts/ui_smoke.mjs`、`e2e/`（可选） | 创建→开演→讨论→结束→回看→中断恢复 全部通过 |

> 阶段顺序不是形式：契约一旦变化，先改 SDD 产物，再让 DDD/TDD/E2E 跟上；反过来会留下「文档说一套、代码做一套」的漂移。

---

## 2. 日常改动的执行顺序

1. **先定位契约影响面**：是否需要改 `models.py` / `schemas.py` / `docs/API.md`？
   - 需要 → 先改契约与文档，再改实现（禁止先写代码后补文档）。
2. **再写/改测试**（TDD）：先在 `backend/tests/` 里写出会失败的断言，再实现到通过。
3. **最后补 E2E**：只有当改动跨进程/跨链路（SSE、重启恢复、回看）时才需要加 E2E 用例。
4. **门禁全绿再提交**：
   ```bash
   python -m pytest backend/tests -q   # 后端单测 + E2E（Mock 模式，零模型调用）
   cd frontend && npm run build        # 前端构建（当前无 lint/TS 门禁）
   ```
5. **提交信息**按类型前缀：`feat:` / `fix:` / `test:` / `docs:` / `chore:`，一条提交只做一件事。

---

## 3. 环境与数据

| 事项 | 做法 |
|---|---|
| 运行模式 | 默认 `MOCK_MODE=true`（零成本）；真实模式需关掉该开关并配置 `DEEPSEEK_API_KEY` |
| 建库与样例 | `python -m backend.seeds`（`--reset` 重建）；`scripts/init_db.py` 为兼容入口 |
| 清理历史 | `python scripts/clean_sessions.py --keep N` / `--yes` |
| 测试库 | 测试使用独立临时 SQLite（`conftest.py`），不触碰 `data/app.db` |
| 真实模式失败 | 单次调用重试 2 次（间隔 1s），仍失败降级 Mock；`done.degraded=true` 并在演播厅顶部提示 |

---

## 4. 各阶段的检查清单

### SDD
- [ ] 新增字段/事件同时更新 `docs/API.md` 与 `docs/ER.md`；
- [ ] 未实现的端点必须在文档里标注「规划中」；
- [ ] SSE 事件信封保持不变（`session_id` / `seq` / `ts` / `data`）。

### DDD
- [ ] 页面文案统一走 `frontend/src/constants.js`；
- [ ] 颜色只读 `expert.color` / `message.expert_color`；
- [ ] Transcript 只渲染「姓名 + 职业/Title + 正文」，不出现举手/调度等内部词；
- [ ] 主持人卡片与专家卡片同结构、同尺寸。

### TDD
- [ ] 用例不依赖真实时钟（`SPEAK/THINK/TYPING` 在测试环境为 0）；
- [ ] 随机调度注入固定种子；
- [ ] 断言落在「观众能看到的事实」：条数、归属、顺序、关键词。

### E2E
- [ ] 走真实 HTTP/SSE 路径（`httpx.ASGITransport`），不直接调内部函数；
- [ ] 用「调用记录为空」证明回看/重连不触发生成；
- [ ] 中断恢复用例通过清空进程内任务表与广播中枢模拟重启。

---

## 5. 交付物索引

| 类别 | 文件 |
|---|---|
| 产品需求 | [`PRD.md`](./PRD.md)（定位 / 用户画像 / 功能需求与验收标准） |
| 数据模型 | [`ER.md`](./ER.md)（mermaid ER 图 + 表结构约束） |
| 接口契约 | [`API.md`](./API.md)（REST + 六种 SSE 事件；未实现端点标注「规划中」） |
| Prompt 记录 | [`PROMPTS.md`](./PROMPTS.md)（四范式 7 段，含背景/挑战/修正） |
| 运行与测试 | 根目录 [`README.md`](../README.md) |
| 工作流 | 本文件 |
