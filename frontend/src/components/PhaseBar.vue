<script setup>
/**
 * 顶部阶段指示条：开场 / 自由讨论 / 收尾 / 已结束 + 轮次进度 + 会话状态。
 * 阶段 key 与 SSE phase 事件一致：opening / debate / wrap_up（ended 为终态）。
 */
import { computed } from 'vue'

import {
  PHASES,
  SESSION_STATUS_LABEL,
  SESSION_STATUS_TAG_TYPE,
  phaseIndex,
} from '@/constants'

const props = defineProps({
  phase: { type: String, default: 'opening' },
  round: { type: Number, default: 0 },
  maxRounds: { type: Number, default: 20 },
  status: { type: String, default: 'live' },
})

const active = computed(() => phaseIndex(props.phase))

function stepStatus(index) {
  if (props.phase === 'ended') return 'success'
  if (index < active.value) return 'success'
  if (index === active.value) return 'process'
  return 'wait'
}

const statusLabel = computed(() => SESSION_STATUS_LABEL[props.status] ?? props.status)
const statusType = computed(() => SESSION_STATUS_TAG_TYPE[props.status] ?? 'info')

/** 已结束判定：phase 或 session 任一为 ended 都算（回看不发 phase 事件） */
const ended = computed(() => props.phase === 'ended' || props.status === 'ended')

/**
 * 真实进度（不是装饰条）：
 * - 开场 6%；
 * - 交锋阶段按 `round / maxRounds` 线性推进到 94%；
 * - 收尾固定 96%，结束后 100%。
 * `round` 由后端 `total_rounds` 与已播发言条数共同决定（见 useDiscussionStream）。
 */
const progress = computed(() => {
  if (ended.value) return 100
  const total = Math.max(1, props.maxRounds)
  const ratio = Math.min(1, Math.max(0, props.round / total))
  if (props.phase === 'wrap_up') return 96
  if (props.phase === 'opening') return 6
  return Math.min(94, Math.round(6 + 88 * ratio))
})

const progressText = computed(() => `${props.round} / ${Math.max(1, props.maxRounds)} 轮`)
</script>

<template>
  <div class="phase-bar">
    <el-steps :active="active" align-center finish-status="success" class="phase-bar__steps">
      <el-step
        v-for="(item, index) in PHASES"
        :key="item.key"
        :title="item.label"
        :status="stepStatus(index)"
      />
    </el-steps>

    <div class="phase-bar__meta">
      <el-tag size="small" effect="dark" :type="statusType">{{ statusLabel }}</el-tag>
      <span class="phase-bar__round">第 {{ progressText }}</span>
      <span
        class="phase-bar__track"
        role="progressbar"
        aria-label="讨论进度"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="progress"
      >
        <span class="phase-bar__fill" :style="{ width: `${progress}%` }" />
      </span>
      <span class="phase-bar__percent">{{ progress }}%</span>
    </div>
  </div>
</template>

<style scoped>
.phase-bar {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 10px 16px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(22, 33, 58, 0.9), rgba(17, 26, 46, 0.9));
  box-shadow: var(--shadow-2);
}

.phase-bar__steps {
  flex: 1 1 auto;
  min-width: 0;
}

.phase-bar__meta {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13.5px;
  color: var(--text-1);
}

.phase-bar__round {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  color: var(--text-0);
}

/* 真实进度条：桌面端一直可见（不再只是移动端的装饰） */
.phase-bar__track {
  display: block;
  width: 132px;
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  overflow: hidden;
}

.phase-bar__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--accent), #a78bfa);
  transition: width 0.45s ease;
}

.phase-bar__percent {
  min-width: 36px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
}

/* ---- Element Plus 深色对齐 ---- */
.phase-bar :deep(.el-step__title) {
  font-size: 13.5px;
  line-height: 1.45;
  color: var(--text-2);
}

.phase-bar :deep(.el-step__title.is-process) {
  color: var(--text-0);
  font-weight: 600;
}

.phase-bar :deep(.el-step__title.is-success) {
  color: var(--text-1);
}

.phase-bar :deep(.el-step__head.is-process) {
  color: var(--accent);
  border-color: var(--accent);
}

.phase-bar :deep(.el-step__head.is-wait) {
  color: var(--text-2);
  border-color: var(--line);
}

.phase-bar :deep(.el-step__head.is-success) {
  color: var(--agree);
  border-color: var(--agree);
}

.phase-bar :deep(.el-step__line) {
  background-color: var(--line);
  top: 11px;
}

.phase-bar :deep(.el-step__icon) {
  width: 26px;
  height: 26px;
  font-size: 12px;
}

@media (max-width: 1023px) {
  .phase-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
    padding: 6px 10px;
  }

  .phase-bar__meta {
    justify-content: space-between;
    gap: 8px;
    font-size: 12.5px;
  }

  .phase-bar__track {
    display: block;
    flex: 1 1 auto;
  }

  .phase-bar :deep(.el-step__title) {
    font-size: 12.5px;
  }

  .phase-bar :deep(.el-step__icon) {
    width: 18px;
    height: 18px;
    font-size: 10px;
  }
}
</style>
