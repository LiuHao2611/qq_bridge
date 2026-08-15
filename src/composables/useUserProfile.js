import { ref, computed } from 'vue'
import defaultAvatar from '../assets/avatar-default.jpg'

const LS_KEY_NAME = 'manju_user_nickname'
const LS_KEY_AVATAR = 'manju_user_avatar'
const DEFAULT_NAME = '小志'

function loadName() {
  try {
    const v = localStorage.getItem(LS_KEY_NAME)
    if (v && v.trim()) return v.trim()
  } catch (e) { /* ignore */ }
  return DEFAULT_NAME
}

function saveName(v) {
  try {
    localStorage.setItem(LS_KEY_NAME, (v || '').trim())
  } catch (e) { /* ignore */ }
}

function loadAvatar() {
  try {
    const v = localStorage.getItem(LS_KEY_AVATAR)
    if (v && v.trim()) return v.trim()
  } catch (e) { /* ignore */ }
  return defaultAvatar
}

const nickname = ref(loadName())
const avatarUrl = ref(loadAvatar())

export function useUserProfile() {
  const name = computed({
    get: () => nickname.value || DEFAULT_NAME,
    set: (v) => {
      nickname.value = (v || '').trim() || DEFAULT_NAME
      saveName(nickname.value)
    },
  })

  const avatar = computed(() => avatarUrl.value || defaultAvatar)

  function setAvatar(url) {
    avatarUrl.value = url || defaultAvatar
    try {
      if (url) localStorage.setItem(LS_KEY_AVATAR, url)
      else localStorage.removeItem(LS_KEY_AVATAR)
    } catch (e) { /* ignore */ }
  }

  function reset() {
    nickname.value = DEFAULT_NAME
    avatarUrl.value = defaultAvatar
    try {
      localStorage.removeItem(LS_KEY_NAME)
      localStorage.removeItem(LS_KEY_AVATAR)
    } catch (e) { /* ignore */ }
  }

  return { nickname: name, avatar, setAvatar, reset, DEFAULT_NAME }
}
