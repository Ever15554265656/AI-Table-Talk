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
const progress = computed(() => {
  if (!props.maxRounds) return 0
  return Math.min(100, Math.round((props.round / props.maxRounds) * 100))
})
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
      <span class="phase-bar__round">第 {{ round }} / {{ maxRounds }} 轮</span>
      <span class="phase-bar__track" aria-hidden="true">
        <span class="phase-bar__fill" :style="{ width: `${progress}%` }" />
      </span>
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
  font-size: 12px;
  color: var(--text-1);
}

.phase-bar__round {
  white-space: nowrap;
}

.phase-bar__track {
  display: none;
  width: 96px;
  height: 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  overflow: hidden;
}

.phase-bar__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--accent), #a78bfa);
  transition: width 0.4s ease;
}

/* ---- Element Plus 深色对齐 ---- */
.phase-bar :deep(.el-step__title) {
  font-size: 12px;
  line-height: 1.4;
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
  width: 22px;
  height: 22px;
  font-size: 11px;
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
    font-size: 11px;
  }

  .phase-bar__track {
    display: block;
    flex: 1 1 auto;
  }

  .phase-bar :deep(.el-step__title) {
    font-size: 11px;
  }

  .phase-bar :deep(.el-step__icon) {
    width: 18px;
    height: 18px;
    font-size: 10px;
  }
}
</style>
