# 开发过程说明：我如何用 AI 做这个项目

## 一、我是怎么用 AI 开发这个项目的

我用 Cline + DeepSeek，全程 `MOCK_MODE=true`，按四个阶段推进，每阶段只给 AI 一个能验收的目标。

**SDD**（`5a1f013`、`beedbe1`）：先表结构与 SSE 信封，再出入参，最后写实现；字段必须同时出现在 `API.md` 与 `ER.md`，验收 `test_routers.py`/`test_stream.py` 加人工对文档。AI 曾把 SSE 写成两套字段，我打回重写。

**DDD**（`4cc8b36`、`84c2b38`、`89b82fd`）：颜色只读后端、Transcript 只渲染姓名+职业+正文、主持人/专家同尺寸；验收 `npm run build` 加我自己点页面。AI 把“举手”渲染进气泡，我指着 `SpeakerBubble.vue` 要求删掉。

**TDD**（`be49125`）：测试零网络零等待（间隔 0、固定种子），验收 `pytest` 全绿；失败就贴断言要根因，不许删测试。

**E2E**（`af11dc0`）：`httpx` 走真实 HTTP/SSE，回看必须证明零生成，6 个场景 30 秒内。后续修补（`a5e1c4e`、`7179944`、`63c12a2`、`4e82baa`、`490cd9d`）同节奏。

## 二、三个典型问题

### 1. 专家发言扎堆、不贴议题

**现象**：同一人连说三四轮、有人全程沉默；换题还念“手机/67%”旧素材。

**定位**：`turn_taking.py` 每轮随机点人；`arguments.py` 把案例写死在域表，换题就串。

**引导修正**：新增 `scheduling.py` 的 `plan_round`（每轮全员一次、相邻轮不同、首位≠上轮末位）；素材改“域机制+场景锚点”，每句带 `{keyword}`，断言写进 `test_arguments.py`。

**验证**：`test_scheduling.py::test_speech_counts_are_balanced`、`test_discussion.py::test_speakers_rotate_evenly_by_round`；实测 4 席 12 条 `[3,3,3,3]`，差 0。

### 2. LLM 超时配置形同虚设

**现象**：`LLM_TIMEOUT_SECONDS` 只在 `config.py` 被读取，没有任何调用点用它。

**定位**：`llm.py` 的 `_ensure_sync_client`/`_ensure_async_client` 构造 `OpenAI`/`AsyncOpenAI` 时只传 `api_key` 与 `base_url`。

**引导修正**：补 `timeout=self._config.llm_timeout_seconds`；加 3 次尝试（间隔 1 秒）重试，失败降级 Mock、`done.degraded=true`、前端顶部提示，阵容生成同样降级不再 502（`7179944`）。

**验证**：`test_llm_fallback.py::test_openai_clients_receive_configured_timeout`（`timeout==12.5`）、`test_sync_stream_retries_then_falls_back_to_mock`。

### 3. AI 把作业需求原文当成交付物提交

**现象**：`5a1f013` 里 AI 还提交了 `docs/REQUIREMENTS.md`（作业原文），PRD 有 9 处引用它（其中 7 条是 `REQUIREMENTS.md Lxx` 行号），标题写成“题目对照表/考察点”。

**定位**：需求是输入不是交付物；行号引用会让 PRD 随原文漂移。grep `REQUIREMENTS`、`wisquest` 列出全部命中。

**引导修正**：立规矩“AI 只改实现与项目文档，永不改需求与验收标准”；删文件、删 PRD 的 7 条来源行与 `Lxx` 引用，把“题目/考察点”改成“需求总览/设计要点”，补 §6–§11 索引（`4e82baa`）。

**验证**：grep `REQUIREMENTS` 只剩 `README` 的 `requirements.txt`，`wisquest` 0；`docs/` 只剩 5 个文档。

## 三、我对“工程化 AI 开发”的理解

我不信“一句提示词生成整个项目”：拆成阶段，每步才可验收。测试是最硬的约束——`timeout==12.5` 就是 12.5，`done.degraded is True` 就是 True，`test_isolation_backend.py` 两场并发串一场就红。

AI 还会犯人不常犯的错：配置写了没接上、提交信息留占位符、把需求当交付物、注释写成半句（`ER.md` 的 `created | live | ended` 让 GitHub Mermaid 失败）。这靠我查引用、查仓库、把图真的渲染一遍。

分工：AI 写实现与测试，我定验收、查边界、做复核；AI 提速的是产出，不是判断。

---

## 附录：各阶段检查清单（压缩）

- **SDD**：字段/事件同步 `API.md` 与 `ER.md`；未实现端点标“规划中”；SSE 信封不变。
- **DDD**：文案走 `constants.js`；颜色只读后端；Transcript 不含内部词；卡片同结构。
- **TDD**：不依赖真实时钟；固定种子；断言落在条数/归属/顺序/关键词。
- **E2E**：`httpx` 走真实链路；用调用记录为空证明回看不生成；重启用清空任务表模拟。

```bash
python -m pytest backend/tests -q     # 157 条，Mock 模式
cd frontend && npm run build
```
