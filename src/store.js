import { reactive } from 'vue'

export const toastState = reactive({ msg: '' })
let _timer = null
export function toast(m) {
  toastState.msg = m
  clearTimeout(_timer)
  _timer = setTimeout(() => (toastState.msg = ''), 2600)
}
