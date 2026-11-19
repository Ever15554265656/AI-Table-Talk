"""用例 2 · 数据隔离（多讨论互不串台，PRD R9）。

流程：跑两场不同话题的讨论（甲 6 席、乙 4 席）→ 打开**乙**的只读回看 →
断言甲的内容没有出现在乙的页面上。

关于「甲的内容」在这个 Mock 下的可判定范围（重要）
------------------------------------------------
* **议题**：唯一字符串，可硬断言「甲议题不出现」。
* **专家名**：Mock 阵容由 `backend/topics.py::seat_plan` 生成 —— 职业按议题域选，
  姓名仍按 `DEFAULT_NAMES` 池顺序取（第 i 席取第 i 个名字）。
  所以六席的甲包含四席的乙**没有**的两位（第 5、6 席）→ 断言这两位不出现，
  同时断言乙的四位都出现。
* **发言 / 共识条目**：Mock 文案由「议题域模板 + 议题关键词 + 席位论据」拼成，
  两场话题不同则文本不同；但「文本不同」本身不是隔离证据，
  因此改用更硬的判据：**页面条目集合必须与乙场后端数据逐条一致**
  （数量、顺序、文本、配色），任何一条甲场记录混进来都会让数量或顺序对不上。
* 另外断言**甲场 session_id 从未出现在乙页面的 DOM 与任何网络请求里**，
  这是「数据入口按 session_id 隔离」最直接的证据。
"""

from __future__ import annotations

import re
from uuid import uuid4

import httpx
from playwright.sync_api import Page, expect

TOPIC_PLACEHOLDER = "例如：远程办公是否降低了团队的创新力？"
END_TIMEOUT_MS = 120_000


def _wait_for(page: Page, expression: str, *, timeout: int, message: str) -> None:
    try:
        page.wait_for_function(expression, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"{message}（超时 {timeout}ms）") from exc


def _run_discussion(page: Page, app_url: str, topic: str, experts: int) -> str:
    """走一遍「发起 → 阵容确认 → 演播厅 → 等到讨论结束」，返回 session_id。"""
    page.goto(app_url)
    page.get_by_placeholder(TOPIC_PLACEHOLDER).fill(topic)
    if experts != 4:  # 表单默认 4 人，其他人数用快捷按钮（3 / 4 / 6 人）
        page.get_by_role("button", name=f"{experts} 人").click()
    page.get_by_role("button", name="发起讨论").click()

    expect(page).to_have_url(re.compile(r"/lineup/"), timeout=20_000)
    session_id = page.url.rstrip("/").rsplit("/", 1)[-1]
    page.get_by_role("button", name="确认阵容并进入演播厅").click()
    expect(page).to_have_url(re.compile(rf"/studio/{session_id}"), timeout=20_000)

    _wait_for(
        page,
        "() => document.querySelector('.phase-bar__meta .el-tag')?.innerText.trim() === '已结束'",
        timeout=END_TIMEOUT_MS,
        message=f"讨论没有结束：{topic}",
    )
    return session_id


def _open_replay_from_home(page: Page, app_url: str, topic: str) -> None:
    """从首页「已结束」列表进入某场的只读回看（AC-1.5）。"""
    page.goto(app_url)
    expect(page.locator(".home__list-body")).to_be_visible()
    page.locator(".home__list .el-radio-button").filter(has_text="已结束").first.click()
    card = page.locator(".session").filter(has_text=topic).first
    expect(card).to_be_visible()
    card.click()
    expect(page).to_have_url(re.compile(r"/studio/[0-9a-zA-Z]+"), timeout=20_000)


def test_isolation(page: Page, app_url: str, api: httpx.Client) -> None:
    page.set_default_timeout(15_000)
    token = uuid4().hex[:8]
    topic_a = f"隔离用例甲 {token}：四天工作制能否提升创造力？"
    topic_b = f"隔离用例乙 {token}：城市是否应该全面禁售燃油车？"

    id_a = _run_discussion(page, app_url, topic_a, experts=6)
    id_b = _run_discussion(page, app_url, topic_b, experts=4)
    assert id_a != id_b

    session_a = api.get(f"/api/sessions/{id_a}").json()
    session_b = api.get(f"/api/sessions/{id_b}").json()
    assert session_a["status"] == session_b["status"] == "ended"

    # -------------------------------------------------- 打开乙的回看（记录网络请求）
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    _open_replay_from_home(page, app_url, topic_b)
    expect(page.locator(".studio__topic")).to_have_text(topic_b)
    expect(page.locator(".studio__controls .el-tag")).to_have_text("只读回看")

    body = page.locator("body").inner_text()

    # -------------------------------------------------- 1. 议题隔离
    assert topic_b in body
    assert topic_a not in body, "乙的回看页出现了甲的议题"
    assert id_a not in page.url and id_a not in body, "乙的回看页出现了甲的 session_id"

    # -------------------------------------------------- 2. 专家名隔离
    names_a = {item["name"] for item in session_a["experts"]}
    names_b = {item["name"] for item in session_b["experts"]}
    only_a = names_a - names_b
    assert only_a, "两场阵容完全相同，专家名断言会失去意义"
    for name in only_a:
        assert name not in body, f"乙的回看页出现了甲场独有专家：{name}"
    for name in names_b:
        assert name in body, f"乙的回看页缺少本场专家：{name}"

    # -------------------------------------------------- 3. 发言隔离（逐条对齐乙场数据）
    messages_b = api.get(f"/api/sessions/{id_b}/messages").json()
    assert all(message["session_id"] == id_b for message in messages_b)
    bubbles = page.locator(".bubble")
    expect(bubbles).to_have_count(len(messages_b))

    rendered = [text.strip() for text in page.locator(".bubble__text").all_inner_texts()]
    expected = [message["content"].strip() for message in messages_b]
    assert rendered == expected, "回看页的发言与乙场后端数据不一致（可能混入了其他场次）"

    for index, message in enumerate(messages_b):
        color = bubbles.nth(index).evaluate(
            "el => getComputedStyle(el).getPropertyValue('--speaker-color').trim()"
        )
        assert color.lower() == message["expert_color"].lower()
        expect(bubbles.nth(index).locator(".bubble__name")).to_have_text(message["expert_name"])
    # 回看是纯回放：不应有打字机中间态残留
    expect(page.locator(".bubble.is-streaming")).to_have_count(0)

    # -------------------------------------------------- 4. 共识 / 分歧隔离
    insights_b = api.get(f"/api/sessions/{id_b}/insights").json()
    expected_items = insights_b["agree"] + insights_b["disagree"]
    expect(page.locator(".insight__text")).to_have_count(len(expected_items))
    rendered_items = [text.strip() for text in page.locator(".insight__text").all_inner_texts()]
    assert rendered_items == [item["content"].strip() for item in expected_items]

    insights_a = api.get(f"/api/sessions/{id_a}/insights").json()
    assert len(insights_a["agree"]) + len(insights_a["disagree"]) == len(expected_items)

    # 归属人只认本场专家：共识条目里的 expert_ids 必须全部属于乙场
    expert_ids_b = {item["id"] for item in session_b["experts"]}
    for item in expected_items:
        assert set(item["expert_ids"]) <= expert_ids_b
    rendered_people = [
        name.strip() for name in page.locator(".insight__people .person").all_inner_texts()
    ]
    assert set(rendered_people) <= names_b, f"共识区出现了非本场专家：{rendered_people}"

    # -------------------------------------------------- 5. 请求隔离
    assert requests, "没有捕获到任何请求，监听可能失效"
    assert any(id_b in url for url in requests), "乙场数据未被请求，页面断言不可信"
    leaked = [url for url in requests if id_a in url]
    assert not leaked, f"乙页面请求了甲的接口：{leaked}"
