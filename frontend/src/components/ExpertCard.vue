<script setup>
/**
 * 单个席位卡片：颜色标识 + 姓名 + 立场标签 + 职业/Title + 状态 + 发言次数 + 关注点。
 *
 * 本轮要求：**主持人与专家完全一致的结构与尺寸**（不再有「主持人一张大卡、专家一行备注」），
 * 状态文案统一为四档：聆听中 | 正在发言 | 已发言 | 已总结。
 * 只展示公开关注点（focus），不存在任何推理链字段（PRD R4 / AC-4.4）。
 */
import { computed } from 'vue'

import {
  FOCUS_PLACEHOLDER,
  SEAT_STATE,
  SEAT_STATE_LABEL,
  SEAT_STATE_TAG_TYPE,
  STANCE_TAG_TYPE,
  stanceTagLabel,
} from '@/constants'

const props = defineProps({
  /** ExpertOut + { state, focus, spoken } */
  expert: { type: Object, required: true },
  /** 整场是否已结束：结束后所有席位统一显示「已总结」 */
  finished: { type: Boolean, default: false },
  /** 主持台当前行为文案（与专家卡片同结构，只多一行说明） */
  hostBehavior: { type: String, default: '' },
})

const stateKey = computed(() => {
  if (props.finished) return SEAT_STATE.SUMMARIZED
  if (props.expert.state === 'speaking' || props.expert.state === 'preparing') {
    return SEAT_STATE.SPEAKING
  }
  return (props.expert.spoken ?? 0) > 0 ? SEAT_STATE.SPOKEN : SEAT_STATE.LISTENING
})
const stateLabel = computed(() => SEAT_STATE_LABEL[stateKey.value])
const stateType = computed(() => SEAT_STATE_TAG_TYPE[stateKey.value])
const spoken = computed(() => props.expert.spoken ?? 0)
const stance = computed(() => stanceTagLabel(props.expert.stance))
const stanceType = computed(() => STANCE_TAG_TYPE[stance.value] ?? 'info')
const focusText = computed(() => (props.expert.focus || '').trim() || FOCUS_PLACEHOLDER)
const hasFocus = computed(() => Boolean((props.expert.focus || '').trim()))
</script>

<template>
  <article
    class="expert-card"
    :class="{
      'is-speaking': stateKey === SEAT_STATE.SPEAKING,
      'is-host': expert.is_host,
      'is-summarized': stateKey === SEAT_STATE.SUMMARIZED,
    }"
    :style="{ '--seat-color': expert.color }"
  >
    <div class="expert-card__top">
      <span class="expert-card__dot" aria-hidden="true" />
      <span class="expert-card__name">{{ expert.name }}</span>
      <el-tag v-if="expert.is_host" size="small" effect="plain" class="expert-card__host">主持</el-tag>
      <el-tag size="small" effect="plain" :type="stanceType" class="expert-card__stance">
        {{ stance }}
      </el-tag>
      <el-tag
        size="small"
        effect="dark"
        :type="stateType"
        class="expert-card__state"
        :class="`is-${stateKey}`"
      >
        {{ stateLabel }}
      </el-tag>
    </div>

    <p class="expert-card__meta">
      <span class="expert-card__profession">{{ expert.profession }}</span>
      <span class="expert-card__title">{{ expert.title }}</span>
      <span class="expert-card__count" :title="`已发言 ${spoken} 次`">{{ spoken }} 次</span>
    </p>

    <p class="expert-card__focus" :class="{ 'is-empty': !hasFocus }">
      <span v-if="expert.is_host && hostBehavior" class="expert-card__behavior">{{ hostBehavior }} · </span>{{ focusText }}
    </p>
  </article>
</template>

<style scoped>
.expert-card {
  position: relative;
  padding: 10px 12px 10px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid rgba(255, 255, 255, 0.06);
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
  overflow: hidden;
}

/* 席位专属色：左侧竖条 */
.expert-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--seat-color);
  opacity: 0.85;
}

/* 主持人：与专家完全同一张卡、同一尺寸，只用底色与标签区分身份 */
.expert-card.is-host {
  background: color-mix(in srgb, #5b9dff 8%, transparent);
  border-color: color-mix(in srgb, #5b9dff 26%, transparent);
}

.expert-card.is-speaking {
  border-color: var(--seat-color);
  background: color-mix(in srgb, var(--seat-color) 12%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--seat-color) 40%, transparent),
    0 8px 22px color-mix(in srgb, var(--seat-color) 22%, transparent);
}

.expert-card.is-summarized {
  border-color: rgba(255, 255, 255, 0.08);
  opacity: 0.92;
}

.expert-card__top {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.expert-card__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--seat-color);
  box-shadow: 0 0 8px color-mix(in srgb, var(--seat-color) 70%, transparent);
}

.expert-card__name {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-0);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expert-card__host {
  --el-tag-bg-color: rgba(91, 157, 255, 0.16);
  --el-tag-border-color: rgba(91, 157, 255, 0.42);
  --el-tag-text-color: #9cc4ff;
  flex: none;
}

/* 已发言次数：让「谁一直没说话」一眼可见 */
.expert-card__count {
  margin-left: auto;
  flex: none;
  font-size: 12px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}

.expert-card__stance {
  flex: none;
  font-size: 12px;
}

.expert-card__state {
  flex: none;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.expert-card__state.is-speaking {
  animation: state-pulse 1.4s ease-in-out infinite;
}

@keyframes state-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(248, 113, 113, 0.55);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(248, 113, 113, 0);
  }
}

.expert-card__meta {
  margin-top: 4px;
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  font-size: 13px;
  color: var(--text-2);
}

.expert-card__profession,
.expert-card__title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expert-card__title {
  min-width: 0;
}

.expert-card__focus {
  margin-top: 6px;
  font-size: 14px;
  line-height: 1.55;
  color: var(--text-1);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 20px;
}

.expert-card__focus.is-empty {
  color: var(--text-2);
}

.expert-card__behavior {
  color: #9cc4ff;
}

/* 窄屏：状态条里的小窗更紧凑（仍保留姓名 / 职业·Title / 三态 / 关注点） */
@media (max-width: 1023px) {
  .expert-card {
    padding: 8px 10px 8px 12px;
  }

  .expert-card__focus {
    margin-top: 4px;
    -webkit-line-clamp: 1;
    line-clamp: 1;
  }
}
</style>
