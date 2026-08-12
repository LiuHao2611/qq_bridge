import { createRouter, createWebHistory } from 'vue-router'
import ProjectsList from './components/ProjectsList.vue'
import Home from './components/Home.vue'
import TaskCenter from './components/TaskCenter.vue'
import ProjectFragmentEditor from './components/ProjectFragmentEditor.vue'
import MemoirDirectorBoard from './components/MemoirDirectorBoard.vue'
import MemoirList from './components/MemoirList.vue'
import Assets from './components/Assets.vue'
import ProjectEntry from './components/ProjectEntry.vue'
import Canvas from './components/Canvas.vue'
import Txt2Img from './components/Txt2Img.vue'
import Img2Img from './components/Img2Img.vue'
import Creative from './components/Creative.vue'
import GlobalAssets from './components/GlobalAssets.vue'
import ProjectPlanning from './components/ProjectPlanning.vue'
import ProductionStage from './components/ProductionStage.vue'
import Img2Prompt from './components/Img2Prompt.vue'
import History from './components/History.vue'
import Membership from './components/Membership.vue'
import MyData from './components/MyData.vue'
import Messages from './components/Messages.vue'
import Help from './components/Help.vue'
import Logs from './components/Logs.vue'
import Settings from './components/Settings.vue'
import SystemManager from './components/SystemManager.vue'
import { useModuleFlags } from './composables/useModuleFlags'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/projects', component: ProjectsList },
    { path: '/tasks', component: TaskCenter },
    // 人生回忆录入口 → 打开示例回忆录项目
    { path: '/memoir', component: MemoirList, meta: { moduleKey: 'memoir' } },
    { path: '/txt2img', component: Txt2Img, meta: { moduleKey: 'creative' } },
    { path: '/img2img', component: Img2Img, meta: { moduleKey: 'creative' } },
    { path: '/creative', component: Creative, meta: { moduleKey: 'creative' } },
    { path: '/global-assets', component: GlobalAssets, meta: { moduleKey: 'assets' } },
    { path: '/img2prompt', component: Img2Prompt, meta: { moduleKey: 'creative' } },
    { path: '/history', component: History },
    { path: '/membership', component: Membership },
    { path: '/my-data', component: MyData },
    { path: '/messages', component: Messages },
    { path: '/help', component: Help },
    { path: '/logs', component: Logs },
    { path: '/settings', component: Settings },
    { path: '/projects/:id/planning', component: ProjectPlanning, meta: { moduleKey: 'planning' } },
    { path: '/projects/:id/editor', component: ProjectFragmentEditor, meta: { moduleKey: 'fragment-editor' } },
    { path: '/projects/:id/production', component: ProductionStage, meta: { moduleKey: 'production' } },
    { path: '/projects/:id/assets', component: Assets, meta: { moduleKey: 'assets' } },
    { path: '/projects/:id/canvas', component: Canvas, meta: { moduleKey: 'canvas' } },
    // 服务与版本管理器
    { path: '/system', component: SystemManager },
    // 项目统一入口：按 current_phase 自动跳转到进行中阶段
    { path: '/projects/:id', component: ProjectEntry },
    { path: '/memoir/:id', component: MemoirDirectorBoard, meta: { moduleKey: 'memoir' } },
    { path: '/project/:id', redirect: (to) => ({ path: '/projects/' + to.params.id }) },
    { path: '/project/:id/memoir', redirect: (to) => ({ path: '/memoir/' + to.params.id }) },
    { path: '/:pathMatch(.*)*', component: Home },
  ],
})

export default router

// 导航守卫：拦截已停用的业务模块（运行时灰度开关 → 轻量回退）
let _flagsLoaded = false
router.beforeEach(async (to, from, next) => {
  const mk = to.meta && to.meta.moduleKey
  if (mk) {
    const fm = useModuleFlags()
    if (!_flagsLoaded) {
      _flagsLoaded = true
      await fm.loadFlags()
    }
    if (!fm.isEnabled(mk)) {
      return next({ path: '/system', query: { disabled: mk } })
    }
  }
  next()
})

