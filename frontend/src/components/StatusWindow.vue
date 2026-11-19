<script setup>
/**
 * 右侧席位状态区：**主持人与专家同一套卡片、同一尺寸**，按席位顺序排列。
 * 每一张卡片都来自同一份 panelists 状态树，四档状态（聆听中 / 正在发言 / 已发言 / 已总结）
 * 由 ExpertCard 统一推导，这里只负责排序与整体提示。
 */
import { computed } from 'vue'

import ExpertCard from '@/components/ExpertCard.vue'
import { HOST_BEHAVIOR_LABEL } from '@/constants'

const props = defineProps({
  /** ExpertOut + { state, focus } 的数组（主持人也在其中） */
  experts: { type: Array, default: () => [] },
  /** 当前阶段，用于推导主持台行为标签（AC-4.8） */
  phase: { type: String, default: 'opening' },
  /** 整场是否已结束（结束后全员显示「已总结」） */
  finished: { type: Boolean, default: false },
})

const host = computed(() => props.experts.find((item) => item.is_host) ?? null)
const guests = computed(() => props.experts.filter((item) => !item.is_host))
const hostBehavior = computed(() => HOST_BEHAVIOR_LABEL[props.phase] ?? '')
const hint = computed(() => {
  if (props.finished) return '讨论已结束'
  const speaking = props.experts.find((item) => item.state === 'speaking')
  if (speaking) return `${speaking.name} 正在发言`
  const preparing = props.experts.find((item) => item.state === 'preparing')
  if (preparing) return `${preparing.name} 正在组织语言…`
  return '全员聆听中'
})
</script>

<template>
  <section class="status-window panel">
    <header class="panel__head">
      <span class="panel__title">专家状态</span>
      <span class="panel__hint">{{ hint }}</span>
    </header>

    <div class="status-window__body scroll-area">
      <ExpertCard
        v-if="host"
        :expert="host"
        :finished="finished"
        :host-behavior="hostBehavior"
      />
      <ExpertCard
        v-for="expert in guests"
        :key="expert.id"
        :expert="expert"
        :finished="finished"
      />
    </div>
  </section>
</template>

<style scoped>
.status-window {
  min-height: 0;
}

.status-window__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  /* 关键：容器高度受限时，隐式行必须按内容高度排版，
     否则 Chrome 会把 auto 行压到 22px，卡片互相重叠（overflow:hidden 让最小贡献为 0） */
  grid-auto-rows: max-content;
  align-content: start;
  gap: 8px;
  padding: 10px;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

/* 窄屏：折叠为横向滚动的状态条（PRD R4.6 / AC-4.7） */
@media (max-width: 1023px) {
  .status-window__body {
    grid-auto-flow: column;
    grid-auto-columns: minmax(210px, 1fr);
    grid-template-columns: none;
    overflow-x: auto;
    overflow-y: hidden;
    padding: 8px 10px;
  }
}
</style>
