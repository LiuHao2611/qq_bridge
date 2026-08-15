<script setup>
// P5 主题 · 全站弹窗统一予告状（覆盖 Element Plus 各类型弹窗 + 自研 toast）
// 会员中心改造原则（user 2026-08-14 强制要求）：
//   - 全部前台弹窗与「会员中心」一致，不只是外观，**动画也要完全一致**
//   - 会员中心：ElMessage.success(…) 同步触发 P5Celebrate 的 MISSION COMPLETE 予告状
//   - 修复：之前 P5Celebrate 只监听 success，其它类型(info/warning/error/notification/自研 toast)漏触发
//
// 实现：MutationObserver 同时监听 5 类节点：
//   1) .el-message--success / .el-message--info / .el-message--warning / .el-message--error
//   2) .el-notification（任意位置）
//   3) App.vue 内 .toast-wrap > .toast（store.js 中 toast() 渲染）
// 类型→kinds 映射到 4 套文案/图标（success/info/warning/error），notification 默认走 info。
// 仅在 html.t-p5 主题激活时生效；pointer-events:none 绝不遮挡可点击元素。
import { ref, onMounted, onBeforeUnmount } from 'vue'

const show = ref(false)
const msg = ref('')
const kind = ref('success') // success | info | warning | error
const seq = ref(0) // 每次 fire 递增 → :key 强制重建 → 入场动画每次都重跑
let timer = null
let mo = null

const KIND_LABEL = {
  success: '任务完成 · MISSION COMPLETE',
  info: '通知 · NOTICE',
  warning: '注意 · WARNING',
  error: '失败 · FAILED',
}
const KIND_ICON = {
  success: '★',
  info: 'i',
  warning: '!',
  error: '✕',
}

// P5 主题色：success/info 走绯红；warning 走琥珀；error 走深红（与硬阴影 → 暗红）
const KIND_RED = {
  success: 'var(--p5-red)',
  info: 'var(--p5-red)',
  warning: '#ffb020',
  error: 'var(--p5-red-deep)',
}

function fire(detail) {
  if (!document.documentElement.classList.contains('t-p5')) return
  const k = detail && detail.kind && KIND_LABEL[detail.kind] ? detail.kind : 'info'
  kind.value = k
  msg.value = (detail && detail.msg) || KIND_LABEL[k]
  // 用 :key 强制 Vue 重新挂载覆盖层 DOM，从而让入场动画每次都从头播放，
  // 与会员中心「每次操作都触发一次预告状」的语义一致。
  // 不做 show=false 中间切换，避免 Teleport 锚点被外部删 DOM 时 Vue 抛错。
  if (!show.value) show.value = true
  seq.value += 1
  clearTimeout(timer)
  timer = setTimeout(() => { show.value = false }, 2800)
}

function onCelebrate(e) { fire({ kind: e.detail?.kind, msg: e.detail?.msg }) }

// 从一个节点上识别所属类型（避免误判）
function detectKind(el) {
  if (!el || !el.classList) return 'info'
  if (el.classList.contains('el-message--success')) return 'success'
  if (el.classList.contains('el-message--warning')) return 'warning'
  if (el.classList.contains('el-message--error')) return 'error'
  if (el.classList.contains('el-message--info')) return 'info'
  if (el.classList.contains('el-notification--success')) return 'success'
  if (el.classList.contains('el-notification--warning')) return 'warning'
  if (el.classList.contains('el-notification--error')) return 'error'
  if (el.classList.contains('toast')) {
    if (el.classList.contains('toast-error')) return 'error'
    if (el.classList.contains('toast-warning')) return 'warning'
    if (el.classList.contains('toast-success')) return 'success'
    return 'info'
  }
  return 'info'
}

// 提取内容节点文本（优先取 .el-message__content / .el-notification__content / .toast 自身）
function extractText(el) {
  if (!el) return ''
  const inner = el.querySelector('.el-message__content, .el-notification__content, .el-notification__title')
  const src = inner || el
  return (src.textContent || '').replace(/\s+/g, ' ').trim()
}

// 一个 DOM 节点是否落在我们关心的弹窗容器里（任意一类即可）
function isTracked(el) {
  if (!el || el.nodeType !== 1) return false
  if (el.classList && el.classList.contains('toast')) return true
  if (el.classList && (
    el.classList.contains('el-message')
    || el.classList.contains('el-notification')
  )) return true
  if (el.querySelector) {
    return !!el.querySelector('.el-message, .el-notification')
  }
  return false
}

onMounted(() => {
  if (!document.documentElement.classList.contains('t-p5')) return
  window.addEventListener('p5:celebrate', onCelebrate)
  // 监听 DOM 中出现的 4 类 Element Plus 弹窗 + 自研 toast
  mo = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (!n || n.nodeType !== 1) continue
        // 节点本身即弹窗 → 直接 fire
        if (n.classList && (
          n.classList.contains('el-message')
          || n.classList.contains('el-notification')
        )) {
          fire({ kind: detectKind(n), msg: extractText(n) })
          continue
        }
        // 子节点中存在弹窗
        if (n.querySelector && (
          n.querySelector('.el-message')
          || n.querySelector('.el-notification')
        )) {
          const inner = n.querySelector('.el-message, .el-notification')
          fire({ kind: detectKind(inner), msg: extractText(inner) })
          continue
        }
        // 自研 .toast
        if (n.classList && n.classList.contains('toast')) {
          fire({ kind: detectKind(n), msg: extractText(n) })
        }
      }
    }
  })
  mo.observe(document.body, { childList: true, subtree: true })
})

onBeforeUnmount(() => {
  window.removeEventListener('p5:celebrate', onCelebrate)
  if (mo) mo.disconnect()
  clearTimeout(timer)
})
</script>

<template>
  <teleport to="body">
    <div v-if="show" :key="seq" class="p5-celebrate" :data-kind="kind" aria-hidden="true">
      <div class="p5-starburst"><span>{{ KIND_ICON[kind] }}</span></div>
      <div class="p5-notice" :style="{ borderLeftColor: KIND_RED[kind] }">
        <h4>{{ KIND_LABEL[kind] }}</h4>
        <div class="p5-celebrate-msg">{{ msg }}</div>
      </div>
    </div>
  </teleport>
</template>

<style scoped>
.p5-celebrate {
  position: fixed; left: 50%; top: 92px; transform: translateX(-50%);
  z-index: 4000; pointer-events: none;
  display: flex; align-items: center; gap: 16px;
}
.p5-celebrate .p5-notice { min-width: 240px; max-width: 420px; }
.p5-celebrate-msg { font-family: var(--font-body); font-size: 13px; color: var(--p5-white); }
</style>
