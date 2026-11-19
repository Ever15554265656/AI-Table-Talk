"""议题域规则：议题关键词 → 专家席位 → 分域发言模板（纯数据 + 纯函数）。

为什么单独一层
--------------
「专家阵容要跟着议题走」「发言要提到议题里的词」这两条是**内容规则**，不是 Prompt，
也不该散落在 ``llm.py`` 的 Mock 池里 —— 真实模式（DeepSeek）与 Mock 模式必须共享同一套
领域划分，否则两边的选角口径会漂移。因此：

* ``llm.py``（Mock 阵容 / 发言）直接调用本模块；
* ``flows/lineup.py`` 把域提示写进 user message，让真实模型知道该往哪个领域选人；
* ``flows/turn_taking.py`` 用 ``stance_label`` 判断「立场是否对立」，据此加权随机点名。

三层结构
--------
1. **域 ``TopicDomain``**：关键词表 + 该域 6 个互斥职业席位 + 三类意图的发言模板；
2. **席位 ``DomainSeat``**：职业 / Title / 立场标签（支持·反对·中立）/ 论据内核 / 举手关注点；
3. **席位方案 ``SeatPlan``**：``seat_plan(topic, count)`` 把域规则落成「这一场具体有谁」。

硬约束（对应本轮需求，改动时不要破坏）
--------------------------------------
* 领域识别只看**议题全文**关键词，不做随机抽取；
* 同一场里 支持 / 反对 / 中立 必须都出现（≥2 位专家时就要求「支持 + 反对」并存的互补结构）；
* 发言模板必须显式嵌入议题关键词（``{kw}`` / ``{kw2}``），禁止「这个问题」「该议题」这类空指代；
* 反驳模板必须能点名上一位发言人（``{reply}``），不允许无指向的反驳。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

#: 立场标签三档（互斥；用于版面展示与「不一边倒」校验）
STANCE_TYPES: tuple[str, str, str] = ("支持", "反对", "中立")

#: 兜底立场句式：``{topic}`` 议题短句 / ``{claim}`` 论据内核
STANCE_TEMPLATES: dict[str, str] = {
    "支持": "支持「{topic}」：{claim}",
    "反对": "反对「{topic}」：{claim}",
    "中立": "有条件中立：「{topic}」取决于——{claim}",
}

#: 中立立场的显式前缀（判定顺序必须在「不该 / 不认同」等自由文本标记之前）
NEUTRAL_PREFIXES: tuple[str, ...] = ("有条件中立", "中立", "中性", "中立派")

#: 重复占位（专家数 > 域内席位数）时的立场翻转表：同一职业换一个人、换一头立场
STANCE_FLIP: dict[str, str] = {"支持": "反对", "反对": "支持", "中立": "中立"}

#: 议题里的疑问标记：用于在没有命中域关键词时，从议题里切出「主词」当关键词
QUESTION_MARKERS: tuple[str, ...] = (
    "应不应该",
    "该不该",
    "要不要",
    "能不能",
    "会不会",
    "值不值得",
    "是否",
    "如何",
    "怎么",
    "为什么",
    "值不值",
)

#: Mock 姓名池（按席位下标取用；与主持人「沈知远」不重名）
DEFAULT_NAMES: tuple[str, ...] = (
    "林岚",
    "周衡",
    "郑弈",
    "罗屿",
    "韩澈",
    "陆准",
    "裴清",
    "许野",
)


@dataclass(frozen=True)
class DomainSeat:
    """域内一个职业席位（身份 + 立场倾向 + 论据内核）。

    ``claims`` 给两个论据内核：同一个域被选到的次数多时，同职业的第二位（占位重复时）
    会用第二个内核 + 翻转立场，避免「两个同名职业说同一句话」。
    """

    profession: str
    title: str
    stance_type: str
    claims: tuple[str, ...]
    focus: str


@dataclass(frozen=True)
class TopicDomain:
    """一个议题域：识别关键词 + 席位表。

    发言模板不再挂在这里：Mock 发言改由 :mod:`backend.arguments` 按
    「论证包 + 轮次分层」现场合成，这里只保留「谁上场、立场倾向、论据内核」。
    """

    key: str
    label: str
    keywords: tuple[str, ...]
    seats: tuple[DomainSeat, ...]


@dataclass(frozen=True)
class SeatPlan:
    """一场阵容里具体的一个专家席位（已填充议题与立场文本）。"""

    name: str
    profession: str
    title: str
    stance: str
    stance_type: str
    claim: str
    focus: str


# ============================================================ 域 1：技术与互联网

TECH_DOMAIN: TopicDomain = TopicDomain(
    key="tech",
    label="技术与互联网",
    keywords=(
        "AI",
        "人工智能",
        "算法",
        "程序员",
        "程序",
        "代码",
        "软件",
        "互联网",
        "科技",
        "技术",
        "大模型",
        "芯片",
        "自动化",
        "数据",
        "工程师",
        "数字化",
        "云计算",
        "机器",
    ),
    seats=(
        DomainSeat(
            profession="技术专家",
            title="某大厂首席架构师",
            stance_type="支持",
            claims=(
                "工具先吃掉重复环节，剩下的判断反而更值钱",
                "真正被替代的是不会用工具的那部分工作",
            ),
            focus="先划清哪些岗位会被重排",
        ),
        DomainSeat(
            profession="伦理学者",
            title="某高校科技伦理研究员",
            stance_type="反对",
            claims=(
                "追责链没写清，出事时没人担得起",
                "把判断权交出去之前，得先有可追责的审计链路",
            ),
            focus="追问责任与审计边界",
        ),
        DomainSeat(
            profession="劳动经济学家",
            title="某研究院劳动经济研究员",
            stance_type="中立",
            claims=(
                "短期看的是结构调整，长期才谈得上总量",
                "效率提升不等于安全，分配机制才是关键变量",
            ),
            focus="要一组可对照的就业数据",
        ),
        DomainSeat(
            profession="科技行业投资人",
            title="某早期基金合伙人",
            stance_type="支持",
            claims=(
                "这轮机会是把人从重复劳动挪到设计与验收",
                "谁先把工具编进流程，谁的成本结构就先松动",
            ),
            focus="算一笔替代成本与回报",
        ),
        DomainSeat(
            profession="教育学者",
            title="某师范大学教授",
            stance_type="中立",
            claims=(
                "培养方式不变，岗位和毕业生之间就还是差一截",
                "课程不改，供给和需求还会继续错配",
            ),
            focus="看人才供给这一端",
        ),
        DomainSeat(
            profession="安全工程师",
            title="某安全实验室负责人",
            stance_type="反对",
            claims=(
                "自动生成的东西没人读得完，漏洞更隐蔽、更晚暴露",
                "自动化把攻击面一起放大，出事时止损成本翻倍",
            ),
            focus="盯住被放大的攻击面",
        ),
    ),
)


# ============================================================ 域 2：职场与组织

WORKPLACE_DOMAIN: TopicDomain = TopicDomain(
    key="workplace",
    label="职场与组织",
    keywords=(
        "公司",
        "职场",
        "员工",
        "工作",
        "管理",
        "办公",
        "团队",
        "老板",
        "加班",
        "薪酬",
        "绩效",
        "招聘",
        "裁员",
        "工时",
        "四天",
        "远程",
        "组织",
        "同事",
        "考勤",
        "弹性",
    ),
    seats=(
        DomainSeat(
            profession="HR负责人",
            title="某上市公司人力资源总监",
            stance_type="支持",
            claims=(
                "工时不降、考核不改，压缩工时只会把五天塞进四天",
                "招聘端看得出来，员工把弹性排在薪资前面",
            ),
            focus="谈考核口径怎么改",
        ),
        DomainSeat(
            profession="企业创始人",
            title="某 SaaS 公司创始人",
            stance_type="反对",
            claims=(
                "节奏一压，客户响应先崩，成本最后落在一线",
                "小机构没有冗余，工时一砍就只能靠加班补回来",
            ),
            focus="算清楚交付与人力成本",
        ),
        DomainSeat(
            profession="组织行为学者",
            title="某高校副教授",
            stance_type="中立",
            claims=(
                "减少无效会议确实提效，但跨部门协同会先卡住",
                "效率提升有多少来自工时，实验里很难剥离",
            ),
            focus="要一组可复现的实验数据",
        ),
        DomainSeat(
            profession="劳动法专家",
            title="某律所合伙人",
            stance_type="中立",
            claims=(
                "认定口径和加班费不写清楚，事后无从举证",
                "弹性排班缺少合同条款支撑，落地一定变形",
            ),
            focus="盯劳动合同与工时条款",
        ),
        DomainSeat(
            profession="心理学教授",
            title="某大学心理学系教授",
            stance_type="支持",
            claims=(
                "恢复感上来，缺勤和离职率先降",
                "持续高压下的效率是借来的，透支早晚要还",
            ),
            focus="谈员工的心理负荷",
        ),
        DomainSeat(
            profession="门店运营经理",
            title="某连锁品牌区域运营经理",
            stance_type="反对",
            claims=(
                "排班是按需求排的，人少一个就得有人顶班",
                "一线没有弹性空间，工时一改就是人手缺口",
            ),
            focus="讲一线排班的刚性",
        ),
    ),
)


# ============================================================ 域 3：教育与校园

EDUCATION_DOMAIN: TopicDomain = TopicDomain(
    key="education",
    label="教育与校园",
    keywords=(
        "教育",
        "学生",
        "学校",
        "考试",
        "课堂",
        "校园",
        "教师",
        "老师",
        "作业",
        "家长",
        "班级",
        "手机",
        "招生",
        "升学",
        "家校",
    ),
    seats=(
        DomainSeat(
            profession="一线教师",
            title="某市重点中学班主任",
            stance_type="支持",
            claims=(
                "反馈越快，练得越准，课堂时间才够用",
                "自控力还没成形时，讲道理不如先给边界",
            ),
            focus="讲课堂管理的真实场景",
        ),
        DomainSeat(
            profession="教育学者",
            title="某师范大学教授",
            stance_type="反对",
            claims=(
                "替学生把思考做完，练出来的是依赖不是能力",
                "学生要学的是方法，学校却只教结论",
            ),
            focus="质疑一刀切的真实效果",
        ),
        DomainSeat(
            profession="学生代表",
            title="某高中学生代表",
            stance_type="中立",
            claims=(
                "学生在意的是规则透明，而不是一句口号",
                "同一个要求，落在不同学生身上效果差很多",
            ),
            focus="说学生这一侧的规则诉求",
        ),
        DomainSeat(
            profession="家长代表",
            title="某小学家长委员会成员",
            stance_type="支持",
            claims=(
                "家里管不住的那一段，学校不接就真的没人接",
                "边界清楚，家长和学校的分工才落得下去",
            ),
            focus="谈家庭的监管成本",
        ),
        DomainSeat(
            profession="政策研究员",
            title="某教育科学研究院副研究员",
            stance_type="中立",
            claims=(
                "关键看执行成本与责任怎么分，而不是口号响不响",
                "规则要配可执行的例外条款，否则只能一刀切",
            ),
            focus="算执行成本与责任边界",
        ),
        DomainSeat(
            profession="校长",
            title="某公办中学校长",
            stance_type="反对",
            claims=(
                "一刀切落到登记和通报上，学校就只剩执法成本",
                "学生的差异千差万别，一刀切最容易被绕过",
            ),
            focus="盯学校的管理成本",
        ),
    ),
)


# ============================================================ 域 4：城市与公共政策

CITY_DOMAIN: TopicDomain = TopicDomain(
    key="city",
    label="城市与公共政策",
    keywords=(
        "城市",
        "住房",
        "落户",
        "交通",
        "人口",
        "房价",
        "租房",
        "地铁",
        "通勤",
        "户籍",
        "学区",
        "规划",
        "燃油车",
        "限行",
        "小区",
        "社区",
        "城镇化",
    ),
    seats=(
        DomainSeat(
            profession="城市规划师",
            title="某市规划院副总规划师",
            stance_type="支持",
            claims=(
                "供给与需求的账要一起算，单点改造只会把压力推到隔壁",
                "承载力有上限，规划得先给流量留出余量",
            ),
            focus="谈承载力与规划余量",
        ),
        DomainSeat(
            profession="社会学者",
            title="某高校社会学系教授",
            stance_type="反对",
            claims=(
                "被政策挤出去的，往往是对成本最敏感的那批人",
                "治理不能只看总量，还要看社区被怎么改变",
            ),
            focus="关注被挤出的人群",
        ),
        DomainSeat(
            profession="经济学家",
            title="某智库宏观经济研究员",
            stance_type="中立",
            claims=(
                "成本与收益要摊到几年看，短期数据会骗人",
                "政策一收紧，人和产业会先用脚投票",
            ),
            focus="算长期成本收益",
        ),
        DomainSeat(
            profession="本地居民代表",
            title="某社区议事会成员",
            stance_type="反对",
            claims=(
                "每天多花的通勤时间，是实打实的成本",
                "政策落地之前，受影响的人连一次听证都没被问过",
            ),
            focus="说居民的实际体验",
        ),
        DomainSeat(
            profession="政策研究者",
            title="某公共政策研究中心主任",
            stance_type="中立",
            claims=(
                "供给、住房、通勤要配套，单拎一条都推不动",
                "关键是执行细则与过渡期怎么设计",
            ),
            focus="谈配套与过渡期",
        ),
        DomainSeat(
            profession="交通工程师",
            title="某设计院交通规划工程师",
            stance_type="支持",
            claims=(
                "容量是算得出来的，不调需求，修再多也会堵回去",
                "通勤时间对产业布局的影响，比多数人想的更直接",
            ),
            focus="用路网容量算一笔账",
        ),
    ),
)


# ============================================================ 域 5：青少年与注意力

YOUTH_DOMAIN: TopicDomain = TopicDomain(
    key="youth",
    label="青少年与注意力",
    keywords=(
        "青少年",
        "短视频",
        "游戏",
        "注意力",
        "未成年人",
        "沉迷",
        "屏幕",
        "刷手机",
        "上网",
        "直播",
        "网络",
    ),
    seats=(
        DomainSeat(
            profession="儿科医生",
            title="某儿童医院儿保科主任",
            stance_type="支持",
            claims=(
                "注意力和睡眠的损伤，在一线是看得见的",
                "使用时长一上去，户外活动和视力先出问题",
            ),
            focus="讲一线看到的真实损伤",
        ),
        DomainSeat(
            profession="心理学者",
            title="某高校心理学系副教授",
            stance_type="反对",
            claims=(
                "把问题全算在一个工具头上，是一种偷懒的归因",
                "孩子真正缺的是可控的成就感和稳定的陪伴",
            ),
            focus="质疑单一归因",
        ),
        DomainSeat(
            profession="教育专家",
            title="某教育发展研究中心研究员",
            stance_type="中立",
            claims=(
                "内容分级和时长工具比一刀切更可执行",
                "关键看学校和家庭能不能提供可验证的替代方案",
            ),
            focus="谈可执行的替代方案",
        ),
        DomainSeat(
            profession="内容平台从业者",
            title="某短视频平台内容治理负责人",
            stance_type="中立",
            claims=(
                "即时反馈确实被放大，时长提示只是最低配",
                "平台能做的是把防护做成默认，而不是选项",
            ),
            focus="讲平台侧的治理手段",
        ),
        DomainSeat(
            profession="青少年家长代表",
            title="某家长社群发起人",
            stance_type="支持",
            claims=(
                "家长最需要的是可执行的边界，而不是一句少用",
                "孩子已经会拿规则反过来跟家长谈条件了",
            ),
            focus="说家长的执行困境",
        ),
        DomainSeat(
            profession="中学班主任",
            title="某初中班主任",
            stance_type="反对",
            claims=(
                "真正出问题的往往是家里没人管的那几个，禁令抓不到根",
                "一刀切只会把使用转到地下，问题更难被发现",
            ),
            focus="讲一线观察到的例外",
        ),
    ),
)


# ============================================================ 兜底域（立场互补三角）

GENERAL_DOMAIN: TopicDomain = TopicDomain(
    key="general",
    label="通用议题",
    keywords=(),
    seats=(
        DomainSeat(
            profession="支持方经济学人",
            title="某智库高级研究员",
            stance_type="支持",
            claims=(
                "先算总账：收益在前、成本在后，别只盯眼前这一笔",
                "方向没错，问题出在节奏和配套",
            ),
            focus="算总账与推进节奏",
        ),
        DomainSeat(
            profession="反对方领域学者",
            title="某高校教授",
            stance_type="反对",
            claims=(
                "难点不在该不该，而在代价最后由谁承担",
                "缺可执行细则，落地一定变形",
            ),
            focus="追问代价与细则",
        ),
        DomainSeat(
            profession="中立派社会观察员",
            title="某社会调查机构负责人",
            stance_type="中立",
            claims=(
                "要看不同人群的实际体验差异，不能拿平均数下结论",
                "先看试点数据，别用个案替代规律",
            ),
            focus="要一组分群数据",
        ),
        DomainSeat(
            profession="一线从业者代表",
            title="某行业一线从业者",
            stance_type="支持",
            claims=(
                "现场的感受是流程太厚、人太薄，改一改确实有用",
                "先把能落地的部分做掉，比等一个完美方案现实",
            ),
            focus="讲一线的真实负担",
        ),
        DomainSeat(
            profession="政策评估专家",
            title="某政策评估中心副主任",
            stance_type="中立",
            claims=(
                "要看指标怎么定、谁来验收，否则好意图也会走形",
                "过渡期安排得好不好，决定政策能不能站住",
            ),
            focus="谈指标与验收口径",
        ),
        DomainSeat(
            profession="公益组织负责人",
            title="某公益基金会项目负责人",
            stance_type="反对",
            claims=(
                "最容易被忽略的那部分人，通常也是最后被通知的那批",
                "政策一旦只算效率，公平的代价会留在沉默的一方身上",
            ),
            focus="盯容易被忽略的人",
        ),
    ),
)


#: 域注册表：顺序即优先级（关键词命中数相同时取靠前的域）
DOMAINS: tuple[TopicDomain, ...] = (
    YOUTH_DOMAIN,
    EDUCATION_DOMAIN,
    TECH_DOMAIN,
    WORKPLACE_DOMAIN,
    CITY_DOMAIN,
)

#: 兜底域：任何议题都至少有「支持 / 反对 / 中立」三类立场互补的角色
FALLBACK_DOMAIN: TopicDomain = GENERAL_DOMAIN


# ============================================================ 纯函数：识别 / 取词


def topic_brief(topic: str, limit: int = 24) -> str:
    """把议题压成能自然念进一句话里的短句（议题最长 200 字，直接塞进台词会撑爆字段）。"""
    text = " ".join(str(topic or "").split()) or "本次议题"
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，、；：,.;:?!。 ") + "…"


def detect_domain(topic: str) -> TopicDomain:
    """按**议题全文**命中的关键词数量识别议题域；一个都没命中时落到兜底域。"""
    lowered = str(topic or "").lower()
    best = FALLBACK_DOMAIN
    best_hits = 0
    for domain in DOMAINS:
        hits = sum(1 for keyword in domain.keywords if keyword.lower() in lowered)
        if hits > best_hits:
            best, best_hits = domain, hits
    return best


def _drop_nested(keywords: list[str]) -> list[str]:
    """去掉被更长关键词包含的短词（命中「程序员」时不再重复取「程序」）。"""
    return [
        keyword
        for keyword in keywords
        if not any(keyword != other and keyword in other for other in keywords)
    ]


def fallback_keywords(topic: str) -> list[str]:
    """议题没命中任何域关键词时，从疑问标记前切出主词（如「远程办公是否…」→「远程办公」）。"""
    text = " ".join(str(topic or "").split()).strip("「」“”\"' ")
    for marker in QUESTION_MARKERS:
        index = text.find(marker)
        if index > 0:
            head = text[:index].strip("，、；：,.;:!?。 ")
            if len(head) >= 2:
                return [head[:8]]
    head = text.strip("，、；：,.;:!?。 ")
    return [head[:8]] if head else ["这个议题"]


def match_keywords(topic: str, domain: TopicDomain | None = None) -> list[str]:
    """议题里真实出现的域关键词（按出现位置排序），供发言模板显式引用。"""
    domain = domain or detect_domain(topic)
    lowered = str(topic or "").lower()
    hits = [
        (lowered.find(keyword.lower()), keyword)
        for keyword in domain.keywords
        if keyword.lower() in lowered
    ]
    hits.sort(key=lambda item: item[0])
    ordered = _drop_nested([keyword for _, keyword in hits])
    return ordered or fallback_keywords(topic)


# ============================================================ 纯函数：立场


def format_stance(brief: str, stance_type: str, claim: str) -> str:
    """把「立场标签 + 论据内核」拼成一句可上屏的立场文案（标签放在句首，一眼可辨）。"""
    template = STANCE_TEMPLATES.get(stance_type, STANCE_TEMPLATES["中立"])
    return template.format(topic=brief, claim=claim)


def stance_label(stance: str) -> str:
    """把任意立场文本归到 支持 / 反对 / 中立 三档（用于「不一边倒」校验与点名加权）。"""
    text = " ".join(str(stance or "").split())
    if not text:
        return "中立"
    # 先认显式的中立前缀：「有条件中立：『该不该…』」里的议题含「不该」，
    # 若不先判前缀，会被下面的自由文本标记误判成「反对」。
    if text.startswith(NEUTRAL_PREFIXES):
        return "中立"
    if text.startswith("支持"):
        return "支持"
    if text.startswith("反对"):
        return "反对"
    head = text[:16]
    if any(marker in head for marker in ("反对", "不支持", "不认同", "不该")):
        return "反对"
    if any(marker in head for marker in ("支持", "赞成", "方向对", "值得推进")):
        return "支持"
    return "中立"


def stance_spread(labels: Sequence[str]) -> bool:
    """立场是否「不一边倒」。

    * 只有 1 位专家：无从谈起，视为通过；
    * 2 位专家：至少两个不同标签；
    * ≥3 位专家：必须同时出现「支持」与「反对」（中立可有可无）。
    """
    values = [label for label in labels if label]
    if len(values) < 2:
        return True
    unique = set(values)
    if len(values) == 2:
        return len(unique) >= 2
    return {"支持", "反对"} <= unique


# ============================================================ 纯函数：席位方案


def seat_plan(
    topic: str,
    count: int,
    *,
    names: Sequence[str] = DEFAULT_NAMES,
) -> list[SeatPlan]:
    """按议题（**完整文本**）与专家人数生成席位方案。

    规则：
    1. 先识别议题域，再按域内席位顺序取人 —— 不做「固定职业池随机抽」；
    2. 立场标签按席位预置，前缀保证「支持 / 反对 / 中立」分布互补；
    3. 专家数超过域内席位数时循环取人，并翻转立场 + 换第二个论据内核
       （同一职业、不同的两个人，不会说着同一句话）。
    """
    domain = detect_domain(topic)
    brief = topic_brief(topic, 24)
    total = max(0, int(count))
    pool = tuple(names) or DEFAULT_NAMES
    seats = domain.seats

    plans: list[SeatPlan] = []
    for index in range(total):
        seat = seats[index % len(seats)]
        cycle = index // len(seats)
        claim = seat.claims[cycle % len(seat.claims)]
        stance_type = seat.stance_type if cycle % 2 == 0 else STANCE_FLIP[seat.stance_type]
        plans.append(
            SeatPlan(
                name=pool[index % len(pool)],
                profession=seat.profession,
                title=seat.title,
                stance=format_stance(brief, stance_type, claim),
                stance_type=stance_type,
                claim=claim,
                focus=seat.focus,
            )
        )
    return plans


def seat_for_profession(topic: str, profession: str) -> DomainSeat | None:
    """按职业反查域内席位（Mock 发言要靠它取「论据内核」与关注点）。"""
    target = str(profession or "").strip()
    if not target:
        return None
    domain = detect_domain(topic)
    for candidate_domain in (domain, *DOMAINS, FALLBACK_DOMAIN):
        for seat in candidate_domain.seats:
            if seat.profession == target:
                return seat
    for candidate_domain in (domain, *DOMAINS, FALLBACK_DOMAIN):
        for seat in candidate_domain.seats:
            if seat.profession in target or target in seat.profession:
                return seat
    return None


__all__ = [
    "DEFAULT_NAMES",
    "DOMAINS",
    "DomainSeat",
    "FALLBACK_DOMAIN",
    "NEUTRAL_PREFIXES",
    "QUESTION_MARKERS",
    "STANCE_FLIP",
    "STANCE_TEMPLATES",
    "STANCE_TYPES",
    "SeatPlan",
    "TopicDomain",
    "detect_domain",
    "fallback_keywords",
    "format_stance",
    "match_keywords",
    "seat_for_profession",
    "seat_plan",
    "stance_label",
    "stance_spread",
    "topic_brief",
]

