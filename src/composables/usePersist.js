import { reactive, watch } from 'vue'

// 创建一个「刷新不丢」的响应式状态：初始化时从 localStorage 读取，
// 之后任意字段变化都自动写回 localStorage（深度监听）。
export function usePersistedState(key, defaults) {
  let init = { ...defaults }
  try {
    const raw = localStorage.getItem(key)
    if (raw) init = { ...defaults, ...JSON.parse(raw) }
  } catch (e) { /* 忽略损坏数据 */ }
  const state = reactive(init)
  watch(state, () => {
    try { localStorage.setItem(key, JSON.stringify(state)) } catch (e) { /* 容量/隐私模式忽略 */ }
  }, { deep: true })
  return state
}
