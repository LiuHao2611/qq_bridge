// 业务模块灰度开关（与服务管理器 module_flags 表对齐）。
// 供路由导航守卫拦截「已停用」模块，实现运行时上线/下线这类轻量回退。
import { reactive, ref } from 'vue'
import { listModules } from '../api'

const flags = reactive({})     // key -> boolean（是否启用）
const loaded = ref(false)

export function useModuleFlags() {
  async function loadFlags() {
    try {
      const res = await listModules()
      const mods = (res && res.modules) || []
      mods.forEach((m) => { flags[m.key] = m.enabled !== false })
      loaded.value = true
    } catch (e) {
      // 拉取失败不阻断导航，默认全部启用
    }
  }

  // 未加载或未知模块 → 默认启用（安全放行）
  function isEnabled(key) {
    if (!(key in flags)) return true
    return flags[key] !== false
  }

  return { flags, loaded, loadFlags, isEnabled }
}
