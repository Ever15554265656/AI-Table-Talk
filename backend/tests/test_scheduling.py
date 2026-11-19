"""TDD 用例：``backend/flows/scheduling.py`` 均衡轮转调度。

验收口径（问题 2）：
1. 每一轮全员各发言一次（发言次数差距全场不超过 1）；
2. 相邻轮次顺序不同，同一人不连说两条；
3. 同一场给定种子可复现（排障与测试需要）。
"""

from __future__ import annotations

import random

from backend.flows import scheduling


def test_round_contains_every_expert_once():
    plan = scheduling.plan_round(["A", "B", "C", "D"], 1)
    assert sorted(plan.order) == ["A", "B", "C", "D"]
    assert plan.index == 1


def test_adjacent_rounds_differ_and_do_not_chain_the_same_speaker():
    plans = scheduling.plan_schedule(["A", "B", "C", "D", "E"], 20, rng=random.Random(7))
    for previous, current in zip(plans, plans[1:]):
        assert current.order != previous.order
        assert current.order[0] != previous.order[-1]


def test_speech_counts_are_balanced():
    plans = scheduling.plan_schedule(["A", "B", "C"], 20, rng=random.Random(3))
    counts = scheduling.speech_counts(plans)
    assert set(counts) == {"A", "B", "C"}
    assert max(counts.values()) - min(counts.values()) <= 1
    assert sum(counts.values()) == len(plans) * 3


def test_total_rounds_rounds_up_the_speech_budget():
    assert scheduling.total_rounds_for(20, 4) == 5
    assert scheduling.total_rounds_for(20, 5) == 4
    assert scheduling.total_rounds_for(6, 3) == 2
    assert scheduling.total_rounds_for(1, 8) == 1


def test_schedule_is_reproducible_with_the_same_seed():
    first = scheduling.plan_schedule(["A", "B", "C"], 12, rng=random.Random(11))
    second = scheduling.plan_schedule(["A", "B", "C"], 12, rng=random.Random(11))
    assert [plan.order for plan in first] == [plan.order for plan in second]


def test_single_expert_still_produces_a_schedule():
    plans = scheduling.plan_schedule(["A"], 6)
    assert len(plans) == 6
    assert all(plan.order == ("A",) for plan in plans)
