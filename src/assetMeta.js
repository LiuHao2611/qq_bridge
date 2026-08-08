// 角色「设定」与「音色」共存于 assets.description 字段。
// 后端 patch_asset 仅支持 name/description/tags/cover_url/role_type，且无独立音色列，
// 故用不可见分隔符把音色文案寄生在 description 末尾；加载时剥离，避免污染分镜提示词。
const VOICE_MARK = '\u0001VOICE\u0001'

export function splitVoice(desc = '') {
  const i = String(desc).indexOf(VOICE_MARK)
  if (i < 0) return { desc: String(desc), voice: '' }
  return { desc: String(desc).slice(0, i), voice: String(desc).slice(i + VOICE_MARK.length) }
}

export function joinVoice(desc = '', voice = '') {
  const clean = String(desc).replace(VOICE_MARK, '')
  return voice ? clean + VOICE_MARK + voice : clean
}
