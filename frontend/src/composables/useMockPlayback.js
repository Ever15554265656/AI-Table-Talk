/**
 * 假数据播放引擎（MOCK）。
 *
 * 真实接入后端时，本文件将被「EventSource 订阅 + message/status/consensus/phase 事件
 * 归约到同一份状态树」的实现替换；对外暴露的字段刻意与 SSE 语义一一对应：
 *   - items       ← message 事件（含正在流式输出的最后一条）
 *   - panelists   ← status 事件里的 PanelistStatus[]（三态 + 公开关注点）
 *   - agree/disagree ← consensus 事件（整组覆盖）
 *   - phase/round ← phase 事件
 */
import { computed, onBeforeUnmount, ref } from 'vue'

import { MESSAGE_GAP_MS, PREPARING_MS, SPEAKING_TAIL_MS, TYPE_INTERVAL_MS } from '@/constants'

export function useMockPlayback(fixture) {
  const source = fixture?.messages ?? []
  const experts = fixture?.experts ?? []
  const total = source.length
  const instant = Boolean(fixture?.instant) || total === 0

  /** 已完整落地的发言 */
  const messages = ref([])
  /** 正在打字机输出的那条：{ message, shown } */
  const partial = ref(null)
  const agree = ref([])
  const disagree = ref([])
  const panelists = ref(experts.map((expert) => ({ ...expert, state: 'idle', focus: null })))
  const phase = ref('opening')
  const round = ref(0)
  const maxRounds = ref(fixture?.max_rounds ?? Math.max(total, 6))
  const playing = ref(!instant)
  const finished = ref(false)

  const timers = new Set()
  let cursor = 0

  function clearTimers() {
    timers.forEach((id) => clearTimeout(id))
    timers.clear()
  }

  function later(fn, ms) {
    const id = setTimeout(() => {
      timers.delete(id)
      fn()
    }, ms)
    timers.add(id)
    return id
  }

  function setState(expertId, state, focus) {
    const target = panelists.value.find((item) => item.id === expertId)
    if (target) {
      target.state = state
      target.focus = focus ?? null
    }
  }

  /** opening（第 1 条）→ debate → wrap_up（最后两条） */
  function phaseForRound(value) {
    if (value <= 1) return 'opening'
    if (total > 3 && value >= total - 1) return 'wrap_up'
    return 'debate'
  }

  /** 共识/分歧按 atRound 节奏"边走边更新"，整组覆盖而非追加 */
  function revealInsights(currentRound) {
    const pick = (items) => items.filter((item) => item.atRound <= currentRound)
    agree.value = pick(fixture?.insights?.agree ?? [])
    disagree.value = pick(fixture?.insights?.disagree ?? [])
  }

  function finish() {
    finished.value = true
    playing.value = false
    phase.value = 'ended'
    round.value = total
    panelists.value.forEach((item) => {
      item.state = 'idle'
      item.focus = null
    })
  }

  function complete(message) {
    messages.value.push(message)
    partial.value = null
    setState(message.expert_id, 'idle', null)
    revealInsights(message.round)
    cursor += 1
    if (cursor >= total) {
      finish()
      return
    }
    if (playing.value) later(startNext, MESSAGE_GAP_MS)
  }

  /** 打字机：逐字显示，完成后停顿 SPEAKING_TAIL_MS 再落库 */
  function typeMessage(message) {
    let index = 0
    const step = () => {
      index += 1
      partial.value = { message, shown: message.content.slice(0, index) }
      if (index < message.content.length) {
        later(step, TYPE_INTERVAL_MS)
        return
      }
      later(() => complete(message), SPEAKING_TAIL_MS)
    }
    step()
  }

  function startNext() {
    if (finished.value || !playing.value) return
    const message = source[cursor]
    if (!message) {
      finish()
      return
    }
    phase.value = phaseForRound(message.round)
    round.value = message.round - 1 // 发言落地后才算走完这一轮
    setState(message.expert_id, 'preparing', message.focus)
    later(() => {
      setState(message.expert_id, 'speaking', message.focus)
      typeMessage(message)
    }, PREPARING_MS)
  }

  function toggle() {
    if (finished.value) return
    playing.value = !playing.value
    // 暂停时允许把当前这条播完；恢复时若空闲则推进下一条
    if (playing.value && !partial.value) startNext()
  }

  /** 跳到终态：一次性补齐全部发言与共识（回看 / 点击"结束讨论"） */
  function endNow() {
    if (finished.value) return
    clearTimers()
    partial.value = null
    messages.value = source.slice()
    agree.value = [...(fixture?.insights?.agree ?? [])]
    disagree.value = [...(fixture?.insights?.disagree ?? [])]
    cursor = total
    finish()
  }

  if (instant) {
    endNow()
  } else {
    later(startNext, 420)
  }

  /** 渲染列表：已落地的发言 + 正在打字的那条 */
  const items = computed(() => {
    const rendered = messages.value.map((message) => ({
      ...message,
      shown: message.content,
      streaming: false,
    }))
    if (partial.value) {
      rendered.push({
        ...partial.value.message,
        shown: partial.value.shown,
        streaming: true,
      })
    }
    return rendered
  })

  const typedLength = computed(() => partial.value?.shown.length ?? 0)
  const speakingCount = computed(() => panelists.value.filter((item) => item.state === 'speaking').length)

  onBeforeUnmount(clearTimers)

  return {
    items,
    messages,
    panelists,
    agree,
    disagree,
    phase,
    round,
    maxRounds,
    playing,
    finished,
    instant,
    typedLength,
    speakingCount,
    toggle,
    endNow,
  }
}
