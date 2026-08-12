// 模型输出自动草稿（P1 提取 / P2 分场 / P3 润色）
// 用途：Ollama 推理成功后立即写 localStorage；下次进入页面/弹窗时检测草稿，
//       顶部弹「恢复/丢弃」提示条，避免误关/误取消导致模型结果丢失重跑。
// 设计：纯 localStorage（不进 C 盘、不写文件）；try/catch 吞 localStorage 异常不阻塞主流程。
export const saveDraft = (key, data) => {
  try {
    localStorage.setItem(key, JSON.stringify({ data, ts: Date.now() }))
    return true
  } catch (e) {
    console.warn('[useDraft] save failed', key, e)
    return false
  }
}

export const loadDraft = (key) => {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const obj = JSON.parse(raw)
    if (!obj || typeof obj.ts !== 'number') return null
    return obj
  } catch (e) {
    console.warn('[useDraft] load failed', key, e)
    return null
  }
}

export const clearDraft = (key) => {
  try { localStorage.removeItem(key) } catch (e) { /* ignore */ }
}

export const draftAge = (ts) => {
  const m = Math.floor((Date.now() - ts) / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

// 三个 stage 的 key 工厂，集中管理避免拼写漂移
export const draftKey = {
  extract: (pid) => `manju_draft_extract_${pid}`,
  split:   (pid) => `manju_draft_split_${pid}`,
  polish:  (pid) => `manju_draft_polish_${pid}`,
}