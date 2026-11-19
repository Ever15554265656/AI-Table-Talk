<script setup>
/**
 * 共识与分歧面板：左侧「共识」、右侧「分歧」，两栏各自独立滚动。
 * 条目更新（同一 id）只做高亮，不重建节点（AC-5.7）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  agree: { type: Array, default: () => [] },
  disagree: { type: Array, default: () => [] },
  /** 用于把 expert_ids 还原成「姓名 + 专属色」小标 */
  experts: { type: Array, default: () => [] },
})

const expertById = computed(() => new Map(props.experts.map((item) => [item.id, item])))

function speakers(item) {
  return (item.expert_ids ?? []).map((id) => expertById.value.get(id)).filter(Boolean)
}

/* ---- 新增条目高亮 3 秒（AC-5.7） ---- */
const fresh = ref(new Set())
const known = new Set()
let freshTimer = null

function syncFresh() {
  const next = new Set(fresh.value)
  let changed = false
  for (const item of [...props.agree, ...props.disagree]) {
    if (!known.has(item.id)) {
      known.add(item.id)
      next.add(item.id)
      changed = true
    }
  }
  if (!changed) return
  fresh.value = next
  clearTimeout(freshTimer)
  freshTimer = setTimeout(() => {
    fresh.value = new Set()
  }, 3000)
}

watch(
  () => [...props.agree, ...props.disagree].map((item) => item.id).join('|'),
  syncFresh,
  { immediate: true },
)

onBeforeUnmount(() => clearTimeout(freshTimer))
</script>

<template>
  <section class="consensus panel">
    <header class="panel__head">
      <span class="panel__title">共识与分歧</span>
      <span class="panel__hint">{{ agree.length }} 条共识 · {{ disagree.length }} 条分歧</span>
    </header>

    <div class="consensus__cols">
      <div class="consensus__col">
        <h3 class="consensus__label is-agree">共识</h3>
        <div class="consensus__list scroll-area">
          <article
            v-for="item in agree"
            :key="item.id"
            class="insight is-agree"
            :class="{ 'is-fresh': fresh.has(item.id) }"
          >
            <p class="insight__text">{{ item.content }}</p>
            <div v-if="speakers(item).length" class="insight__people">
              <span v-for="person in speakers(item)" :key="person.id" class="person">
                <i class="person__dot" :style="{ background: person.color }" aria-hidden="true" />
                {{ person.name }}
              </span>
            </div>
          </article>
          <p v-if="!agree.length" class="consensus__empty">讨论刚开始，暂无共识</p>
        </div>
      </div>

      <div class="consensus__col">
        <h3 class="consensus__label is-disagree">分歧</h3>
        <div class="consensus__list scroll-area">
          <article
            v-for="item in disagree"
            :key="item.id"
            class="insight is-disagree"
            :class="{ 'is-fresh': fresh.has(item.id) }"
          >
            <p class="insight__text">{{ item.content }}</p>
            <div v-if="speakers(item).length" class="insight__people">
              <span v-for="person in speakers(item)" :key="person.id" class="person">
                <i class="person__dot" :style="{ background: person.color }" aria-hidden="true" />
                {{ person.name }}
              </span>
            </div>
          </article>
          <p v-if="!disagree.length" class="consensus__empty">暂无分歧，双方还在试探</p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.consensus {
  min-height: 0;
}

.consensus__cols {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
  padding: 10px;
}

.consensus__col {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.consensus__label {
  flex: none;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.06em;
  padding-left: 8px;
  border-left: 3px solid transparent;
}

.consensus__label.is-agree {
  color: var(--agree);
  border-left-color: var(--agree);
}

.consensus__label.is-disagree {
  color: var(--disagree);
  border-left-color: var(--disagree);
}

.consensus__list {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-right: 4px;
}

.insight {
  padding: 8px 9px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.02);
  transition: background 0.4s ease, border-color 0.4s ease;
}

.insight.is-agree {
  background: var(--agree-soft);
  border-color: rgba(52, 211, 153, 0.22);
}

.insight.is-disagree {
  background: var(--disagree-soft);
  border-color: rgba(248, 113, 113, 0.22);
}

.insight.is-fresh {
  animation: fresh-glow 0.9s ease 2;
}

.insight__text {
  font-size: 14px;
  line-height: 1.62;
  color: var(--text-0);
  word-break: break-word;
}

.insight__people {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin-top: 6px;
}

.person {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: var(--text-1);
}

.person__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.consensus__empty {
  font-size: 14px;
  color: var(--text-2);
  padding: 6px 2px;
}

@keyframes fresh-glow {
  0% {
    box-shadow: 0 0 0 0 rgba(91, 157, 255, 0.5);
  }
  100% {
    box-shadow: 0 0 0 6px rgba(91, 157, 255, 0);
  }
}
</style>
