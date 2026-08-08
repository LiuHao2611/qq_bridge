// useAutoSave —— 统一自动保存（防抖 + 状态可见 + 失败显式提示）
// 设计依据：
//  - 知识库 §08 CLOSE-02「禁止静默吞错」：保存失败必须显式提示，不能静默。
//  - Web 端工具保存最佳实践（检索）：「定时触发 + 手动保存」双保险；状态可见（保存中/已保存/失败）；
//    失败第一时间告知并给可行方案；自动保存不应打断用户操作。
//
// 用法：
//   const { status, lastSavedAt, flush, schedule } = useAutoSave({
//     dirty,            // ref<boolean>：内容变更标记（页面自行维护）
//     save,             // async () => boolean|void：真实保存函数，返回 false 表示失败
//     enabled,          // ref<boolean>（可选）：是否启用自动保存（如 mock 模式为 false）
//     delay,            // number（可选）：防抖毫秒，默认 1500
//   })
// status: 'idle' | 'pending' | 'saving' | 'saved' | 'error'
import { ref, watch, onUnmounted } from 'vue'

export function useAutoSave({ dirty, save, enabled, delay = 1500 }) {
  const status = ref('idle') // idle | pending | saving | saved | error
  const lastSavedAt = ref(0)
  let timer = null
  let running = false

  const clearTimer = () => { if (timer) { clearTimeout(timer); timer = null } }

  const flush = async () => {
    if (running) return
    if (enabled && enabled.value === false) return
    running = true
    status.value = 'saving'
    try {
      const res = await save()
      if (res === false) {
        status.value = 'error'
        return
      }
      status.value = 'saved'
      lastSavedAt.value = Date.now()
      // 自动复位 dirty，避免重复保存
      if (dirty && typeof dirty.value !== 'undefined') dirty.value = false
      // 2.5s 后回到 idle，保持界面干净
      setTimeout(() => { if (status.value === 'saved') status.value = 'idle' }, 2500)
    } catch (e) {
      // CLOSE-02：显式记录并暴露失败态（页面会据此提示）
      status.value = 'error'
      console.warn('[useAutoSave] save failed:', e && e.message)
    } finally {
      running = false
    }
  }

  const schedule = () => {
    if (enabled && enabled.value === false) return
    if (status.value !== 'saving') status.value = 'pending'
    clearTimer()
    timer = setTimeout(() => { flush() }, delay)
  }

  watch(
    dirty,
    (v) => { if (v) schedule() },
    { flush: 'post' }
  )

  onUnmounted(clearTimer)

  return { status, lastSavedAt, flush, schedule }
}
