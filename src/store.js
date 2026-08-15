import { reactive } from 'vue'

// 分级 toast：type ∈ info | success | warning | error
// 默认 info；错误路径请用 toast.error(...) 以红色呈现，杜绝"假成功/中性通知"掩盖失败。
export const toastState = reactive({ msg: '', type: 'info' })
let _timer = null
export function toast(m, type = 'info') {
  toastState.msg = m
  toastState.type = type
  clearTimeout(_timer)
  _timer = setTimeout(() => { toastState.msg = ''; toastState.type = 'info' }, 2600)
}
toast.error = (m) => toast(m, 'error')
toast.warn = (m) => toast(m, 'warning')
toast.success = (m) => toast(m, 'success')
