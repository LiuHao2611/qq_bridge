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

// F4：阶段命名统一（INV-2）。三处 submitStage 必须一致：
//   - role 类型：层级名 `<角色名>-<后缀>`（如「周岩-青年」），便于跨角色去歧义与引用解析；
//   - scene / prop 类型：裸名（阶段即该场景/道具自身的状态，无需前缀）。
// assetName 为父资产名（role 时必填），suffix 为用户输入的阶段名后缀（scene/prop 时为完整名）。
export function stageNameFor(assetName, type, suffix) {
  const s = String(suffix || '').trim()
  if (type === 'role') {
    const base = String(assetName || '').trim()
    return base ? base + '-' + (s || 'default') : (s || 'default')
  }
  return s
}
