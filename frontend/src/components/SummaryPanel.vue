<script setup>
/**
 * 主持人总结卡片：只渲染后端 `SummaryOut.sections` 的「标题 + 正文」。
 *
 * `heading` 被后端限制为「共识 / 分歧 / 结论建议」三值（`Literal`），
 * 因此这里不需要解析自由文本；`degraded=true` 表示模型输出不合格时的兜底文案。
 */
import { computed } from 'vue'

const props = defineProps({
  /** SummaryOut（`docs/API.md` §3.5），未结束时为 null */
  summary: { type: Object, default: null },
})

const sections = computed(() => props.summary?.sections ?? [])
const degraded = computed(() => Boolean(props.summary?.degraded))
</script>

<template>
  <section class="summary panel">
    <header class="panel__head">
      <span class="panel__title">主持人总结</span>
      <el-tag v-if="degraded" size="small" type="warning" effect="plain">兜底文案</el-tag>
      <span v-else class="panel__hint">{{ sections.length }} 段</span>
    </header>

    <div class="summary__body scroll-area">
      <article v-for="section in sections" :key="section.heading" class="summary__section">
        <h3 class="summary__heading">{{ section.heading }}</h3>
        <p class="summary__text">{{ section.body }}</p>
      </article>

      <p v-if="!sections.length" class="summary__empty">讨论结束后，主持人会在这里给出总结。</p>
    </div>
  </section>
</template>

<style scoped>
.summary {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.summary__body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  overflow-y: auto;
}

.summary__section {
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.022);
}

.summary__heading {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--accent);
  margin-bottom: 4px;
}

.summary__text {
  font-size: 12px;
  line-height: 1.68;
  color: var(--text-0);
  word-break: break-word;
}

.summary__empty {
  font-size: 12px;
  color: var(--text-2);
  padding: 4px 2px;
}
</style>
