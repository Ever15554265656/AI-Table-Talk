/**
 * 全局展示常量。
 *
 * 与 docs/API.md 的枚举一一对应，页面只从这里取文案，禁止各处硬编码，
 * 这样后端契约变化时只需改这一个文件（对应 AC-1.8「文案字典可断言」）。
 */

/** 讨论状态（sessions.status） */
export const SESSION_STATUS_LABEL = {
  created: '待开演',
  live: '直播中',
  ended: '已结束',
}

export const SESSION_STATUS_TAG_TYPE = {
  created: 'info',
  live: 'danger',
  ended: 'success',
}

/** 阶段：opening / debate / wrap_up 来自 SSE phase 事件；ended 为终态展示位 */
export const PHASES = [
  { key: 'opening', label: '开场' },
  { key: 'debate', label: '自由讨论' },
  { key: 'wrap_up', label: '收尾' },
  { key: 'ended', label: '已结束' },
]

export const PHASE_LABEL = Object.fromEntries(PHASES.map((p) => [p.key, p.label]))

export function phaseIndex(phase) {
  const index = PHASES.findIndex((p) => p.key === phase)
  return index < 0 ? 0 : index
}

/** 席位三态：与 PanelistStatus.state 严格一致，不多不少（PRD R4） */
export const EXPERT_STATES = [
  { key: 'idle', label: '待机', tagType: 'info' },
  { key: 'preparing', label: '准备发言', tagType: 'warning' },
  { key: 'speaking', label: '发言中', tagType: 'danger' },
]

export const STATE_LABEL = Object.fromEntries(EXPERT_STATES.map((s) => [s.key, s.label]))
export const STATE_TAG_TYPE = Object.fromEntries(EXPERT_STATES.map((s) => [s.key, s.tagType]))

/**
 * 专家卡片上给观众看的四档状态（本轮要求）：
 * 聆听中 | 正在发言 | 已发言 | 已总结。
 *
 * 与后端三态的关系：``preparing`` 也显示成「正在发言」（观众不需要看到内部的两步），
 * ``idle`` 再按发言次数分成「聆听中 / 已发言」，讨论结束后所有人统一为「已总结」。
 */
export const SEAT_STATE = {
  LISTENING: 'listening',
  SPEAKING: 'speaking',
  SPOKEN: 'spoken',
  SUMMARIZED: 'summarized',
}

export const SEAT_STATE_LABEL = {
  listening: '聆听中',
  speaking: '正在发言',
  spoken: '已发言',
  summarized: '已总结',
}

export const SEAT_STATE_TAG_TYPE = {
  listening: 'info',
  speaking: 'danger',
  spoken: 'success',
  summarized: 'success',
}

/** 立场标签三档（与后端 stance 文案前缀对应），用于卡片上的立场 tag */
export const STANCE_TAG_TYPE = {
  支持: 'success',
  反对: 'danger',
  中立: 'info',
}

export function stanceTagLabel(stance) {
  const text = String(stance || '').trim()
  if (!text) return '中立'
  if (text.startsWith('有条件中立') || text.startsWith('中立')) return '中立'
  if (text.startsWith('支持')) return '支持'
  if (text.startsWith('反对')) return '反对'
  return '中立'
}

/**
 * 待机席位的细分文案：还没接过话筒 vs 已经发过言。
 * 后端三态只有 idle，但观众需要一眼看出「他在听」而不是「他掉线了」。
 */
export const IDLE_LABEL = {
  waiting: '等待发言',
  listening: '聆听中',
}

/** 无公开关注点时的占位文案（AC-4.3：不留空白） */
export const FOCUS_PLACEHOLDER = '旁观中…'

/** 主持台当前行为（AC-4.8）：由阶段推导，不暴露内部 intent 字段 */
export const HOST_BEHAVIOR_LABEL = {
  opening: '开场引题',
  debate: '追问与串联',
  wrap_up: '正在整理结论',
  ended: '已给出总结',
}

/** 打字机速率（毫秒/字） */
export const TYPE_INTERVAL_MS = 40
/** 一条发言结束后，席位回到"待机"前的停顿 */
export const SPEAKING_TAIL_MS = 700
/** 两位发言人之间的间隔 */
export const MESSAGE_GAP_MS = 950
/** "准备发言"到"发言中"的间隔 */
export const PREPARING_MS = 480

/** 创建表单约束：与 backend/schemas.py 的 SessionCreate 完全一致 */
export const TOPIC_MAX_LEN = 200
export const TOPIC_MIN_LEN = 1
export const EXPERT_COUNT_MIN = 2
export const EXPERT_COUNT_MAX = 8
export const DEFAULT_EXPERT_COUNT = 4
export const QUICK_EXPERT_COUNTS = [3, 4, 6]

/** 后端 PANEL_COLOR_PALETTE（9 色）的前端副本，仅用于假数据；
 *  真实接入后颜色一律读 expert.color / message.expert_color，前端绝不自行取色。 */
export const PANEL_COLORS = [
  '#F87171',
  '#FBBF24',
  '#34D399',
  '#60A5FA',
  '#A78BFA',
  '#F472B6',
  '#22D3EE',
  '#A3E635',
  '#FB923C',
]

/** 响应式三档的分界（px），与各页面 CSS 的媒体查询保持一致 */
export const BREAKPOINTS = {
  wide: 1600,
  compact: 1024,
}
