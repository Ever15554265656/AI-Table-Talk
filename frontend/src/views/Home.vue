<script setup>
/**
 * 首页：进行中 / 已结束讨论列表 + 发起新讨论表单 + 回看入口。
 *
 * 数据全部来自后端：
 *   GET  /api/sessions → 列表（含 expert_total / message_count）
 *   POST /api/sessions → 建会话 + 服务端生成阵容，成功后进入**阵容确认页**（PRD US-1）
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, VideoPlay, View } from '@element-plus/icons-vue'

import { createSession, listSessions } from '@/api/client'
import {
  DEFAULT_EXPERT_COUNT,
  EXPERT_COUNT_MAX,
  EXPERT_COUNT_MIN,
  QUICK_EXPERT_COUNTS,
  SESSION_STATUS_LABEL,
  SESSION_STATUS_TAG_TYPE,
  TOPIC_MAX_LEN,
} from '@/constants'
import { formatRelative } from '@/utils/format'

const router = useRouter()

const sessions = ref([])
const loading = ref(true)
const tab = ref('live')

const form = reactive({
  topic: '',
  expert_count: DEFAULT_EXPERT_COUNT,
})
const submitting = ref(false)

const liveList = computed(() => sessions.value.filter((item) => item.status !== 'ended'))
const endedList = computed(() => sessions.value.filter((item) => item.status === 'ended'))
const liveCount = computed(() => sessions.value.filter((item) => item.status === 'live').length)
const pendingCount = computed(() => sessions.value.filter((item) => item.status === 'created').length)
const visibleList = computed(() => (tab.value === 'live' ? liveList.value : endedList.value))
const totalSeats = computed(() => Number(form.expert_count) + 1)

async function refresh() {
  loading.value = true
  try {
    sessions.value = await listSessions({ limit: 50 })
  } catch (err) {
    ElMessage.error(err?.message || '加载讨论列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

/** 与后端 SessionCreate 一致的本地校验（AC-2.1 / AC-2.2）；服务端仍会再校验一次 */
function validate() {
  const topic = form.topic.trim()
  if (!topic) {
    ElMessage.error('议题不能为空')
    return null
  }
  if (topic.length > TOPIC_MAX_LEN) {
    ElMessage.error(`议题长度不能超过 ${TOPIC_MAX_LEN} 个字符`)
    return null
  }
  const count = Number(form.expert_count)
  if (!Number.isInteger(count) || count < EXPERT_COUNT_MIN || count > EXPERT_COUNT_MAX) {
    ElMessage.error(`专家人数需在 ${EXPERT_COUNT_MIN}–${EXPERT_COUNT_MAX} 之间`)
    return null
  }
  return { topic, count }
}

async function submit() {
  if (submitting.value) return
  const payload = validate()
  if (!payload) return

  submitting.value = true
  try {
    const created = await createSession({ topic: payload.topic, expert_count: payload.count })
    ElMessage.success(`已生成 ${created.expert_total} 个席位（含 1 位主持人），请确认阵容`)
    form.topic = ''
    form.expert_count = DEFAULT_EXPERT_COUNT
    // US-1：先停在确认页，确认后再开演（US-3）
    router.push({ name: 'lineup', params: { id: created.id } })
  } catch (err) {
    ElMessage.error(err?.message || '发起讨论失败')
  } finally {
    submitting.value = false
  }
}

function open(item) {
  // AC-1.5：ready(created) → 确认页；live → 演播厅旁听；ended → 只读回看
  router.push({
    name: item.status === 'created' ? 'lineup' : 'studio',
    params: { id: item.id },
    query: item.status === 'created' ? {} : { mode: item.status === 'ended' ? 'replay' : 'live' },
  })
}

function showReplay() {
  tab.value = 'ended'
}
</script>

<template>
  <div class="home">
    <header class="home__head">
      <div class="home__brand">
        <span class="home__logo" aria-hidden="true">◉</span>
        <div>
          <h1 class="home__title">AI 圆桌演播厅</h1>
          <p class="home__subtitle">输入议题，生成立场互斥的专家席，观看一场实时圆桌讨论</p>
        </div>
      </div>

      <div class="home__stats">
        <span class="stat"><b>{{ liveCount }}</b> 场直播中</span>
        <span class="stat"><b>{{ pendingCount }}</b> 场待开演</span>
        <span class="stat"><b>{{ endedList.length }}</b> 场已结束</span>
        <el-button size="small" :icon="View" @click="showReplay">回看入口</el-button>
      </div>
    </header>

    <main class="home__grid">
      <section class="panel home__list">
        <header class="panel__head">
          <el-radio-group v-model="tab" size="small">
            <el-radio-button value="live">进行中 · {{ liveList.length }}</el-radio-button>
            <el-radio-button value="ended">已结束 · {{ endedList.length }}</el-radio-button>
          </el-radio-group>
          <span class="panel__hint">点击卡片进入演播厅</span>
        </header>

        <div v-loading="loading" class="home__list-body scroll-area">
          <article
            v-for="item in visibleList"
            :key="item.id"
            class="session"
            :class="`is-${item.status}`"
            tabindex="0"
            @click="open(item)"
            @keydown.enter="open(item)"
          >
            <div class="session__main">
              <h2 class="session__topic">{{ item.topic }}</h2>
              <div class="session__meta">
                <el-tag size="small" effect="dark" :type="SESSION_STATUS_TAG_TYPE[item.status]">
                  {{ SESSION_STATUS_LABEL[item.status] }}
                </el-tag>
                <span>{{ item.expert_total }} 席（含主持）</span>
                <span class="dot">·</span>
                <span>{{ item.message_count }} 条发言</span>
                <span class="dot">·</span>
                <span>{{ formatRelative(item.created_at) }}</span>
              </div>
            </div>

            <el-button
              size="small"
              class="session__action"
              :type="item.status === 'ended' ? 'default' : 'primary'"
              :plain="item.status === 'ended'"
              :icon="item.status === 'ended' ? View : VideoPlay"
              @click.stop="open(item)"
            >
              {{ item.status === 'ended' ? '回看' : item.status === 'created' ? '开演' : '进入' }}
            </el-button>
          </article>

          <el-empty v-if="!visibleList.length" description="这里还没有讨论" :image-size="80" />
        </div>
      </section>

      <aside class="panel home__form">
        <header class="panel__head">
          <span class="panel__title">发起新讨论</span>
          <span class="panel__hint">服务端生成阵容与配色</span>
        </header>

        <div class="home__form-body scroll-area">
          <div class="field">
            <span class="field__label">议题</span>
            <el-input
              v-model="form.topic"
              type="textarea"
              :rows="4"
              resize="none"
              :maxlength="TOPIC_MAX_LEN"
              show-word-limit
              placeholder="例如：远程办公是否降低了团队的创新力？"
              @keydown.ctrl.enter="submit"
            />
          </div>

          <div class="field">
            <span class="field__label">专家人数</span>
            <div class="field__row">
              <el-input-number
                v-model="form.expert_count"
                :min="EXPERT_COUNT_MIN"
                :max="EXPERT_COUNT_MAX"
                :step="1"
                step-strictly
                controls-position="right"
              />
              <div class="quick">
                <el-button
                  v-for="count in QUICK_EXPERT_COUNTS"
                  :key="count"
                  size="small"
                  plain
                  :type="form.expert_count === count ? 'primary' : 'default'"
                  @click="form.expert_count = count"
                >
                  {{ count }} 人
                </el-button>
              </div>
            </div>
            <p class="field__tip">
              共 {{ totalSeats }} 个席位：1 位主持人 + {{ form.expert_count }} 位专家
            </p>
          </div>

          <el-button type="primary" class="home__submit" :loading="submitting" :icon="Plus" @click="submit">
            发起讨论
          </el-button>
          <p class="home__note">发起后先生成阵容，确认阵容后进入演播厅开演。</p>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.home {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 24px 22px;
  overflow: hidden; /* 整页不滚动，滚动只发生在列表/表单容器内 */
}

/* ---------------- 顶栏 ---------------- */
.home__head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.home__brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.home__logo {
  font-size: 26px;
  line-height: 1;
  color: var(--accent);
  text-shadow: 0 0 18px rgba(91, 157, 255, 0.65);
}

.home__title {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.home__subtitle {
  margin-top: 2px;
  font-size: 13.5px;
  color: var(--text-2);
}

.home__stats {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 13.5px;
  color: var(--text-1);
}

.stat b {
  margin-right: 2px;
  font-size: 17px;
  color: var(--text-0);
}

/* ---------------- 主体：列表 + 表单 ---------------- */
.home__grid {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(0, 1fr) 380px;
}

.home__list {
  min-height: 0;
}

.home__list-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  overflow-y: auto;
}

.session {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid var(--line-soft);
  background: rgba(255, 255, 255, 0.022);
  cursor: pointer;
  transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease;
}

.session:hover,
.session:focus-visible {
  transform: translateY(-1px);
  border-color: rgba(91, 157, 255, 0.42);
  outline: none;
}

.session.is-live {
  border-color: rgba(248, 113, 113, 0.26);
  background: linear-gradient(90deg, rgba(248, 113, 113, 0.1), rgba(255, 255, 255, 0.02) 58%);
}

.session.is-created {
  cursor: default;
  opacity: 0.78;
}

.session.is-created:hover {
  transform: none;
  border-color: var(--line-soft);
}

.session__main {
  flex: 1 1 auto;
  min-width: 0;
}

.session__topic {
  font-size: 17px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
  font-size: 13.5px;
  color: var(--text-2);
}

.session__action {
  flex: none;
}

.dot {
  color: var(--text-2);
}

/* ---------------- 表单 ---------------- */
.home__form {
  min-height: 0;
}

.home__form-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  overflow-y: auto;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field__label {
  font-size: 13.5px;
  letter-spacing: 0.04em;
  color: var(--text-1);
}

.field__row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.quick {
  display: flex;
  gap: 6px;
}

.field__tip {
  font-size: 13.5px;
  color: var(--text-2);
}

.home__submit {
  width: 100%;
}

.home__note {
  font-size: 13.5px;
  text-align: center;
  color: var(--text-2);
}

/* ---------------- 窄屏：列表在上、表单在下（各自可滚） ---------------- */
@media (max-width: 1023px) {
  .home {
    padding: 12px;
    gap: 10px;
  }

  .home__grid {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) auto;
    gap: 10px;
  }

  .home__list {
    order: 1;
  }

  .home__form {
    order: 2;
  }

  .home__form-body {
    max-height: 38vh;
    gap: 12px;
    padding: 12px;
  }

  .home__title {
    font-size: 21px;
  }

  .home__subtitle {
    display: none;
  }

  .home__stats {
    width: 100%;
    justify-content: space-between;
  }
}

@media (max-width: 480px) {
  .home {
    padding: 8px;
  }

  .home__form-body {
    max-height: 33vh;
  }

  .session {
    padding: 10px 12px;
    gap: 8px;
  }

  .session__topic {
    font-size: 16px;
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
}
</style>


