import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import 'element-plus/dist/index.css'
import '@/styles/base.css'

import App from '@/App.vue'
import router from '@/router'

const app = createApp(App)

// 中文 UI（PRD C-04）：Element Plus 全量中文语言包
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
