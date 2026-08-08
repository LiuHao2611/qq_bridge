import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import router from './router'
import './style.css'
import { initRemoteTheme } from './composables/useTheme'

const app = createApp(App)

for (const [key, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, comp)
}

app.use(ElementPlus)
app.use(router)
// 启动主题远程同步：从后台 8910 拉取并应用「配色预设 + 明暗」（后台为总开关）
initRemoteTheme()
app.mount('#app')
