<script setup>
/**
 * 阵容确认页（PRD R2 / US-1~US-3 / AC-2.7）。
 *
 * 数据：`GET /api/sessions/{id}`（`SessionDetail.experts`，主持人 + 专家，颜色由后端分配）。
 * 行为：确认后进入演播厅；演播厅一连 SSE，后端 `start_discussion` 即开演（`created → live`）。
 * 边界：后端暂未提供 `regenerate` / `confirm` 端点，因此本页只做**前端确认门**，
 *       不伪造「重新生成」；刷新 / 已开演的场次直接跳演播厅，避免重复确认。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, VideoPlay } from '@element-plus/icons-vue'

import { getSession } from '@/api/client'
import { SESSION_STATUS_LABEL, SESSION_STATUS_TAG_TYPE } from '@/constants'

const route = useRoute()
const router = useRouter()

const session = ref(null)
const loading = ref(true)
const error = ref('')
const entering = ref(false)

const host = computed(() => session.value?.experts.find((item) => item.is_host) ?? null)
const guests = computed(() => session.value?.experts.filter((item) => !item.is_host) ?? [])
const statusLabel = computed(() => SESSION_STATUS_LABEL[session.value?.status] ?? '')
const statusType = computed(() => SESSION_STATUS_TAG_TYPE[session.value?.status] ?? 'info')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const detail = await getSession(route.params.id)
    session.value = detail
    // 已开演 / 已结束：确认页无意义，直接进演播厅（直播或只读回看）
    if (detail.status !== 'created') {
      router.replace({
        name: 'studio',
        params: { id: detail.id },
        query: { mode: detail.status === 'ended' ? 'replay' : 'live' },
      })
    }
  } catch (err) {
    error.value = err?.message || '加载阵容失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

function confirmLineup() {
  if (!session.value || entering.value) return
  entering.value = true
  ElMessage.success('阵容已确认，正在进入演播厅')
  router.push({ name: 'studio', params: { id: session.value.id }, query: { mode: 'live' } })
}
</script>

<template>
  <div v-if="loading" class="lineup lineup--empty">
    <el-skeleton :rows="5" animated style="width: min(720px, 92%)" />
  </div>

  <div v-else-if="!session" class="lineup lineup--empty">
    <el-empty :description="error || '未找到该讨论，可能已过期或链接有误'">
      <el-button type="primary" @click="router.push('/')">返回首页</el-button>
      <el-button @click="load()">重试</el-button>
    </el-empty>
  </div>

  <div v-else class="lineup">
    <header class="lineup__head">
      <el-button text :icon="ArrowLeft" class="lineup__back" @click="router.push('/')">返回首页</el-button>
      <div class="lineup__title">
        <h1 class="lineup__heading">阵容确认</h1>
        <p class="lineup__topic" :title="session.topic">{{ session.topic }}</p>
      </div>
      <el-tag size="small" effect="dark" :type="statusType">{{ statusLabel }}</el-tag>
    </header>

    <main class="lineup__body scroll-area">
      <section class="lineup__host panel">
        <header class="panel__head">
          <span class="panel__title">主持台</span>
          <span class="panel__hint">确认后由主持人开场</span>
        </header>
        <article v-if="host" class="seat is-host" :style="{ '--seat-color': host.color }">
          <span class="seat__bar" aria-hidden="true" />
          <div class="seat__main">
            <div class="seat__top">
              <span class="seat__name">{{ host.name }}</span>
              <el-tag size="small" effect="plain" class="seat__tag">主持人</el-tag>
            </div>
            <p class="seat__meta">{{ host.profession }} · {{ host.title }}</p>
            <p class="seat__stance">{{ host.stance }}</p>
          </div>
        </article>
      </section>

      <section class="lineup__guests panel">
        <header class="panel__head">
          <span class="panel__title">专家席</span>
          <span class="panel__hint">
            共 {{ session.experts.length }} 个席位 · 1 位主持人 + {{ guests.length }} 位专家
          </span>
        </header>
        <div class="lineup__grid">
          <article
            v-for="(expert, index) in guests"
            :key="expert.id"
            class="seat"
            :style="{ '--seat-color': expert.color }"
          >
            <span class="seat__bar" aria-hidden="true" />
            <div class="seat__main">
              <div class="seat__top">
                <span class="seat__name">{{ expert.name }}</span>
                <span class="seat__index">席 {{ index + 1 }}</span>
              </div>
              <p class="seat__meta">{{ expert.profession }} · {{ expert.title }}</p>
              <p class="seat__stance">{{ expert.stance }}</p>
            </div>
          </article>
        </div>
      </section>
    </main>

    <footer class="lineup__foot">
      <p class="lineup__note">
        确认后立即开演；席位配色由服务端分配，讨论内容实时生成。
      </p>
      <div class="lineup__actions">
        <el-button @click="router.push('/')">返回首页</el-button>
        <el-button
          type="primary"
          :icon="VideoPlay"
          :loading="entering"
          class="lineup__confirm"
          @click="confirmLineup"
        >
          确认阵容并进入演播厅
        </el-button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.lineup {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 22px 20px;
  overflow: hidden; /* 整页不滚动，滚动只发生在 .lineup__body */
}

.lineup--empty {
  align-items: center;
  justify-content: center;
}

/* ---------------- 顶部 ---------------- */
.lineup__head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.lineup__back {
  flex: none;
  color: var(--text-1) !important;
}

.lineup__title {
  flex: 1 1 auto;
  min-width: 0;
}

.lineup__heading {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.lineup__topic {
  margin-top: 2px;
  font-size: 15px;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ---------------- 主体：主持台 + 专家席 ---------------- */
.lineup__body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.lineup__host,
.lineup__guests {
  flex: none;
}

.lineup__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
  padding: 12px;
}

.seat {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 12px 14px 12px 16px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.024);
  border: 1px solid var(--line-soft);
  overflow: hidden;
}

.seat.is-host {
  margin: 0 12px 12px;
  background: rgba(91, 157, 255, 0.08);
  border-color: rgba(91, 157, 255, 0.26);
}

.seat__bar {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--seat-color);
  opacity: 0.9;
}

.seat__main {
  min-width: 0;
  flex: 1 1 auto;
}

.seat__top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.seat__name {
  font-size: 17px;
  font-weight: 650;
  color: var(--text-0);
}

.seat__index {
  font-size: 13.5px;
  color: var(--text-2);
}

.seat__meta {
  margin-top: 4px;
  font-size: 13.5px;
  color: var(--text-1);
}

.seat__stance {
  margin-top: 8px;
  font-size: 15px;
  line-height: 1.55;
  color: var(--text-2);
}

/* ---------------- 底部操作 ---------------- */
.lineup__foot {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.lineup__note {
  font-size: 13.5px;
  color: var(--text-2);
}

.lineup__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

@media (max-width: 720px) {
  .lineup {
    padding: 12px;
    gap: 10px;
  }

  .lineup__grid {
    grid-template-columns: minmax(0, 1fr);
    padding: 10px;
  }

  .lineup__foot {
    flex-direction: column;
    align-items: stretch;
  }

  .lineup__actions {
    justify-content: flex-end;
  }
}
</style>

