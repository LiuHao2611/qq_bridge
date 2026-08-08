import { ref, computed, onActivated, onDeactivated, onBeforeUnmount } from 'vue'
import { servicesStatus } from '../api.js'

/**
 * 服务状态生命周期管理（KB 规范：切页/卸载时清理资源，避免后台轮询与并发请求堆积）。
 * - 仅在组件 activated 时轮询，deactivated 自动停止并中断未完成的请求。
 * - refresh() 去重：有请求在飞时直接返回。
 * - 返回 services / loading / difyReady / refresh / start / stop。
 */
export function useServiceStatus(opts = {}) {
  const { interval = 10000 } = opts

  const services = ref({})
  const loading = ref(false)
  const active = ref(false)

  let abortCtrl = null
  let intervalId = null

  const difyReady = computed(() => !!services.value?.dify?.ready)

  const ollamaReady = computed(() => !!services.value?.ollama?.ready)

  async function refresh() {
    if (loading.value) return
    if (abortCtrl) abortCtrl.abort()
    abortCtrl = new AbortController()
    loading.value = true
    try {
      services.value = await servicesStatus({ signal: abortCtrl.signal })
    } catch (e) {
      if (e?.name !== 'AbortError') {
        services.value = {}
      }
    } finally {
      loading.value = false
      abortCtrl = null
    }
  }

  function start() {
    if (active.value) return
    active.value = true
    refresh()
    intervalId = setInterval(refresh, interval)
  }

  function stop() {
    active.value = false
    if (intervalId) {
      clearInterval(intervalId)
      intervalId = null
    }
    if (abortCtrl) {
      abortCtrl.abort()
      abortCtrl = null
    }
  }

  onActivated(start)
  onDeactivated(stop)
  onBeforeUnmount(stop)

  return { services, loading, difyReady, ollamaReady, refresh, start, stop }
}
