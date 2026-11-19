import { createRouter, createWebHistory } from 'vue-router'

import Home from '@/views/Home.vue'
import Lineup from '@/views/Lineup.vue'
import Studio from '@/views/Studio.vue'

const routes = [
  {
    path: '/',
    name: 'home',
    component: Home,
    meta: { title: 'AI 圆桌演播厅' },
  },
  {
    // 阵容确认页（PRD R2）：确认后才进入演播厅，SSE 开演由演播厅触发
    path: '/lineup/:id',
    name: 'lineup',
    component: Lineup,
    props: true,
    meta: { title: '阵容确认' },
  },
  {
    // 直播与回看共用同一个演播厅组件，由 route.query.mode 决定控制条形态
    path: '/studio/:id',
    name: 'studio',
    component: Studio,
    props: true,
    meta: { title: '演播厅' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} · AI Panel Studio` : 'AI Panel Studio'
})

export default router
