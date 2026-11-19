"""流程 2/6 补充：均衡轮转调度（每一轮全员各发言一次）。

为什么不再「每轮随机点一个人」
------------------------------
随机点人看起来热闹，但会出现两种观众一眼就能看穿的毛病：同一人连说好几条、
有人整场没开口。这里改成**均衡轮转 + 扰动**：

1. **每轮全员各一次**：轮内顺序随机，因此每人的发言次数全程差距不超过 1；
2. **相邻轮次顺序不同**：两轮顺序完全一样就重新洗牌，避免看得出循环；
3. **同一人不连说两条**：本轮第一位不得等于上一轮最后一位；
4. **主持人不在轮转里**：只负责开场 / 收尾 / 阶段转折。

本模块是纯函数：只要给出席位名单和轮次序号就能算出顺序，方便单测与复现。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Protocol, Sequence

#: 重新洗牌的最大尝试次数（两人阵容时用于跳出死循环）
MAX_RESHUFFLE: int = 8


class _Random(Protocol):
    """只用到 ``shuffle``（便于测试注入固定种子）。"""

    def shuffle(self, x: list) -> None: ...


@dataclass(frozen=True)
class RoundPlan:
    """一轮的发言顺序。"""

    index: int
    order: tuple[str, ...]


def _rotate(order: list[str], shift: int = 1) -> list[str]:
    if not order:
        return order
    shift %= len(order)
    return order[shift:] + order[:shift]


def plan_round(
    names: Sequence[str],
    round_index: int,
    previous: Sequence[str] = (),
    *,
    rng: _Random | None = None,
) -> RoundPlan:
    """算出一轮的发言顺序：全员各一次，且与上一轮不同。

    ``previous`` 传上一轮的顺序（第一轮传空）：
    * 本轮第一位不能是上一轮最后一位（否则同一人连说两条）；
    * 本轮顺序不能与上一轮完全相同（否则观众能看出固定循环）。
    """
    pool = [str(name) for name in names if str(name)]
    if not pool:
        return RoundPlan(index=max(1, int(round_index)), order=())
    chooser = rng or random.Random(f"round|{round_index}|{'|'.join(pool)}")
    prev = [str(name) for name in previous if str(name)]
    prev_last = prev[-1] if prev else ""
    order = list(pool)
    for _ in range(MAX_RESHUFFLE):
        chooser.shuffle(order)
        if len(order) > 1 and order == prev:
            continue
        if prev_last and order[0] == prev_last and len(order) > 1:
            continue
        return RoundPlan(index=max(1, int(round_index)), order=tuple(order))
    # 兜底：以确定性的旋转跳出「刚好洗回原样」的死循环
    fallback = _rotate(list(pool), max(1, int(round_index)))
    if prev_last and len(fallback) > 1 and fallback[0] == prev_last:
        fallback = _rotate(fallback)
    return RoundPlan(index=max(1, int(round_index)), order=tuple(fallback))


def total_rounds_for(speech_budget: int, expert_count: int) -> int:
    """把「本场发言条数预算」换算成轮数（每轮全员一次，向上取整）。"""
    budget = max(1, int(speech_budget or 0))
    experts = max(1, int(expert_count or 1))
    return max(1, math.ceil(budget / experts))


def plan_schedule(
    names: Sequence[str],
    speech_budget: int,
    *,
    rng: _Random | None = None,
) -> list[RoundPlan]:
    """整场排期：返回每一轮的顺序（轮数由发言条数预算决定）。"""
    pool = [str(name) for name in names if str(name)]
    if not pool:
        return []
    rounds = total_rounds_for(speech_budget, len(pool))
    plans: list[RoundPlan] = []
    previous: tuple[str, ...] = ()
    for index in range(1, rounds + 1):
        plan = plan_round(pool, index, previous, rng=rng)
        plans.append(plan)
        previous = plan.order
    return plans


def speech_counts(plans: Sequence[RoundPlan]) -> dict[str, int]:
    """按排期统计每人发言次数（用于校验「差距不超过 1」）。"""
    counts: dict[str, int] = {}
    for plan in plans:
        for name in plan.order:
            counts[name] = counts.get(name, 0) + 1
    return counts


__all__ = [
    "MAX_RESHUFFLE",
    "RoundPlan",
    "plan_round",
    "plan_schedule",
    "speech_counts",
    "total_rounds_for",
]
