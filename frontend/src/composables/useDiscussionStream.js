/**
 * 真实事件流引擎：`EventSource` 订阅 `/api/stream/{id}`，把五种 SSE 事件归约成
 * 与 `useMockPlayback` **完全一致**的状态树，演播厅组件因此无需区分真假数据：
 *
 *   message   → items（带打字机中间态，按 `round` 去重）
 *   status    → panelists（三态 + 公开关注点）
 *   consensus → agree / disagree（整组覆盖）
 *   phase     → phase / round / maxRounds
 *   done      → finished + summary（主持人总结）
 *
 * 回看（`status === 'ended'`）不订阅事件流：直接读 `messages` / `insights` 一次性渲染，
 * 避免为已结束的场次再触发一次讨论。
 */
import { computed, onBeforeUnmount, ref } from 'vue'

import { getSession, listInsights, listMessages, streamUrl } from '@/api/client'
import { TYPE_INTERVAL_MS } from '@/constants'

/** 打字机每 tick 追加的字数（后端一条发言 ≤120 字，3 字/tick 约 1s 出头） */
const CHARS_PER_TICK = 3
/** done 到达后，把剩余队列补齐前的停顿 */
const TAIL_MS = 260

export function useDiscussionStream(sessionId) {
  const session = ref(null)
  const experts = ref([])
  const messages = ref([])
  /** 正在打字机输出的那条：{ message, shown } */
  const partial = ref(null)
  const agree = ref([])
  const disagree = ref([])
  const summary = ref(null)
  const panelists = ref([])
  const phase = ref('opening')
  const round = ref(0)
  const maxRounds = ref(1)
  const playing = ref(true)
  const finished = ref(false)
  const instant = ref(false)
  const loading = ref(true)
  const error = ref('')

  /** 待打字机消费的发言队列 */
  const queue = []
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
    const byId = new Map(panelists.value.map((item) => [item.id, item]))
    for (const status of list ?? []) {
      const target = byId.get(status.expert_id)
      if (target) {
        target.state = status.state
        target.focus = status.focus ?? null
      }
    }
  }

  function resetPanelists() {
    panelists.value = experts.value.map((expert) => ({ ...expert, state: 'idle', focus: null }))
  }

  function commit(message) {
    messages.value.push(message)
  }

  function tick() {
    if (!partial.value) {
      const next = queue.shift()
      if (!next) {
        stopTimer()
        return
      }
      partial.value = { message: next, shown: '' }
    }
    const { message, shown } = partial.value
    const nextShown = message.content.slice(0, shown.length + CHARS_PER_TICK)
    partial.value = { message, shown: nextShown }
    if (nextShown.length >= message.content.length) {
      commit(message)
      partial.value = null
    }
  }

  function pump() {
    if (disposed || !playing.value) return
    if (!partial.value && !queue.length) return
    if (timer) return
    timer = setInterval(tick, TYPE_INTERVAL_MS)
  }

  /** 入队一条发言（按 round 去重，保证历史 + 实时不重复） */
  function enqueue(message) {
    if (!message || seenRounds.has(message.round)) return
    seenRounds.add(message.round)
    queue.push(message)
    pump()
  }

  function flushQueue() {
    for (const message of queue.splice(0)) commit(message)
    if (partial.value) {
      commit(partial.value.message)
      partial.value = null
    }
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
        maxRounds.value = Math.max(1, payload?.max_rounds ?? maxRounds.value)
        round.value = Math.min(Math.max(round.value, payload?.round ?? 0), maxRounds.value)
        break
      case 'done': {
        finished.value = true
        playing.value = false
        phase.value = 'ended'
        const total = payload?.total_rounds ?? round.value
        if (maxRounds.value <= 1) maxRounds.value = Math.max(1, total)
        round.value = Math.min(Math.max(round.value, total), maxRounds.value)
        summary.value = payload?.summary ?? null
        if (payload?.status && session.value) session.value.status = payload.status
        panelists.value.forEach((item) => {
          item.state = 'idle'
          item.focus = null
        })
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
    for (const name of ['message', 'status', 'consensus', 'phase', 'done']) {
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
      round.value = Math.max(round.value, message.round ?? 0)
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
      resetPanelists()

      const history = await loadHistory()
      if (disposed) return

      if (detail.status === 'ended') {
        // 只读回放：一次性补齐，不进事件流
        instant.value = true
        finished.value = true
        playing.value = false
        phase.value = 'ended'
        applyHistory(history)
        maxRounds.value = Math.max(1, round.value)
        return
      }

      // 刷新 / 断线重连：先补已落库的发言，再订阅实时增量
      applyHistory(history)
      maxRounds.value = Math.max(1, history.length)
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
    toggle,
    endNow,
    reconnect,
    reload: load,
  }
}
