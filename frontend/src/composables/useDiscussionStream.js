/**
 * 真实事件流引擎：`EventSource` 订阅 `/api/stream/{id}`，把五种 SSE 事件归约成
 * 与 `useMockPlayback` **完全一致**的状态树，演播厅组件因此无需区分真假数据：
 *
 *   message   → items（带打字机中间态，按 `round` 去重）
 *   status    → seatFeed（公开关注点 + 后端三态兜底）
 *   consensus → agree / disagree（整组覆盖）
 *   phase     → phase / phaseRound / maxRounds
 *   done      → finished + summary（主持人总结）
 *
 * 两条关键约定（决定「屏幕上看到的是不是真的」）：
 * 1. **三态跟着画面走**：`panelists` 是派生值，以「谁正在打字 / 队列下一位是谁」为准，
 *    而不是直接照抄后端 status —— 否则后端已换人、气泡还在打上一位的字；
 * 2. **进度不是装饰**：`round` 由 phase 事件与已播发言条数共同换算
 *    （`message.round` 是发言序号，含开场与收尾，不能直接当轮次用）。
 *
 * 回看（`status === 'ended'`）不订阅事件流：直接读 `messages` / `insights` 一次性渲染，
 * 避免为已结束的场次再触发一次讨论。
 */
import { computed, onBeforeUnmount, ref } from 'vue'

import { getSession, listInsights, listMessages, streamUrl } from '@/api/client'
import { MESSAGE_GAP_MS, TYPE_INTERVAL_MS } from '@/constants'

/** 打字机每 tick 追加的字数（配合 TYPE_INTERVAL_MS ≈ 50 字/秒，接近真人语速） */
const CHARS_PER_TICK = 2
/** done 到达后，把剩余队列补齐前的停顿 */
const TAIL_MS = 260

export function useDiscussionStream(sessionId) {
  const session = ref(null)
  const experts = ref([])
  const messages = ref([])
  /** 正在打字机输出的那条：{ message, shown, focus } */
  const partial = ref(null)
  const agree = ref([])
  const disagree = ref([])
  const summary = ref(null)
  /** 本次生成是否发生过降级（后端 done.degraded=true → 顶部提示「Mock 兜底」） */
  const degraded = ref(false)
  /**
   * 后端 `status` 事件快照：expert_id → { state, focus }。
   * 只用来取**公开关注点**与兜底三态 —— 屏幕上的三态由本端「谁在打字」决定，
   * 否则后端已经换人、气泡还在打上一位的字，状态标注就会对不上。
   */
  const seatFeed = ref({})
  /**
   * 「谁正在发言」：来自后端 `typing` 事件（`{ expert_id, expert_name, round, label }`）。
   * 只用于发言流顶部的占位提示与专家卡片高亮；回看模式不会收到该事件，也就不显示。
   */
  const typing = ref(null)
  const phase = ref('opening')
  /** 后端 phase 事件给出的轮次（权威值）；缺失时由已播发言条数兜底 */
  const phaseRound = ref(0)
  const maxRounds = ref(1)
  const playing = ref(true)
  const finished = ref(false)
  const instant = ref(false)
  const loading = ref(true)
  const error = ref('')

  /** 待打字机消费的发言队列（非响应式，靠 queueVersion 触发重算） */
  const queue = []
  /** 队列版本号：入队 / 出队时自增，让派生的 panelists 跟着重算 */
  const queueVersion = ref(0)
  /** 两位发言人之间的留白截止时间戳（毫秒） */
  let pauseUntil = 0
  /** 已渲染 / 已入队的轮次，用于 SSE 与历史数据的幂等合并 */
  const seenRounds = new Set()
  let timer = null
  let tailTimer = null
  let source = null
  let disposed = false

  function stopTimer() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  function syncPanelists(list) {
    const next = { ...seatFeed.value }
    for (const status of list ?? []) {
      next[status.expert_id] = { state: status.state, focus: status.focus ?? null }
    }
    seatFeed.value = next
  }

  function commit(message) {
    messages.value.push(message)
  }

  function tick() {
    // 换人留白：这段时间席位停在「准备发言」，观感上就是真人在想下一句
    if (Date.now() < pauseUntil) return
    if (!partial.value) {
      const next = queue.shift()
      queueVersion.value += 1
      if (!next) {
        stopTimer()
        return
      }
      partial.value = { message: next, shown: '', focus: next.focus ?? null }
    }
    const { message, shown } = partial.value
    const nextShown = message.content.slice(0, shown.length + CHARS_PER_TICK)
    partial.value = { ...partial.value, shown: nextShown }
    if (nextShown.length >= message.content.length) {
      commit(message)
      partial.value = null
      pauseUntil = Date.now() + MESSAGE_GAP_MS
    }
  }

  function pump() {
    if (disposed || !playing.value) return
    if (!partial.value && !queue.length) return
    if (timer) return
    timer = setInterval(tick, TYPE_INTERVAL_MS)
  }

  /** 入队一条发言（按 round 去重，并快照当时的公开关注点） */
  function enqueue(message) {
    if (!message || seenRounds.has(message.round)) return
    seenRounds.add(message.round)
    // 正式发言落地：同一人的「正在发言」占位立刻退场（由气泡接管）
    if (typing.value && typing.value.expert_id === message.expert_id) typing.value = null
    queue.push({ ...message, focus: seatFeed.value[message.expert_id]?.focus ?? null })
    queueVersion.value += 1
    pump()
  }

  function flushQueue() {
    for (const message of queue.splice(0)) commit(message)
    queueVersion.value += 1
    if (partial.value) {
      commit(partial.value.message)
      partial.value = null
    }
    pauseUntil = 0
    stopTimer()
  }

  function applyEvent(name, payload) {
    switch (name) {
      case 'message':
        enqueue(payload?.message)
        break
      case 'status':
        syncPanelists(payload?.panelists)
        if (payload?.session_status && session.value) session.value.status = payload.session_status
        break
      case 'consensus': {
        // 后端每次只推一栏（agree 或 disagree），另一栏保持上一版
        const items = payload?.items ?? []
        const kind = payload?.type ?? items[0]?.type
        if (kind === 'agree') {
          agree.value = items.filter((item) => item.type === 'agree')
        } else if (kind === 'disagree') {
          disagree.value = items.filter((item) => item.type === 'disagree')
        } else if (items.length) {
          agree.value = items.filter((item) => item.type === 'agree')
          disagree.value = items.filter((item) => item.type === 'disagree')
        }
        break
      }
      case 'phase':
        phase.value = payload?.phase ?? phase.value
        if (payload?.max_rounds) maxRounds.value = Math.max(1, payload.max_rounds)
        phaseRound.value = Math.max(phaseRound.value, payload?.round ?? 0)
        break
      case 'typing':
        // 回看 / 已结束不显示「正在发言」：这只是直播态提示
        typing.value = finished.value || instant.value ? null : { ...payload }
        if (payload?.round) {
          phaseRound.value = Math.max(phaseRound.value, payload.round)
          if (payload.round <= maxRounds.value) phase.value = 'debate'
        }
        break
      case 'done': {
        finished.value = true
        playing.value = false
        phase.value = 'ended'
        typing.value = null
        // done.total_rounds 是「发言条数」，不能直接当轮数：轮次进度以 phase 事件给的轮数为准
        phaseRound.value = Math.max(phaseRound.value, maxRounds.value)
        summary.value = payload?.summary ?? null
        degraded.value = Boolean(payload?.degraded)
        if (payload?.status && session.value) session.value.status = payload.status
        seatFeed.value = {}
        stopTimer()
        if (!queue.length && !partial.value) return
        // 收尾前把没打完的发言一次性补齐
        if (tailTimer) clearTimeout(tailTimer)
        tailTimer = setTimeout(flushQueue, TAIL_MS)
        break
      }
      default:
        break
    }
  }

  function openStream() {
    source = new EventSource(streamUrl(sessionId))
    for (const name of ['message', 'status', 'consensus', 'phase', 'typing', 'done']) {
      source.addEventListener(name, (event) => {
        if (disposed) return
        let payload = null
        try {
          payload = JSON.parse(event.data)
        } catch {
          return
        }
        applyEvent(name, payload?.data ?? payload)
      })
    }
    source.onerror = () => {
      if (disposed) return
      if (source?.readyState === EventSource.CLOSED) {
        error.value = '事件流已断开，可点击「重新连接」继续'
      }
    }
  }

  function closeStream() {
    if (source) {
      source.close()
      source = null
    }
  }

  /** 把已落库的发言按 round 升序提交（回放与续播共用） */
  function applyHistory(history) {
    for (const message of history) {
      seenRounds.add(message.round)
      commit(message)
    }
  }

  /** 总结小标题（与后端 `SummaryOut.heading` 的 Literal 三值保持一致） */
  const SUMMARY_HEADINGS = ['共识', '分歧', '结论建议']

  /**
   * 回看时的总结重建：后端只把总结正文作为「最后一条主持人发言」落库，
   * 因此按空行切段即可无损还原「共识 / 分歧 / 结论建议」三段（不改数据库表）。
   */
  function rebuildSummary(history) {
    const host = experts.value.find((item) => item.is_host)
    if (!host) return null
    const last = [...history].reverse().find((item) => item.expert_id === host.id)
    const text = String(last?.content || '')
      .replace(/\r\n/g, '\n')
      .trim()
    if (!text) return null
    let paragraphs = text
      .split(/\n\s*\n/)
      .map((part) => part.trim())
      .filter(Boolean)
    if (!paragraphs.length) return null
    // 多出来的段落并入最后一段，口径与后端 `split_summary_sections` 一致
    if (paragraphs.length > SUMMARY_HEADINGS.length) {
      const head = paragraphs.slice(0, SUMMARY_HEADINGS.length - 1)
      const tail = paragraphs.slice(SUMMARY_HEADINGS.length - 1).join('')
      paragraphs = [...head, tail]
    }
    return {
      session_id: sessionId,
      sections: paragraphs.map((body, index) => ({ heading: SUMMARY_HEADINGS[index], body })),
      degraded: paragraphs.length < SUMMARY_HEADINGS.length,
      created_at: last?.created_at ?? null,
    }
  }

  async function loadHistory() {
    const [history, insights] = await Promise.all([
      listMessages(sessionId),
      listInsights(sessionId),
    ])
    agree.value = insights?.agree ?? []
    disagree.value = insights?.disagree ?? []
    return history ?? []
  }

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const detail = await getSession(sessionId)
      if (disposed) return
      session.value = detail
      experts.value = detail.experts ?? []
      seatFeed.value = {}

      const history = await loadHistory()
      if (disposed) return

      // 发言条数 ≈ 开场 1 条 + 交锋若干 +（已结束时）收尾 1 条；轮数 = 发言数 / 专家数
      const debateRounds = (ended) => Math.max(1, history.length - (ended ? 2 : 1))
      const roundCount = (speeches) =>
        Math.max(1, Math.ceil(Math.max(1, speeches) / guestCount.value))

      if (detail.status === 'ended') {
        // 只读回放：一次性补齐，不进事件流（真实轮次 = 会后端总发言数 - 开场 - 收尾）
        instant.value = true
        finished.value = true
        playing.value = false
        phase.value = 'ended'
        applyHistory(history)
        // 回看没有 done 事件：有库内 summary 就用，否则从最后一条主持人发言重建
        if (!summary.value) summary.value = rebuildSummary(history)
        maxRounds.value = roundCount(debateRounds(true))
        phaseRound.value = maxRounds.value
        return
      }

      // 刷新 / 断线重连：先补已落库的发言，再订阅实时增量（真实轮次由 phase 事件校正）
      applyHistory(history)
      maxRounds.value = roundCount(debateRounds(false))
      closeStream()
      openStream()
    } catch (err) {
      if (!disposed) error.value = err?.message || '加载讨论失败'
    } finally {
      if (!disposed) loading.value = false
    }
  }

  /** 暂停 / 继续「打字机」渲染（后端产出不受影响） */
  function toggle() {
    playing.value = !playing.value
    if (playing.value) pump()
    else stopTimer()
  }

  /** 立刻结束打字机：把队列与当前这条一次性补齐 */
  function endNow() {
    flushQueue()
  }

  /** 断线兜底：重新订阅（`Last-Event-ID` 由 EventSource 自动带上） */
  function reconnect() {
    error.value = ''
    closeStream()
    if (session.value?.status !== 'ended') openStream()
  }

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

  /** 每位专家的已发言条数（用于「已发言 N 次 / 聆听中」的状态标注） */
  const spokenCounts = computed(() => {
    const counts = {}
    for (const message of messages.value) {
      counts[message.expert_id] = (counts[message.expert_id] ?? 0) + 1
    }
    return counts
  })

  /**
   * 屏幕上的三态：**只认「正在打字的是谁」**。
   *
   * 后端 status 会比画面早 1~2 条（它按生成顺序推进，而前端还在逐字播放），
   * 若直接照抄后端状态，就会出现「标注说 A 在发言，屏幕上还在打 B 的字」。
   * 这里改成：正在打字 → speaking；队列下一位 → preparing；其余 → idle，
   * 关注点仍取后端公开的 focus（排队时快照），保证信息不丢。
   */
  const panelists = computed(() => {
    queueVersion.value // 依赖队列变化
    const active = partial.value?.message ?? null
    const pending = active ? null : (queue[0] ?? null)
    const feed = seatFeed.value
    return experts.value.map((expert) => {
      const server = feed[expert.id] ?? {}
      let state = 'idle'
      let focus = null
      if (active) {
        if (active.expert_id === expert.id) {
          state = 'speaking'
          focus = active.focus ?? server.focus ?? null
        }
      } else if (pending) {
        if (pending.expert_id === expert.id) {
          state = 'preparing'
          focus = pending.focus ?? server.focus ?? null
        }
      } else if (server.state === 'preparing') {
        // 后端已举手准备，但发言还没推到前端：先把「准备发言」亮出来
        state = 'preparing'
        focus = server.focus ?? null
      }
      // 「正在发言」占位：typing 事件先到、正文还没到，卡片要与发言流顶部同步高亮
      if (state === 'idle' && typing.value?.expert_id === expert.id) {
        state = 'speaking'
        focus = server.focus ?? null
      }
      const spoken = (spokenCounts.value[expert.id] ?? 0) + (state === 'speaking' ? 1 : 0)
      return { ...expert, state, focus, spoken }
    })
  })

  /** 专家人数（不含主持人）：把「发言条数」换算成「轮次」时要用 */
  const guestCount = computed(() => {
    const guests = experts.value.filter((item) => !item.is_host).length
    return guests || 1
  })

  /**
   * 真实讨论进度：后端按「每轮全员各说一次」推进，所以轮次 = 已播发言数 / 专家数，
   * 并以 phase 事件给出的 round 为权威值（回看时没有 phase 事件，就用条数折算）。
   */
  const round = computed(() => {
    const shown = messages.value.length + (partial.value ? 1 : 0)
    const derived = Math.max(0, Math.floor(Math.max(0, shown - 1) / guestCount.value))
    return Math.min(Math.max(phaseRound.value, derived), maxRounds.value)
  })

  const speakingCount = computed(
    () => panelists.value.filter((item) => item.state === 'speaking').length,
  )

  load()

  onBeforeUnmount(() => {
    disposed = true
    stopTimer()
    if (tailTimer) clearTimeout(tailTimer)
    closeStream()
  })

  return {
    session,
    experts,
    items,
    messages,
    panelists,
    agree,
    disagree,
    summary,
    degraded,
    phase,
    round,
    maxRounds,
    playing,
    finished,
    instant,
    loading,
    error,
    typedLength,
    speakingCount,
    typing,
    toggle,
    endNow,
    reconnect,
    reload: load,
  }
}
