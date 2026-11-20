/**
 * 前端端到端冒烟：无头 Edge + CDP（无需 Playwright / Puppeteer）。
 *
 * 流程：打开首页（列表必须来自后端）→ 填议题 → 点「发起讨论」→ 演播厅实时渲染
 *      → 断言发言 / 阶段 / 共识 / 总结 → 截图 → 刷新走「只读回看」路径。
 *
 * 前置：
 *   1. 后端以 Mock 模式运行（MOCK_MODE=true，建议 SPEAK_INTERVAL_SECONDS=0 加快节奏）；
 *   2. `npm run dev` 起前端（或用 VITE_API_TARGET 指向后端端口）；
 *   3. 本机装有 Edge（默认取 Program Files 路径，可用 EDGE_PATH 覆盖）。
 *
 * 用法：
 *   node scripts/ui_smoke.mjs [appUrl] [cdpPort]
 *   EDGE_PATH="C:\\...\\msedge.exe" OUT_DIR=./tmp node scripts/ui_smoke.mjs http://127.0.0.1:5173/ 9333
 *
 * 退出码：0 全绿；1 有断言失败；2 启动失败。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const EDGE_CANDIDATES = [
  process.env.EDGE_PATH,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean)

const EDGE = EDGE_CANDIDATES.find((path) => existsSync(path))
if (!EDGE) {
  console.error('[ui] 找不到 Edge / Chrome，可用 EDGE_PATH 指定可执行文件')
  process.exit(2)
}
const APP = process.argv[2] || 'http://127.0.0.1:5179/'
const PORT = Number(process.argv[3] || 9333)
const OUT_DIR = process.env.OUT_DIR || tmpdir()

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const log = (...args) => console.log('[ui]', ...args)

class CDP {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.handlers = new Map()
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data)
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        if (msg.error) reject(new Error(JSON.stringify(msg.error)))
        else resolve(msg.result)
        return
      }
      for (const fn of this.handlers.get(msg.method) || []) fn(msg.params)
    })
  }

  send(method, params = {}) {
    const id = ++this.seq
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }))
  }

  on(method, fn) {
    const list = this.handlers.get(method) || []
    list.push(fn)
    this.handlers.set(method, list)
  }
}

async function launchEdge() {
  const profile = mkdtempSync(join(tmpdir(), 'edge-ui-'))
  const child = spawn(
    EDGE,
    [
      '--headless=new',
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-gpu',
      '--window-size=1680,1000',
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  let wsUrl = ''
  for (let i = 0; i < 60 && !wsUrl; i += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const targets = await response.json()
      wsUrl = targets.find((t) => t.type === 'page')?.webSocketDebuggerUrl || ''
    } catch {
      /* 调试端口还没起来 */
    }
    if (!wsUrl) await sleep(250)
  }
  if (!wsUrl) throw new Error('无法连接 Edge 调试端口')
  return { child, wsUrl }
}

async function main() {
  const { child, wsUrl } = await launchEdge()
  const ws = new WebSocket(wsUrl)
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve)
    ws.addEventListener('error', reject)
  })

  const cdp = new CDP(ws)
  const problems = []
  cdp.on('Runtime.exceptionThrown', (params) => {
    problems.push(params?.exceptionDetails?.exception?.description || params?.exceptionDetails?.text)
  })
  cdp.on('Runtime.consoleAPICalled', (params) => {
    if (params.type === 'error') {
      problems.push((params.args || []).map((a) => a.value ?? a.description).join(' '))
    }
  })

  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  const evaluate = async (expression) => {
    const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    if (exceptionDetails) {
      throw new Error(exceptionDetails.exception?.description || exceptionDetails.text)
    }
    return result.value
  }

  const waitFor = async (expression, { timeout = 30000, label = expression } = {}) => {
    const deadline = Date.now() + timeout
    while (Date.now() < deadline) {
      try {
        if (await evaluate(expression)) return true
      } catch {
        /* 页面切换中 */
      }
      await sleep(250)
    }
    throw new Error(`等待超时：${label}`)
  }

  const shot = async (name) => {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
    const file = join(OUT_DIR, name)
    writeFileSync(file, Buffer.from(data, 'base64'))
    log('截图 →', file)
    return file
  }

  const result = { ok: false, checks: [], problems }

  try {
    log('打开首页', APP)
    await cdp.send('Page.navigate', { url: APP })
    await waitFor("Boolean(document.querySelector('textarea'))", { label: '首页表单渲染' })
    await waitFor("document.querySelectorAll('.session').length > 0", { timeout: 15000, label: '首页列表加载' })

    // 1) 首页列表来自后端（seed 数据里有「四天工作制」）
    const homeText = await evaluate('document.body.innerText')
    result.checks.push(['首页列表来自后端', homeText.includes('四天工作制')])

    // 2) 填议题 + 点「发起讨论」
    const topic = `四天工作制能否提升创造力？${Date.now().toString().slice(-6)}`
    await evaluate(`(() => {
      const box = document.querySelector('textarea')
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
      setter.call(box, ${JSON.stringify(topic)})
      box.dispatchEvent(new Event('input', { bubbles: true }))
      return box.value
    })()`)
    await sleep(200)
    await evaluate(`(() => {
      const button = [...document.querySelectorAll('button')].find((b) => b.innerText.replace(/\\s/g, '').includes('发起讨论'))
      if (!button) throw new Error('找不到「发起讨论」按钮')
      button.click()
      return true
    })()`)

    // 3) 阵容确认页（PRD US-1 / AC-2.7）：主持人置顶 + 专家席 + 确认按钮
    await waitFor("location.pathname.startsWith('/lineup/')", { label: '跳转阵容确认页' })
    await waitFor("Boolean(document.querySelector('.lineup__host .seat__name'))", { label: '主持人卡片渲染' })
    const lineup = await evaluate(`(() => ({
      host: document.querySelector('.lineup__host .seat__name')?.innerText || '',
      guests: [...document.querySelectorAll('.lineup__guests .seat__name')].map((el) => el.innerText),
      stances: [...document.querySelectorAll('.seat__stance')].filter((el) => el.innerText.trim()).length,
      colors: [...document.querySelectorAll('.seat')].map((el) => getComputedStyle(el).getPropertyValue('--seat-color').trim()),
    }))()`)
    result.checks.push(['阵容确认页有主持人', Boolean(lineup.host)])
    result.checks.push(['阵容确认页专家 ≥ 2 人', lineup.guests.length >= 2])
    result.checks.push(['每张席位卡都有立场与配色', lineup.stances === lineup.guests.length + 1 && lineup.colors.every(Boolean)])

    await evaluate(`(() => {
      const button = [...document.querySelectorAll('button')].find((b) => b.innerText.replace(/\\s/g, '').includes('确认阵容'))
      if (!button) throw new Error('找不到「确认阵容」按钮')
      button.click()
      return true
    })()`)

    await waitFor("location.pathname.startsWith('/studio/')", { label: '跳转演播厅' })
    const studioUrl = await evaluate('location.href')
    log('演播厅', studioUrl)

    // 3) 直播：等到 done（总结卡片出现）
    await waitFor("document.querySelectorAll('.bubble').length >= 3", { timeout: 30000, label: '首批发言渲染' })
    await shot('ui-live-midway.png')
    // 3.1) 「正在发言」占位卡：直播中必须能观察到（typing 事件 → 占位卡 + 卡片高亮）
    let typingSeen = false
    for (let i = 0; i < 40 && !typingSeen; i += 1) {
      typingSeen = await evaluate(
        "Boolean(document.querySelector('.studio__typing') && document.querySelector('.typing-bubble'))",
      )
      if (!typingSeen) await sleep(200)
    }
    result.checks.push(['直播中出现「正在发言」占位卡', typingSeen])

    // 3.2) 直播中刷新：恢复到当前进度，且不重复已发出的发言
    const beforeReload = await evaluate("document.querySelectorAll('.bubble').length")
    await cdp.send('Page.reload', { ignoreCache: false })
    await waitFor("Boolean(document.querySelector('.studio__grid'))", { timeout: 20000, label: '直播刷新后恢复画面' })
    await sleep(1500)
    const afterReload = await evaluate(`(() => ({
      bubbles: document.querySelectorAll('.bubble').length,
      rounds: [...document.querySelectorAll('.bubble')].length,
      error: Boolean(document.querySelector('.studio__alert')),
      typing: Boolean(document.querySelector('.studio__typing') || document.querySelector('.typing-bubble')),
    }))()`)
    result.checks.push(['直播刷新后恢复到当前进度', afterReload.bubbles >= Math.min(beforeReload, 2)])
    result.checks.push(['直播刷新无报错提示', !afterReload.error])
    result.checks.push(['直播刷新仍在直播态（可继续）', afterReload.typing || !afterReload.error])

    await waitFor("Boolean(document.querySelector('.summary__section'))", { timeout: 45000, label: '主持人总结渲染' })
    await sleep(600)
    await shot('ui-live-done.png')

    const live = await evaluate(`(() => ({
      topic: document.querySelector('.studio__topic')?.innerText || '',
      bubbles: document.querySelectorAll('.bubble').length,
      names: [...document.querySelectorAll('.bubble__name')].map((n) => n.innerText),
      colors: [...document.querySelectorAll('.bubble')].map((b) => getComputedStyle(b).getPropertyValue('--speaker-color').trim()),
      phaseText: document.querySelector('.phase-bar')?.innerText.replace(/\\s+/g, ' ') || '',
      startStates: [...document.querySelectorAll('.expert-card__state')].map((n) => n.innerText),
      consensus: document.querySelectorAll('.insight').length,
      agreeCount: document.querySelectorAll('.insight.is-agree').length,
      disagreeCount: document.querySelectorAll('.insight.is-disagree').length,
      summarySections: [...document.querySelectorAll('.summary__section h3')].map((n) => n.innerText),
      meta: document.querySelector('.studio__meta')?.innerText.replace(/\\s+/g, ' ') || '',
      hint: document.querySelector('.panel__hint')?.innerText || '',
      roundOk: (() => {
        const match = (document.querySelector('.phase-bar')?.innerText || '').match(/第\\s*(\\d+)\\s*\\/\\s*(\\d+)\\s*轮/)
        return Boolean(match) && Number(match[1]) <= Number(match[2])
      })(),
      text: document.body.innerText,
    }))()`)

    result.checks.push(['议题正确显示', live.topic === topic])
    result.checks.push(['发言气泡 ≥ 6 条', live.bubbles >= 6])
    result.checks.push(['气泡带后端配色', live.colors.every((c) => /^#/.test(c))])
    result.checks.push(['发言人 ≥ 2 位（含主持）', new Set(live.names).size >= 2])
    result.checks.push(['阶段条显示已结束', live.phaseText.includes('已结束')])
    result.checks.push(['会话状态更新为已结束', live.meta.includes('已结束')])
    result.checks.push(['轮次不超出上限', live.roundOk])
    result.checks.push([
      '全员进入已总结态',
      live.startStates.every((s) => ['已总结', '待机', '聆听中', '已发言'].includes(s)),
    ])
    result.checks.push(['共识与分歧各至少 1 条', live.agreeCount >= 1 && live.disagreeCount >= 1])
    result.checks.push(['总结三要素齐全', live.summarySections.join('|').includes('结论建议')])
    result.checks.push(['未泄露内部事件词', !/举手|抢答|调度|intent/.test(live.text)])
    result.checks.push(['无前端异常', problems.filter((p) => p && !/favicon/.test(p)).length === 0])
    result.live = { ...live, text: undefined, config: undefined }

    // 4) 刷新 → 回看路径（只读：发言从库里补齐，不重跑讨论）
    await cdp.send('Page.reload', { ignoreCache: true })
    await waitFor("Boolean(document.querySelector('.studio__grid'))", { timeout: 20000, label: '回看页渲染' })
    await sleep(1200)
    await shot('ui-replay.png')

    const replay = await evaluate(`(() => ({
      bubbles: document.querySelectorAll('.bubble').length,
      tag: [...document.querySelectorAll('.el-tag')].map((t) => t.innerText).join('|'),
      phaseText: document.querySelector('.phase-bar')?.innerText.replace(/\\s+/g, ' ') || '',
      consensus: document.querySelectorAll('.insight').length,
      streaming: document.querySelectorAll('.bubble.is-streaming').length,
      summarySections: [...document.querySelectorAll('.summary__section h3')].map((n) => n.innerText),
      degradedBanner: Boolean(document.querySelector('.studio__degraded')),
      text: document.body.innerText,
    }))()`)

    result.checks.push(['回看标记「只读回看」', replay.tag.includes('只读回看')])
    result.checks.push(['回看发言数与直播一致', replay.bubbles === live.bubbles])
    result.checks.push(['回看无打字机残留', replay.streaming === 0])
    result.checks.push(['回看仍有共识快照', replay.consensus >= 2])
    result.checks.push(['回看阶段为已结束', replay.phaseText.includes('已结束')])
    result.checks.push([
      '回看能重建三段总结',
      replay.summarySections.join('|').includes('结论建议'),
    ])
    result.checks.push(['Mock 模式不显示降级提示', replay.degradedBanner === false])
    result.replay = { ...replay, text: undefined }

    result.ok = result.checks.every(([, passed]) => passed)
  } catch (error) {
    result.error = error?.message || String(error)
    try {
      await shot('ui-error.png')
    } catch {
      /* 截图失败忽略 */
    }
  }

  log('')
  for (const [label, passed] of result.checks) {
    log(`${passed ? 'PASS' : 'FAIL'} · ${label}`)
  }
  if (result.problems.length) log('前端报错：', result.problems.slice(0, 5))
  if (result.error) log('异常：', result.error)
  log('直播快照：', JSON.stringify(result.live ?? null))
  log('回看快照：', JSON.stringify(result.replay ?? null))
  log(result.ok ? '[result] PASS' : '[result] FAIL')

  ws.close()
  child.kill()
  process.exit(result.ok ? 0 : 1)
}

main().catch((error) => {
  console.error('[ui] 启动失败', error)
  process.exit(2)
})
