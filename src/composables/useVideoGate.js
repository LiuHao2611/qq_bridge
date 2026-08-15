// -*- coding: utf-8 -*-
// 视频生成前置闸门（用户诉求：1660Ti 6GB 跑不动视频，别让用户白等 30 分钟才 OOM）。
//
// 设计原则（无创 / 诚实 / 失败开放）：
// - 调后端 /video/readiness 拿到 { feasible, reason, recommendation }；
// - feasible===false → 弹确认框，明确告知「为何跑不动 + 建议」，用户取消即停止分发；
// - 探测本身失败（接口/网络异常）→ 失败开放，绝不因探测不可靠而拦住用户；
// - 仅用于「视频生成」链路（generateSegment / regenerateSegment）；
//   图像生成（Img2Img/Txt2Img）与 FFmpeg 合成（export/synthesize）不受影响。
//
// 关于「本地 ComfyUI 等价替代云端模型」：
// 架构上 MODEL_OPTIONS 的 seedance/kling/jimeng/vidu 虽是云端命名，但在 manju_agent
// 本地部署里被 ComfyUI 本地视频合成等价替代（见 server/memoir_routes.py:6）。
// 因此视频生成实际跑在「本机 GPU + 本地 ComfyUI」，本地显存门槛才是硬约束——
// 这也正是本闸门探测本机 GPU 显存的合理性所在。
//
// 会话级决策缓存：首次询问后记住用户选择（继续 / 取消），避免「一键分发全部」
// 时对每一片段重复弹窗；刷新页面后重新询问。
import { ref } from 'vue'
import { videoReadiness } from '../api'

const CACHE_TTL_MS = 60_000
let _cache = { data: null, at: 0 }
let _decided = null  // 会话级：true=继续生成, false=停止分发, null=尚未询问

async function probe() {
  const now = Date.now()
  if (_cache.data && now - _cache.at < CACHE_TTL_MS) return _cache.data
  const data = await videoReadiness()
  _cache = { data, at: now }
  return data
}

export function useVideoGate() {
  const lastReason = ref('')
  const lastRecommendation = ref('')

  // 返回 true=放行（可行 / 未知 / 探测失败 / 用户已确认继续）；
  // 返回 false=用户主动取消（不应继续分发）。
  async function ensureVideoReady() {
    if (_decided !== null) return _decided  // 复用本会话已做的决定
    let r
    try {
      r = await probe()
    } catch (e) {
      // 探测失败 → 失败开放，不阻断（探测不可靠不应拦住用户）
      console.warn('[video-gate] readiness probe failed, fail-open:', e && e.message)
      return true
    }
    if (!r || r.feasible !== false) {
      lastReason.value = ''
      lastRecommendation.value = ''
      return true
    }
    lastReason.value = r.reason || '当前设备显存不足以稳定生成视频'
    lastRecommendation.value = r.recommendation || ''
    const body = lastReason.value +
      (lastRecommendation.value ? '\n\n建议：' + lastRecommendation.value : '')
    const ok = window.confirm(
      '⚠️ 视频生成可能失败\n\n' + body +
      '\n\n仍要尝试吗？\n（取消将停止分发，避免长时间等待后 OOM / 失败）'
    )
    _decided = ok  // 记住决定，避免批量分发时反复弹窗
    return ok
  }

  // 被动展示用：仅取探测结果，不弹窗、不修改 _decided。
  async function peekReadiness() {
    try { return await probe() } catch { return null }
  }

  return { ensureVideoReady, peekReadiness, lastReason, lastRecommendation }
}
