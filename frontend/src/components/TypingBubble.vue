<script setup>
/**
 * 「正在发言」占位卡片。
 *
 * 后端在生成本条发言前会先推一个 `typing` 事件（MOCK 模式下还会停留 1~2 秒），
 * 这里用一张和正式气泡同尺寸的占位卡顶上：观众先看到「谁在说」，正文到了再替换成气泡。
 * 回看模式不会渲染本组件（由 Studio 判断）。
 */
const props = defineProps({
  /** typing 事件 payload：{ expert_id, expert_name, label, round } */
  typing: { type: Object, required: true },
  /** 席位颜色（与正式气泡一致，便于认出是谁） */
  color: { type: String, default: '#60A5FA' },
  /** 职业 · Title（可选，来自 experts 列表） */
  role: { type: String, default: '' },
  /** 是否为主持人（视觉上稍微区分，尺寸与专家完全一致） */
  isHost: { type: Boolean, default: false },
})
</script>

<template>
  <article
    class="typing-bubble"
    :class="{ 'is-host': isHost }"
    :style="{ '--seat-color': color }"
  >
    <span class="typing-bubble__bar" aria-hidden="true" />
    <div class="typing-bubble__main">
      <header class="typing-bubble__head">
        <span class="typing-bubble__name">{{ typing.expert_name }}</span>
        <span v-if="role" class="typing-bubble__role">{{ role }}</span>
      </header>
      <p class="typing-bubble__text">
        <span class="typing-bubble__label">{{ typing.label || '正在发言' }}</span>
        <span class="typing-bubble__dots" aria-hidden="true">
          <i /><i /><i />
        </span>
      </p>
    </div>
  </article>
</template>

<style scoped>
.typing-bubble {
  display: flex;
  flex: none;
  gap: 12px;
  padding: 12px 14px 12px 0;
  border-radius: 14px;
  background: color-mix(in srgb, var(--seat-color) 8%, transparent);
  border: 1px dashed color-mix(in srgb, var(--seat-color) 46%, transparent);
  border-left: none;
  animation: typing-in 0.2s ease both;
}

.typing-bubble.is-host {
  background: linear-gradient(90deg, rgba(91, 157, 255, 0.14), rgba(91, 157, 255, 0.03));
  border-color: rgba(91, 157, 255, 0.42);
}

.typing-bubble__bar {
  flex: none;
  width: 4px;
  align-self: stretch;
  margin-left: 2px;
  border-radius: 0 4px 4px 0;
  background: var(--seat-color);
  opacity: 0.7;
  animation: bar-pulse 1.4s ease-in-out infinite;
}

.typing-bubble__main {
  min-width: 0;
  padding-left: 12px;
  padding-right: 4px;
}

.typing-bubble__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.typing-bubble__name {
  font-weight: 700;
  font-size: 16px;
  color: var(--seat-color);
}

.typing-bubble__role {
  font-size: 13px;
  color: var(--text-2);
}

.typing-bubble__text {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  color: var(--text-1);
}

.typing-bubble__label {
  letter-spacing: 0.02em;
}

.typing-bubble__dots {
  display: inline-flex;
  gap: 4px;
}

.typing-bubble__dots i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--seat-color);
  animation: dot-blink 1.2s ease-in-out infinite;
}

.typing-bubble__dots i:nth-child(2) {
  animation-delay: 0.18s;
}

.typing-bubble__dots i:nth-child(3) {
  animation-delay: 0.36s;
}

@keyframes dot-blink {
  0%,
  100% {
    opacity: 0.2;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

@keyframes bar-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 0.95;
  }
}

@keyframes typing-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
