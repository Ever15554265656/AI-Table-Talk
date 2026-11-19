"""用例 1 · 完整流程。

发起讨论 → 阵容页确认 → 进演播厅 → 主持人开场 → ≥5 条发言 → 共识区有内容 → 讨论结束。

断言原则：**后端是事实来源，页面是渲染结果**。关键数字（席位数、发言数、共识条数、
主题、颜色）都先用 REST 取一份基线，再逐项对照 DOM，避免「自己证明自己」。
"""

from __future__ import annotations

import re
from uuid import uuid4

import httpx
from playwright.sync_api import Page, expect

#: Home.vue 里的表单占位文案（E2E 的稳定入口，不用改产品代码加 testid）
TOPIC_PLACEHOLDER = "例如：远程办公是否降低了团队的创新力？"

#: 派生的 Mock 讨论在 6 轮后收尾，给足打字机时间
END_TIMEOUT_MS = 120_000


def _wait_for(page: Page, expression: str, *, timeout: int, message: str) -> None:
    """等待一段浏览器内布尔表达式成立（用于「数量会增长」的断言）。"""
    try:
        page.wait_for_function(expression, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 统一补上中文语境信息
        raise AssertionError(f"{message}（超时 {timeout}ms）") from exc


def test_full_flow(page: Page, app_url: str, api: httpx.Client) -> None:
    page.set_default_timeout(15_000)
    token = uuid4().hex[:8]
    topic = f"E2E 全流程 {token}：远程办公是否降低了团队的创新力？"

    # ---------------------------------------------------------------- 1. 首页
    page.goto(app_url)
    expect(page.get_by_placeholder(TOPIC_PLACEHOLDER)).to_be_visible()
    expect(page.locator(".home__list-body")).to_be_visible()

    page.get_by_placeholder(TOPIC_PLACEHOLDER).fill(topic)
    page.get_by_role("button", name="发起讨论").click()

    # ------------------------------------------------- 2. 阵容确认页（PRD R2）
    expect(page).to_have_url(re.compile(r"/lineup/"), timeout=20_000)
    expect(page.locator(".lineup__heading")).to_have_text("阵容确认")
    expect(page.locator(".lineup__topic")).to_have_text(topic)

    session_id = page.url.rstrip("/").rsplit("/", 1)[-1]
    detail = api.get(f"/api/sessions/{session_id}").json()
    assert detail["status"] == "created", "确认前不应自动开演"
    host = next(item for item in detail["experts"] if item["is_host"])
    guests = [item for item in detail["experts"] if not item["is_host"]]
    assert len(guests) >= 2

    expect(page.locator(".lineup__host .seat__name")).to_have_text(host["name"])
    expect(page.locator(".lineup__guests .seat")).to_have_count(len(guests))

    # 每张席位卡：姓名 / 职业·Title / 立场 / 配色都来自后端（AC-2.5、AC-2.7）
    host_card = page.locator(".lineup__host .seat")
    assert host["color"].lower() == host_card.evaluate(
        "el => getComputedStyle(el).getPropertyValue('--seat-color').trim()"
    ).lower()
    for index, guest in enumerate(guests):
        card = page.locator(".lineup__guests .seat").nth(index)
        expect(card.locator(".seat__name")).to_have_text(guest["name"])
        expect(card.locator(".seat__meta")).to_have_text(
            f"{guest['profession']} · {guest['title']}"
        )
        expect(card.locator(".seat__stance")).to_have_text(guest["stance"])
        color = card.evaluate("el => getComputedStyle(el).getPropertyValue('--seat-color').trim()")
        assert color.lower() == guest["color"].lower(), f"{guest['name']} 配色与后端不一致"

    page.get_by_role("button", name="确认阵容并进入演播厅").click()
    expect(page).to_have_url(re.compile(rf"/studio/{session_id}"), timeout=20_000)

    # ------------------------------------------------- 3. 主持人开场（AC-2.9）
    expect(page.locator(".studio__topic")).to_have_text(topic)
    first = page.locator(".bubble").first
    expect(first).to_be_visible(timeout=30_000)
    expect(first.locator(".bubble__name")).to_have_text(host["name"])
    expect(first.locator(".bubble__host")).to_be_visible()
    first_text = first.locator(".bubble__text").inner_text().strip()
    assert len(first_text) >= 10, f"开场白过短：{first_text!r}"

    # ------------------------------------------------- 4. 至少 5 条发言
    _wait_for(
        page,
        "() => document.querySelectorAll('.bubble').length >= 5",
        timeout=60_000,
        message="等待第 5 条发言出现",
    )

    # ------------------------------------------------- 5. 共识区出现内容（AC-5.1）
    _wait_for(
        page,
        "() => document.querySelectorAll('.insight__text').length > 0",
        timeout=60_000,
        message="共识 / 分歧区始终为空",
    )
    expect(page.locator(".consensus__label.is-agree")).to_have_text("共识")
    expect(page.locator(".consensus__label.is-disagree")).to_have_text("分歧")

    # ------------------------------------------------- 6. 讨论最终结束（AC-7.5）
    _wait_for(
        page,
        "() => document.querySelector('.phase-bar__meta .el-tag')?.innerText.trim() === '已结束'",
        timeout=END_TIMEOUT_MS,
        message="讨论没有进入「已结束」",
    )

    final = api.get(f"/api/sessions/{session_id}").json()
    assert final["status"] == "ended"

    messages = api.get(f"/api/sessions/{session_id}/messages").json()
    insights = api.get(f"/api/sessions/{session_id}/insights").json()
    assert len(messages) >= 5
    assert insights["agree"] or insights["disagree"], "后端没有任何共识 / 分歧"

    # 页面条目数与后端完全一致（没有丢条、也没有凭空生成）
    expect(page.locator(".bubble")).to_have_count(len(messages))
    expect(page.locator(".insight__text")).to_have_count(
        len(insights["agree"]) + len(insights["disagree"])
    )
    # 最后一条是主持人总结（AC-7.5）
    expect(page.locator(".bubble").last.locator(".bubble__host")).to_be_visible()

    # 总结卡片：三段（共识 / 分歧 / 结论建议）
    expect(page.locator(".summary")).to_be_visible()
    headings = [
        text.strip() for text in page.locator(".summary__heading").all_inner_texts()
    ]
    assert headings == ["共识", "分歧", "结论建议"], headings

    # 内部事件词不上屏（AC-6.2）
    body_text = page.locator("body").inner_text()
    for word in ("举手", "抢答", "调度", "intent"):
        assert word not in body_text, f"页面泄漏内部事件词：{word}"
