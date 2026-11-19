<script setup>
/**
 * 单个专家小窗：颜色标识 + 姓名 + 职业/Title + 三态 + 当前关注点。
 * 只展示公开关注点（focus），不存在任何推理链字段（PRD R4 / AC-4.4）。
 */
import { computed } from 'vue'

import { FOCUS_PLACEHOLDER, STATE_LABEL, STATE_TAG_TYPE } from '@/constants'

const props = defineProps({
  /** ExpertOut + { state, focus } */
  expert: { type: Object, required: true },
  /** 主持台当前行为文案（仅主持人卡片展示） */
  hostBehavior: { type: String, default: '' },
})

const stateLabel = computed(() => STATE_LABEL[props.expert.state] ?? STATE_LABEL.idle)
const stateType = computed(() => STATE_TAG_TYPE[props.expert.state] ?? STATE_TAG_TYPE.idle)
const focusText = computed(() => (props.expert.focus || '').trim() || FOCUS_PLACEHOLDER)
const hasFocus = computed(() => Boolean((props.expert.focus || '').trim()))
</script>

<template>
  <article
    class="expert-card"
    :class="{
      'is-speaking': expert.state === 'speaking',
      'is-preparing': expert.state === 'preparing',
      'is-host': expert.is_host,
    }"
    :style="{ '--seat-color': expert.color }"
  >
    <div class="expert-card__top">
      <span class="expert-card__dot" aria-hidden="true" />
      <span class="expert-card__name">{{ expert.name }}</span>
      <el-tag v-if="expert.is_host" size="small" effect="plain" class="expert-card__host">主持台</el-tag>
      <el-tag size="small" effect="dark" :type="stateType" class="expert-card__state">{{ stateLabel }}</el-tag>
    </div>

    <p class="expert-card__meta">{{ expert.profession }} · {{ expert.title }}</p>

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

.expert-card.is-host {
  background: rgba(91, 157, 255, 0.08);
  border-color: rgba(91, 157, 255, 0.24);
}

.expert-card.is-preparing {
  border-color: rgba(250, 204, 21, 0.45);
}

.expert-card.is-speaking {
  border-color: var(--seat-color);
  background: color-mix(in srgb, var(--seat-color) 12%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--seat-color) 40%, transparent),
    0 8px 22px color-mix(in srgb, var(--seat-color) 22%, transparent);
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
  font-size: 13px;
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

.expert-card__state {
  margin-left: auto;
  flex: none;
}

.expert-card__meta {
  margin-top: 3px;
  font-size: 11px;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expert-card__focus {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-1);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 18px;
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
