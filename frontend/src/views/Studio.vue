<script setup>
/**
 * 演播厅（直播 / 回看共用），数据全部来自后端：
 *   GET /api/sessions/{id}          → 议题 + 席位（主持人在前）
 *   GET /api/sessions/{id}/messages → 已落库发言（刷新续播 / 只读回看）
 *   GET /api/sessions/{id}/insights → 共识与分歧快照
 *   GET /api/stream/{id}            → SSE：message / status / consensus / phase / done
 *
 * 三档响应式（PRD §6.4 的精神 + 本次任务的显式要求）：
 *   ≥1600px  三列：主发言区 | 专家状态 | 共识分歧
 *   1024~1599 两列：主发言区 | (专家状态 / 共识分歧 上下分栏)
 *   <1024px  单列：状态条 → 主发言区 → 共识分歧
 * 每个区域都在自己的容器内 overflow:auto，整页不滚动。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, RefreshRight, SwitchButton, VideoPause, VideoPlay } from '@element-plus/icons-vue'

import ConsensusPanel from '@/components/ConsensusPanel.vue'
import PhaseBar from '@/components/PhaseBar.vue'
import SpeakerBubble from '@/components/SpeakerBubble.vue'
import StatusWindow from '@/components/StatusWindow.vue'
import SummaryPanel from '@/components/SummaryPanel.vue'
import TypingBubble from '@/components/TypingBubble.vue'
import { SESSION_STATUS_LABEL } from '@/constants'
import { useDiscussionStream } from '@/composables/useDiscussionStream'

const props = defineProps({
  id: { type: String, required: true },
})

const router = useRouter()

const {
  session,
  experts,
  items,
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
  typing,
  toggle,
  endNow,
  reconnect,
  reload,
} = useDiscussionStream(props.id)

const hostId = computed(() => experts.value.find((item) => item.is_host)?.id ?? '')

/* ---------------- 主发言区：仅在贴近底部时自动跟随（AC-6.4） ---------------- */
const stageRef = ref(null)
const unseen = ref(0)
const following = ref(true)
let lastCount = 0

function scrollToBottom(force = false) {
  const el = stageRef.value
  if (!el) return
  if (force) {
    following.value = true
    unseen.value = 0
  }
  el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = stageRef.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  following.value = distance < 40
  if (following.value) unseen.value = 0
}

watch([() => items.value.length, typedLength], async () => {
  const count = items.value.length
  await nextTick()
  if (following.value) {
    scrollToBottom()
  } else if (count > lastCount) {
    unseen.value += count - lastCount
  }
  lastCount = count
})

onMounted(() => {
  lastCount = items.value.length
  scrollToBottom()
})

const isReplay = computed(() => instant.value || session.value?.status === 'ended')
/** 只读态标签：真·回看 vs 刚刚播完 */
const readOnlyLabel = computed(() => (instant.value ? '只读回看' : '讨论已结束'))
const statusLabel = computed(() => SESSION_STATUS_LABEL[session.value?.status] ?? '')
/** 「正在发言」提示：回看 / 已结束时一律不显示 */
const typingSeat = computed(() => {
  if (!typing.value || isReplay.value || finished.value) return null
  const expert = experts.value.find((item) => item.id === typing.value.expert_id)
  return {
    ...typing.value,
    color: expert?.color ?? '#60A5FA',
    role: expert ? `${expert.profession} · ${expert.title}` : '',
    isHost: Boolean(expert?.is_host),
  }
})
const streamHint = computed(() => {
  if (!session.value) return ''
  if (finished.value) return '讨论已结束'
  if (!playing.value) return '已暂停渲染'
  if (typingSeat.value) return `${typingSeat.value.expert_name} 正在发言…`
  const speaker = panelists.value.find((item) => item.state === 'speaking')
  return speaker ? `${speaker.name} 正在输出…` : '等待下一位…'
})
const messageCount = computed(() => items.value.length)
</script>

<template>
  <div v-if="loading" class="studio studio--empty">
    <el-skeleton :rows="5" animated style="width: min(680px, 90%)" />
  </div>

  <div v-else-if="!session" class="studio studio--empty">
    <el-empty :description="error || '未找到该讨论，可能已过期或链接有误'">
      <el-button type="primary" @click="router.push('/')">返回首页</el-button>
      <el-button @click="reload()">重试</el-button>
    </el-empty>
  </div>

  <div v-else class="studio">
    <header class="studio__head">
      <el-button text :icon="ArrowLeft" class="studio__back" @click="router.push('/')">
        返回首页
      </el-button>

      <div class="studio__title">
        <h1 class="studio__topic" :title="session.topic">{{ session.topic }}</h1>
        <div class="studio__meta">
          <span>{{ experts.length }} 个席位</span>
          <span class="dot">·</span>
          <span>{{ messageCount }} 条发言</span>
          <span class="dot">·</span>
          <span>{{ statusLabel }}</span>
        </div>
      </div>

      <div class="studio__controls">
        <template v-if="!isReplay">
          <el-button size="small" :icon="playing ? VideoPause : VideoPlay" @click="toggle()">
            {{ playing ? '暂停' : '继续' }}
          </el-button>
          <el-button size="small" :icon="SwitchButton" :disabled="finished" @click="endNow()">
            跳到最新
          </el-button>
        </template>
        <el-tag v-else size="small" type="success" effect="plain">{{ readOnlyLabel }}</el-tag>
        <el-button
          v-if="error && !isReplay"
          size="small"
          type="warning"
          plain
          :icon="RefreshRight"
          @click="reconnect()"
        >
          重新连接
        </el-button>
        <el-button size="small" :icon="RefreshRight" @click="reload()">刷新</el-button>
      </div>
    </header>

    <el-alert
      v-if="error && !isReplay"
      class="studio__alert"
      type="warning"
      :closable="false"
      show-icon
      :title="error"
    />

    <!-- 真实模式重试后仍失败、本次内容由 Mock 兜底时，演播厅顶部给出明确提示 -->
    <el-alert
      v-if="degraded"
      class="studio__degraded"
      type="warning"
      :closable="false"
      show-icon
      title="本次生成降级（Mock 兜底）"
    />

    <PhaseBar
      class="studio__phase"
      :phase="phase"
      :round="round"
      :max-rounds="maxRounds"
      :status="session.status"
    />

    <main class="studio__grid">
      <section class="studio__stage panel">
        <header class="panel__head">
          <span class="panel__title">现场发言</span>
          <span class="panel__hint">{{ streamHint }}</span>
        </header>

        <!-- 「正在发言」提示条：typing 事件一到就亮，正文落屏后自动退场 -->
        <div
          v-if="typingSeat"
          class="studio__typing"
          :style="{ '--seat-color': typingSeat.color }"
        >
          <span class="studio__typing-dot" aria-hidden="true" />
          <span class="studio__typing-name">{{ typingSeat.expert_name }}</span>
          <span class="studio__typing-text">{{ typingSeat.label || '正在发言' }}…</span>
        </div>

        <div
          ref="stageRef"
          class="studio__stage-body scroll-area"
          @scroll.passive="onScroll"
        >
          <SpeakerBubble
            v-for="item in items"
            :key="item.id"
            :message="item"
            :text="item.shown"
            :streaming="item.streaming"
            :is-host="item.expert_id === hostId"
          />

          <!-- 占位卡片：发言完成后被正式气泡替换 -->
          <TypingBubble
            v-if="typingSeat"
            :typing="typingSeat"
            :color="typingSeat.color"
            :role="typingSeat.role"
            :is-host="typingSeat.isHost"
          />

          <el-empty
            v-if="!items.length && !typingSeat"
            description="等待主持人开场…"
            :image-size="72"
            class="studio__empty"
          />
        </div>

        <transition name="fade">
          <el-button
            v-if="unseen > 0"
            class="studio__latest"
            type="primary"
            size="small"
            round
            @click="scrollToBottom(true)"
          >
            回到最新（{{ unseen }} 条）
          </el-button>
        </transition>
      </section>

      <StatusWindow
        class="studio__status"
        :experts="panelists"
        :phase="phase"
        :finished="finished || isReplay"
      />

      <div class="studio__insights">
        <ConsensusPanel
          class="studio__consensus"
          :agree="agree"
          :disagree="disagree"
          :experts="experts"
        />
        <SummaryPanel v-if="summary" class="studio__summary" :summary="summary" />
      </div>
    </main>
  </div>
</template>

<style scoped>
.studio {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 18px 16px;
  overflow: hidden; /* 整页不滚动 */
}

/* ---------------- 顶部：返回 + 议题 + 控制条 ---------------- */
.studio__head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.studio__back {
  flex: none;
  color: var(--text-1) !important;
}

.studio__title {
  flex: 1 1 auto;
  min-width: 0;
}

.studio__topic {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.studio__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--text-2);
}

.studio__controls {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
}

.studio__phase {
  flex: none;
}

/* ---------------- 三块区域：超宽三列 ---------------- */
.studio__grid {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(0, 1fr) 300px 400px;
  grid-template-areas: 'stage status insights';
}

.studio__stage {
  grid-area: stage;
  position: relative;
  min-height: 0;
  min-width: 0;
}

.studio__status {
  grid-area: status;
  min-width: 0;
}

.studio__insights {
  grid-area: insights;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.studio__consensus {
  flex: 1 1 auto;
  min-height: 0;
}

/* 有总结时，共识区让出一部分高度给总结卡片（各自独立滚动） */
.studio__summary {
  flex: 0 1 auto;
  max-height: 46%;
}

.studio__alert {
  flex: none;
}

/* 「本次生成降级」提示条：与错误提示同款宽度，但用不同的语义色 */
.studio__degraded {
  flex: none;
}

/* 主发言区：容器内独立滚动 */
.studio__stage-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* 「正在发言」提示条：贴在发言区顶部，不占滚动内容 */
.studio__typing {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 12px;
  padding: 6px 10px;
  border-radius: 10px;
  font-size: 13px;
  color: var(--text-1);
  background: color-mix(in srgb, var(--seat-color) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--seat-color) 34%, transparent);
}

.studio__typing-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--seat-color);
  animation: typing-pulse 1.2s ease-in-out infinite;
}

.studio__typing-name {
  font-weight: 700;
  color: var(--seat-color);
}

.studio__typing-text {
  color: var(--text-1);
}

@keyframes typing-pulse {
  0%,
  100% {
    opacity: 0.3;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1.1);
  }
}

.studio__empty {
  margin: auto 0;
}

.studio__latest {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: 3;
  box-shadow: var(--shadow-1);
}

/* ---------------- 普通屏：两列，右列上下分栏 ---------------- */
@media (max-width: 1599px) {
  .studio__grid {
    grid-template-columns: minmax(0, 1fr) 320px;
    grid-template-rows: minmax(0, 42fr) minmax(0, 58fr);
    grid-template-areas:
      'stage status'
      'stage insights';
  }
}

/* ---------------- 窄屏：单列，状态折叠为横条 ---------------- */
@media (max-width: 1023px) {
  .studio {
    padding: 8px;
    gap: 6px;
  }

  .studio__head {
    flex-wrap: wrap;
    row-gap: 4px;
    gap: 6px;
  }

  .studio__title {
    order: 3;
    flex-basis: 100%;
  }

  .studio__topic {
    font-size: 17px;
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 1;
    line-clamp: 1;
    -webkit-box-orient: vertical;
  }

  /* 窄屏节省高度：席位/发言数已在列表与阶段条体现 */
  .studio__meta {
    display: none;
  }

  .studio__controls {
    margin-left: auto;
  }

  .studio__grid {
    grid-template-columns: minmax(0, 1fr);
    /* 保证主发言区至少 150px 高，其余区域各自内部滚动 */
    grid-template-rows: auto minmax(150px, 1fr) minmax(104px, 22vh);
    grid-template-areas:
      'status'
      'stage'
      'insights';
    gap: 6px;
  }

  .studio__stage-body {
    padding: 8px 10px;
    gap: 8px;
  }
}

@media (max-width: 420px) {
  .studio {
    padding: 6px;
  }

  .studio__grid {
    grid-template-rows: auto minmax(140px, 1fr) minmax(96px, 20vh);
  }
}

/* 空态 */
.studio--empty {
  align-items: center;
  justify-content: center;
}

/* "回到最新"按钮淡入 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translate(-50%, 6px);
}
</style>
