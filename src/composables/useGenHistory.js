// 模型生成历史记录（文字 / 图片 / 视频）
// 用途：每次「调用模型生成内容」成功后，把结果摘要 + 完整 payload 写入 localStorage；
//       左侧折叠面板（HistoryPanel.vue）列出这些记录，点击「覆盖使用」即可回填/覆盖当前内容，
//       避免模型跑一次后误关导致结果丢失、或想复用上次生成却要重跑。
// 设计：纯 localStorage（不进 C 盘、不写文件）；try/catch 吞异常不阻塞主流程。
const HISTORY_CAP = 30

export const genKey = {
  extract: (pid) => `manju_hist_extract_${pid}`,
  split:   (pid) => `manju_hist_split_${pid}`,
  polish:  (pid) => `manju_hist_polish_${pid}`,
  editor:  (pid) => `manju_hist_editor_${pid}`, // P3 润色 / P4 重新生成 / P5 合成全集（同一编辑器）
  t2i:     (pid) => `manju_hist_t2i_${pid}`,
  prod:    (pid) => `manju_hist_prod_${pid}`, // P4 生产阶段·逐片段视频生成历史（ProductionStage 页）
}

// record: { kind:'text'|'image'|'video', title, preview, previewText?, payload }
export function pushHistory(key, record) {
  try {
    const raw = localStorage.getItem(key)
    let list = raw ? JSON.parse(raw) : []
    if (!Array.isArray(list)) list = []
    const item = {
      id: 'h_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7),
      ts: Date.now(),
      kind: record.kind || 'text',
      title: record.title || '',
      preview: record.preview || '',
      previewText: record.previewText || '',
      payload: record.payload || null,
    }
    list.unshift(item)
    if (list.length > HISTORY_CAP) list = list.slice(0, HISTORY_CAP)
    localStorage.setItem(key, JSON.stringify(list))
    return item
  } catch (e) {
    console.warn('[useGenHistory] push failed', key, e)
    return null
  }
}

export function loadHistory(key) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return []
    const list = JSON.parse(raw)
    return Array.isArray(list) ? list : []
  } catch (e) { return [] }
}

export function clearHistory(key) {
  try { localStorage.removeItem(key) } catch (e) { /* ignore */ }
}
