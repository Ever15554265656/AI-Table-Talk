<script setup>
/**
 * 单条发言气泡。
 * 只渲染「可见发言」：颜色条 + 姓名 + 职业/Title + 正文。
 * 内部事件（举手 / 抢答 / 调度 / 状态）一律不上屏（PRD R6 / AC-6.2）。
 */
const props = defineProps({
  /** MessageOut + shown/streaming（打字机中间态） */
  message: { type: Object, required: true },
  /** 当前可见文本；流式中是前缀，结束后等于 content */
  text: { type: String, default: '' },
  /** 是否仍在打字机输出中（显示光标） */
  streaming: { type: Boolean, default: false },
  /** 是否为主持人发言（视觉区分，AC-6.9） */
  isHost: { type: Boolean, default: false },
})
</script>

<template>
  <article
    class="bubble"
    :class="{ 'is-host': isHost, 'is-streaming': streaming }"
    :style="{ '--speaker-color': message.expert_color }"
  >
    <span class="bubble__bar" aria-hidden="true" />
    <div class="bubble__main">
      <header class="bubble__head">
        <span class="bubble__name">{{ message.expert_name }}</span>
        <span class="bubble__role">{{ message.expert_profession }} · {{ message.expert_title }}</span>
        <el-tag v-if="isHost" size="small" effect="plain" class="bubble__host">主持人</el-tag>
      </header>
      <p class="bubble__text">
        {{ text || message.content
        }}<span v-if="streaming" class="bubble__caret" aria-hidden="true" />
      </p>
    </div>
  </article>
</template>

<style scoped>
.bubble {
  display: flex;
  flex: none; /* 容器变矮时不允许压扁气泡（气泡高度由内容决定） */
  gap: 12px;
  padding: 12px 14px 12px 0;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.024);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-left: none;
  transition: background 0.2s ease;
  animation: bubble-in 0.28s ease both;
}

.bubble:hover {
  background: rgba(255, 255, 255, 0.045);
}

.bubble.is-host {
  background: linear-gradient(90deg, rgba(91, 157, 255, 0.14), rgba(91, 157, 255, 0.03));
  border-color: rgba(91, 157, 255, 0.28);
}

.bubble.is-streaming {
  border-color: color-mix(in srgb, var(--speaker-color) 62%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--speaker-color) 30%, transparent);
}

/* 发言人专属色：竖条（视觉主标识，颜色严格来自后端 expert_color） */
.bubble__bar {
  flex: none;
  width: 4px;
  align-self: stretch;
  margin-left: 2px;
  border-radius: 0 4px 4px 0;
  background: var(--speaker-color);
  box-shadow: 0 0 12px color-mix(in srgb, var(--speaker-color) 55%, transparent);
}

.bubble__main {
  min-width: 0;
  padding-left: 12px;
  padding-right: 4px;
}

.bubble__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 4px;
}

.bubble__name {
  font-weight: 700;
  font-size: 14px;
  color: var(--speaker-color);
  letter-spacing: 0.01em;
}

.bubble__role {
  font-size: 12px;
  color: var(--text-2);
}

.bubble__host {
  --el-tag-bg-color: rgba(91, 157, 255, 0.16);
  --el-tag-border-color: rgba(91, 157, 255, 0.42);
  --el-tag-text-color: #9cc4ff;
}

.bubble__text {
  font-size: 14px;
  line-height: 1.75;
  color: var(--text-0);
  word-break: break-word;
  white-space: pre-wrap;
}

.bubble__caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: -0.16em;
  background: var(--speaker-color);
  animation: caret-blink 0.9s steps(2, start) infinite;
}

@keyframes caret-blink {
  to {
    opacity: 0;
  }
}

@keyframes bubble-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
