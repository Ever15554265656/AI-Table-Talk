/**
 * 纯前端假数据（MOCK）。
 *
 * 设计约束：
 * 1. 字段与 docs/API.md 完全一致（SessionListItem / ExpertOut / MessageOut /
 *    ConsensusItemOut / SummaryOut），后续把 `getStudioMock` 换成 `fetch` 即可接后端；
 * 2. 发言对象的 `focus`、共识条目的 `atRound` 是**仅本文件使用**的演示字段，
 *    对应真实 SSE 的 `status.panelists[].focus` 与「第 N 条发言后刷新」的节奏；
 * 3. 颜色一律取 PANEL_COLORS（后端调色板副本），前端渲染时只读 expert_color。
 */
import { PANEL_COLORS } from '@/constants'

const MINUTE = 60 * 1000
const pad2 = (n) => String(n).padStart(2, '0')

/** 在 UTC naive ISO 串上加减分钟（保持与后端一致的 "YYYY-MM-DDTHH:mm:ss" 格式） */
function shiftMinutes(iso, minutes) {
  const base = new Date(`${iso}Z`).getTime() + minutes * MINUTE
  return new Date(base).toISOString().slice(0, 19)
}

/** 构造一个席位（ExpertOut） */
function seat(sessionId, index, name, profession, title, stance) {
  return {
    id: `${sessionId}-p${pad2(index)}`,
    session_id: sessionId,
    name,
    profession,
    title,
    stance,
    color: PANEL_COLORS[index % PANEL_COLORS.length],
    is_host: index === 0,
  }
}

/**
 * 构造发言列表（MessageOut）。
 * rows: [{ seat, text, focus }]——seat 为席位序号（0 即主持人）
 */
function buildMessages(sessionId, experts, rows, startAt) {
  const bySeat = new Map(experts.map((e) => [e.id.slice(-3), e]))
  return rows.map((row, index) => {
    const round = index + 1
    const speaker = bySeat.get(`p${pad2(row.seat)}`)
    return {
      id: `${sessionId}-m${pad2(round)}`,
      session_id: sessionId,
      expert_id: speaker.id,
      expert_name: speaker.name,
      expert_profession: speaker.profession,
      expert_title: speaker.title,
      expert_color: speaker.color,
      content: row.text,
      round,
      created_at: shiftMinutes(startAt, round),
      focus: row.focus ?? null,
    }
  })
}

/** 构造一条共识 / 分歧（ConsensusItemOut + 演示用 atRound） */
function insight(sessionId, type, index, atRound, content, expertIds) {
  return {
    id: `${sessionId}-c${pad2(index)}`,
    session_id: sessionId,
    type,
    content,
    expert_ids: expertIds.map((seatIndex) => `${sessionId}-p${pad2(seatIndex)}`),
    created_at: shiftMinutes('2026-11-19T01:20:00', atRound * 2),
    atRound,
  }
}

/* ============================================================
   一、首页列表
   ============================================================ */
export const sessionList = [
  {
    id: 'smp-four-day-week',
    topic: '公司是否应全面推行四天工作制？',
    expert_count: 6,
    status: 'live',
    created_at: '2026-11-19T01:10:00',
    expert_total: 7,
    message_count: 12,
  },
  {
    id: 'smp-short-video-attention',
    topic: '短视频是否高估了对青少年注意力的伤害？',
    expert_count: 4,
    status: 'live',
    created_at: '2026-11-19T02:05:00',
    expert_total: 5,
    message_count: 8,
  },
  {
    id: 'smp-open-model',
    topic: '开源大模型会终结闭源商业模型吗？',
    expert_count: 6,
    status: 'ended',
    created_at: '2026-11-18T13:00:00',
    expert_total: 7,
    message_count: 14,
  },
  {
    id: 'smp-remote-innovation',
    topic: '远程办公是否降低了团队的创新力？',
    expert_count: 4,
    status: 'created',
    created_at: '2026-11-19T03:12:00',
    expert_total: 5,
    message_count: 0,
  },
  {
    id: 'smp-ai-copyright',
    topic: 'AI 生成内容是否应享有著作权保护？',
    expert_count: 5,
    status: 'created',
    created_at: '2026-11-19T03:40:00',
    expert_total: 6,
    message_count: 0,
  },
  {
    id: 'smp-hukou-policy',
    topic: '一线城市是否应继续放宽落户门槛？',
    expert_count: 3,
    status: 'created',
    created_at: '2026-11-19T04:02:00',
    expert_total: 4,
    message_count: 0,
  },
]

/* ============================================================
   二、直播中样例：四天工作制（1 主持 + 6 专家）
   ============================================================ */
const FOUR_DAY_ID = 'smp-four-day-week'

const fourDayExperts = [
  seat(FOUR_DAY_ID, 0, '沈知远', '财经媒体', '资深圆桌主持人', '保持中立，只负责开场、追问与串联，不预设立场'),
  seat(FOUR_DAY_ID, 1, '林岚', '组织行为学', '某高校副教授', '会议压掉三成之后，四天制的交付反而更快'),
  seat(FOUR_DAY_ID, 2, '郑野', '互联网', '分布式团队负责人', '招聘市场已经用投递量投票，四天制是硬性吸引力'),
  seat(FOUR_DAY_ID, 3, '何嘉', '制造业', '工厂运营总监', '产线排程是连续函数，少一天就是硬缺口'),
  seat(FOUR_DAY_ID, 4, '苏昀', '劳动法', '执业律师', '工时压缩不等于工作量压缩，加班认定必须先写清楚'),
  seat(FOUR_DAY_ID, 5, '唐砚', '经济学', '宏观研究员', '中小企业现金流撑不住多雇一个人'),
  seat(FOUR_DAY_ID, 6, '白露', '人力咨询', '资深顾问', '只在知识型岗位做一年期试点才谈得上成立'),
]

const fourDayMessages = buildMessages(
  FOUR_DAY_ID,
  fourDayExperts,
  [
    { seat: 0, text: '今天只讨论一个问题：公司是否该全面推行四天工作制？请各位先给判断，再说依据。', focus: '先定调，再要依据' },
    { seat: 1, text: '我在两家公司做过对照：会议密度压掉三成后，四天制的交付反而更快。', focus: '盯住会议密度这个变量' },
    { seat: 3, text: '我这边不行。产线是连续排程，少一天就是硬缺口，客户不会等我们。', focus: '核实产能缺口有多大' },
    { seat: 5, text: '制造业只是一环。真正的问题是中小企业现金流撑不住多雇一个人。', focus: '把成本算进讨论' },
    { seat: 2, text: '但招聘端已经给出信号：不写四天制的岗位，投递量明显更少。', focus: '招聘竞争力是否被低估' },
    { seat: 4, text: '提醒一句，工时压缩不等于工作量压缩，加班认定不写清楚，先出事的是法务。', focus: '等一个合规边界' },
    { seat: 6, text: '我建议把「全面」两个字拿掉，先在知识型岗位做一年期试点。', focus: '试点范围怎么划' },
    { seat: 1, text: '那试点也得有指标，否则一年后大家只记得自己少上了一天班。', focus: '要求可量化的成效指标' },
    { seat: 3, text: '只要能给出连续产线的例外条款，我不是完全反对。', focus: '例外条款能否落地' },
    { seat: 5, text: '例外条款越多，「全面推行」就越像一句口号。', focus: '质疑政策一致性' },
    { seat: 2, text: '我承认在制造与零售，四天制眼下更像是雇主品牌工具。', focus: '行业差异是否被抹平' },
    { seat: 0, text: '看来真正的分歧不在「几天」，而在「谁承担切换成本」，这点请两位正面回应。', focus: '收束到成本由谁承担' },
  ],
  '2026-11-19T01:11:00',
)

const fourDayInsights = {
  agree: [
    insight(FOUR_DAY_ID, 'agree', 1, 5, '四天制的效果高度依赖岗位类型，知识型工作与连续型产线的结论不能互相套用', [1, 3, 6]),
    insight(FOUR_DAY_ID, 'agree', 2, 8, '无论是否推行，都需要可量化的产出指标，否则无法判断成效', [1, 4, 6]),
    insight(FOUR_DAY_ID, 'agree', 3, 11, '直接砍掉一天而不改流程，只会把五天的工作压进四天', [1, 3, 5]),
  ],
  disagree: [
    insight(FOUR_DAY_ID, 'disagree', 4, 10, '切换成本由谁承担：一方主张企业先行试错，另一方认为需要政策或行业补贴配套，中小企业的承受能力是争议焦点', [5, 2]),
    insight(FOUR_DAY_ID, 'disagree', 5, 12, '「全面推行」是否现实：一方认为应先试点再扩围，另一方认为不设统一时限就永远不会真正落地', [6, 5]),
  ],
}

/* ============================================================
   三、直播中样例：短视频与青少年注意力（1 主持 + 4 专家）
   ============================================================ */
const SHORT_VIDEO_ID = 'smp-short-video-attention'

const shortVideoExperts = [
  seat(SHORT_VIDEO_ID, 0, '程未', '传播学', '媒体研究圆桌主持', '保持中立，只追问证据强度，不预设结论'),
  seat(SHORT_VIDEO_ID, 1, '顾南', '发展心理学', '青少年心理研究者', '十年纵向数据不支持「短视频直接损害注意力」的强判断'),
  seat(SHORT_VIDEO_ID, 2, '梁澈', '神经科学', '认知实验室研究员', '睡眠被挤压是更可信的中介变量'),
  seat(SHORT_VIDEO_ID, 3, '方黎', '中学教育', '一线班主任', '我看到的是作业拖拉与深夜刷屏同时发生'),
  seat(SHORT_VIDEO_ID, 4, '齐桓', '平台治理', '内容安全研究员', '推荐强度比使用时长更能解释个体差异'),
]

const shortVideoMessages = buildMessages(
  SHORT_VIDEO_ID,
  shortVideoExperts,
  [
    { seat: 0, text: '短视频对青少年的注意力，究竟是伤害还是替罪羊？先请各位给出结论。', focus: '等一个可检验的结论' },
    { seat: 1, text: '纵向数据里注意力指标是十年缓慢下滑，和短视频普及曲线并不吻合。', focus: '对照两条曲线的时间点' },
    { seat: 3, text: '我在班里看到另一面：作业拖拉的孩子，几乎都在睡前刷到很晚。', focus: '要一线观察的样本量' },
    { seat: 2, text: '那更可能是睡眠被挤压的间接效应，而不是注意力被直接削弱。', focus: '锁定睡眠这个中介变量' },
    { seat: 4, text: '平台侧的推荐强度差异，比使用时长更能解释个体差异。', focus: '推荐机制是否被忽略' },
    { seat: 1, text: '所以归因到「短视频」这个词上，反而放过了睡眠与作业结构。', focus: '警惕概念化的归因' },
    { seat: 3, text: '可家长能改变的只有时长，让他们等一项十年队列研究并不现实。', focus: '家长可操作的抓手' },
    { seat: 0, text: '争议其实落在「证据强度」与「家长期待」之间，谁该先让步？', focus: '收束到证据与行动的张力' },
  ],
  '2026-11-19T02:06:00',
)

const shortVideoInsights = {
  agree: [
    insight(SHORT_VIDEO_ID, 'agree', 1, 5, '睡眠时长是被反复提到的影响路径，比「注意力被削弱」更接近可观测的机制', [2, 3]),
    insight(SHORT_VIDEO_ID, 'agree', 2, 8, '把问题笼统归因到「短视频」，会掩盖家庭作息与作业结构的作用', [1, 2]),
  ],
  disagree: [
    insight(SHORT_VIDEO_ID, 'disagree', 3, 7, '证据门槛之争：一方坚持先有因果证据再下结论，另一方认为家长的干预窗口等不起十年研究', [1, 3]),
    insight(SHORT_VIDEO_ID, 'disagree', 4, 8, '治理对象之争：一方主张限制使用时长，另一方认为应优先约束推荐强度与深夜推送', [4, 3]),
  ],
}

/* ============================================================
   四、已结束样例（含总结）：开源大模型与闭源商业模型（1 主持 + 6 专家）
   ============================================================ */
const OPEN_MODEL_ID = 'smp-open-model'

const openModelExperts = [
  seat(OPEN_MODEL_ID, 0, '顾言', '科技法律', '知识产权律师·圆桌主持', '保持中立，只负责收束争议与追问依据'),
  seat(OPEN_MODEL_ID, 1, '周衡', '云计算', '某云厂商首席架构师', '长上下文与工程化交付上，闭源仍领先一个身位'),
  seat(OPEN_MODEL_ID, 2, '郑弈', '开源社区', '大模型开源项目维护者', '通用能力一旦追平，价格战会先压垮闭源'),
  seat(OPEN_MODEL_ID, 3, '罗屿', '投资', '早期基金合伙人', '模型会变成水电煤，护城河在应用层'),
  seat(OPEN_MODEL_ID, 4, '沈迟', '企业 IT', '制造业数字化负责人', '采购只看确定性与责任主体'),
  seat(OPEN_MODEL_ID, 5, '韩澈', '安全研究', '数据合规研究员', '权重可控性决定企业敢不敢用'),
  seat(OPEN_MODEL_ID, 6, '陆准', '产品', 'AI 应用创业者', '成本是唯一标准，推理便宜一半产品就换形态'),
]

const openModelMessages = buildMessages(
  OPEN_MODEL_ID,
  openModelExperts,
  [
    { seat: 0, text: '今天的题目是：开源大模型会终结闭源商业模型吗？请各位先给结论，再讲依据。', focus: '先要结论，再要依据' },
    { seat: 1, text: '短期不会。长上下文、工程化交付和责任承担，闭源仍领先一个身位。', focus: '比较工程化能力' },
    { seat: 2, text: '长尾市场已经失守了，通用能力一旦追平，价格战会先压垮闭源。', focus: '估算价格战的时间点' },
    { seat: 3, text: '护城河在应用层，模型本身会很快变成水电煤。', focus: '寻找应用层壁垒' },
    { seat: 4, text: '我们采购只看两件事：确定性和合规责任，谁签字谁负责。', focus: '责任主体是否可替代' },
    { seat: 5, text: '权重可控性决定企业敢不敢用，开源在这点上天然占优。', focus: '追问数据出境边界' },
    { seat: 6, text: '成本是唯一标准。推理成本每降一半，我们的产品形态就换一次。', focus: '盯住推理单价曲线' },
    { seat: 1, text: '成本降下来的是推理，不是把一次事故兜住的能力。', focus: '区分成本与责任' },
    { seat: 2, text: '但事故兜底也可以由服务商在开源权重上做，为什么必须闭源？', focus: '责任能否外包给服务商' },
    { seat: 4, text: '因为审计报告需要有主体，权重下载到本地之后没人能给我盖章。', focus: '审计主体如何确认' },
    { seat: 3, text: '所以未来可能不是谁终结谁，而是开源做底座、闭源做责任。', focus: '验证分层结论' },
    { seat: 5, text: '这个组合在实践中会遇到许可条款冲突，合规成本会转移给使用方。', focus: '许可条款冲突' },
    { seat: 6, text: '那对创业者反而是好事，我们只需要最便宜的那一层。', focus: '创业者的真实选择' },
    { seat: 0, text: '今天至少有一点清楚了：争论焦点已经从「能力」转到「责任与成本由谁承担」。感谢各位。', focus: '收束全场争议' },
  ],
  '2026-11-18T13:01:00',
)

const openModelInsights = {
  agree: [
    insight(OPEN_MODEL_ID, 'agree', 1, 5, '闭源模型在长上下文、工程化交付与责任承担上的优势，短期难以被开源追平', [1, 4]),
    insight(OPEN_MODEL_ID, 'agree', 2, 8, '推理成本会持续下降，但事故责任与审计主体的问题不会随成本一起消失', [1, 5]),
    insight(OPEN_MODEL_ID, 'agree', 3, 12, '通用模型能力趋于同质化后，差异化更多来自应用层与交付能力', [3, 6]),
  ],
  disagree: [
    insight(OPEN_MODEL_ID, 'disagree', 4, 10, '开源是否会吃掉通用模型市场：一方认为长尾已经失守，另一方认为企业只为确定性与合规付费', [2, 1, 4]),
    insight(OPEN_MODEL_ID, 'disagree', 5, 12, '责任可否外包：一方认为服务商可在开源权重上提供兜底，另一方坚持审计报告必须有可追责主体', [2, 4]),
  ],
}

const openModelSummary = {
  session_id: OPEN_MODEL_ID,
  sections: [
    { heading: '共识', body: '双方都承认推理成本是决定商业模型的关键变量，也都认同闭源在长上下文与工程化交付上仍有短期优势。' },
    { heading: '分歧', body: '争议集中在两处：通用模型市场是否会被开源吃光，以及事故责任能否由服务商在开源权重之上承担；双方都未让步。' },
    { heading: '结论建议', body: '若以成本为先，可优先评估开源底座加自建交付；若业务需要责任边界与 SLA 保障，仍应选择可追责的闭源服务。' },
  ],
  degraded: false,
  created_at: '2026-11-18T13:16:00',
}

/* ============================================================
   五、样例索引与读取函数
   ============================================================ */
const studioFixtures = {
  [FOUR_DAY_ID]: {
    session: sessionList[0],
    experts: fourDayExperts,
    messages: fourDayMessages,
    insights: fourDayInsights,
    summary: null,
  },
  [SHORT_VIDEO_ID]: {
    session: sessionList[1],
    experts: shortVideoExperts,
    messages: shortVideoMessages,
    insights: shortVideoInsights,
    summary: null,
  },
  [OPEN_MODEL_ID]: {
    session: sessionList[2],
    experts: openModelExperts,
    messages: openModelMessages,
    insights: openModelInsights,
    summary: openModelSummary,
  },
}

export function getSession(id) {
  return sessionList.find((item) => item.id === id) ?? null
}

/** 演播厅数据：直播→逐条播放；已结束→一次性静态渲染（instant=true） */
export function getStudioMock(id) {
  const fixture = studioFixtures[id]
  if (!fixture) return null
  return { ...fixture, instant: fixture.session.status === 'ended' }
}

/** 新建讨论时使用的临时阵容池（真实实现应在后端生成） */
const CAST_POOL = [
  ['林岚', '组织行为学', '某高校副教授', '先看数据，再谈立场'],
  ['郑野', '互联网', '分布式团队负责人', '规模化的组织经验优先'],
  ['何嘉', '制造业', '工厂运营总监', '任何方案都要能落到产线'],
  ['苏昀', '劳动法', '执业律师', '先确认边界，再谈弹性'],
  ['唐砚', '经济学', '宏观研究员', '从成本与激励看问题'],
  ['白露', '人力咨询', '资深顾问', '关注可执行性与试点设计'],
  ['顾南', '发展心理学', '青少年心理研究者', '警惕把相关性当因果'],
  ['梁澈', '神经科学', '认知实验室研究员', '机制先于结论'],
  ['齐桓', '平台治理', '内容安全研究员', '规则设计决定结果'],
  ['罗屿', '投资', '早期基金合伙人', '看长期结构而非短期噪音'],
]

function buildPlaceholderLineup(sessionId, expertCount) {
  const host = seat(sessionId, 0, '沈知远', '财经媒体', '资深圆桌主持人', '保持中立，只负责开场、追问与串联，不预设立场')
  const guests = CAST_POOL.slice(0, expertCount).map((spec, index) =>
    seat(sessionId, index + 1, spec[0], spec[1], spec[2], spec[3]),
  )
  return [host, ...guests]
}

/** 假提交：本地生成一条"待开演"讨论并写入内存列表 */
export function createLocalSession({ topic, expert_count: expertCount }) {
  const id = `local-${Date.now().toString(36)}`
  const session = {
    id,
    topic,
    expert_count: expertCount,
    status: 'created',
    created_at: new Date().toISOString().slice(0, 19),
    expert_total: expertCount + 1,
    message_count: 0,
  }
  sessionList.unshift(session)
  studioFixtures[id] = {
    session,
    experts: buildPlaceholderLineup(id, expertCount),
    messages: [],
    insights: { agree: [], disagree: [] },
    summary: null,
  }
  return session
}


