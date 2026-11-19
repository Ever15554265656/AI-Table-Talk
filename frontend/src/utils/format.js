/** 时间展示工具：后端返回 UTC naive ISO8601（如 2026-11-19T05:01:33），前端按本地时区展示。 */

const pad2 = (value) => String(value).padStart(2, '0')

function toDate(iso) {
  if (!iso) return null
  const date = new Date(iso.endsWith('Z') ? iso : `${iso}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 2026-11-19 13:01 */
export function formatDateTime(iso) {
  const date = toDate(iso)
  if (!date) return '—'
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours(),
  )}:${pad2(date.getMinutes())}`
}

/** 刚刚 / 3 分钟前 / 2 小时前 / 昨天 / 日期 */
export function formatRelative(iso) {
  const date = toDate(iso)
  if (!date) return '—'
  const diffMinutes = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMinutes < 1) return '刚刚'
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`
  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) return `${diffHours} 小时前`
  if (diffHours < 48) return '昨天'
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`
}
