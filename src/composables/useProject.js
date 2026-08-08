import { ref, nextTick } from 'vue'
import {
  getProject, listSegments, getSegment, createSegment, updateProject, updateSegment, putShots, putAssetRefs,
  deleteSegment, generateSegment, estimatePoints, preAuthPoints, getTask,
  listAssets, listStagesWithPoses,
} from '../api'
import { mockProject, mockMemoir, normalizeShotEnums } from '../mock'
import { toast } from '../store'
import { splitVoice } from '../assetMeta'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// 后端字段 → 前端旧结构映射
// 活动片段（含分镜）映射：被 toFrontend 与 switchSegment 复用，保证切换与首屏一致
const safeJson = (v, fallback = {}) => {
  if (v == null || v === '') return fallback
  if (typeof v === 'object') return v
  try { return JSON.parse(v) } catch { return fallback }
}

// 为所有资产并行加载阶段 + 姿势（KB §02 character_stages / character_poses 已泛化到 asset_id 维度）
const loadAssetStages = async (assets) => {
  if (!Array.isArray(assets) || !assets.length) return assets
  const stageMap = new Map()
  await Promise.all(
    assets.map(async (a) => {
      try {
        const items = await listStagesWithPoses(a.asset_id)
        stageMap.set(a.asset_id, Array.isArray(items) ? items : [])
      } catch (e) {
        console.warn('load stages for', a.asset_id, e.message)
        stageMap.set(a.asset_id, [])
      }
    })
  )
  return assets.map((a) => ({ ...a, stages: stageMap.get(a.asset_id) || [] }))
}

const applyActiveSegment = (p, activeSeg, shots) => {
  if (!activeSeg) {
    p.subtitle = '片段 · 未加载'
    p.scenes = []
    p._activeSegmentId = ''
    return
  }
  const params = safeJson(activeSeg.params, {})
  p.subtitle = '片段 ' + (activeSeg.index || 1) + ' · ' + activeSeg.title
  p.creation_model = params.creation_model || p.creation_model || '创作旗舰 Pro'
  p.ai_model = params.ai_model || p.ai_model || 'Seedance 2.0 Pro'
  p.resolution = activeSeg.resolution || p.resolution || '720p'
  p.ratio = activeSeg.ratio || p.ratio || '16:9'
  p.duration = activeSeg.duration || 12
  p.audio = !!activeSeg.audio_enabled
  p.seed = activeSeg.seed ?? -1
  p.real_person = !!activeSeg.asset_humanize
  p.return_tail = !!activeSeg.return_last_frame
  p.status = activeSeg.status === 'succeeded' ? 'done' : activeSeg.status === 'generating' ? 'rendering' : 'draft'
  p.scene_description = activeSeg.scene_description || ''
  p.remote_task_id = activeSeg.generation_task_id || ''
  p.local_task_id = ''
  p.video_url = activeSeg.video_url || ''
  p.gen_log = activeSeg.gen_log || ''
  p._activeSegmentId = activeSeg.segment_id
  p.scenes = shots.map((s, idx) => normalizeShotEnums({
    scene_id: s.shot_id,
    chapter_id: activeSeg.segment_id,
    no: s.index || idx + 1,
    caption: (s.description || '').split('\n')[0].slice(0, 20) || '分镜 ' + (idx + 1),
    description: s.description || '',
    prompt: s.image_prompt || '',
    negative_prompt: s.negative_prompt || '',
    duration: s.duration || 3,
    shot_type: s.shot_type,
    camera_angle: s.camera_angle,
    camera_movement: s.camera_movement,
    focal_length: s.focal_length,
    thumb: s.image_url || '',
    voiceover: s.voiceover || '',
    dialogue: s.dialogue || '',
    refs: (safeJson(s.asset_refs, [])).map((r) => r?.asset_id || r).filter(Boolean),
    setting: s.setting || '',
  }))
}

// 资产映射（角色/场景/道具）抽出，供首屏 toFrontend 与 refreshAssets 复用
const applyAssets = (project, assets = []) => {
  const list = assets || []
  const toStage = (s) => ({
    stage_id: s.stage_id,
    stage_name: s.stage_name,
    age: s.age ?? '',
    reference_image_url: s.reference_image_url || '',
    poses: Array.isArray(s.poses) ? s.poses.map((p) => ({
      pose_id: p.pose_id,
      pose_name: p.pose_name,
      reference_image_url: p.reference_image_url || '',
    })) : [],
  })
  project.characters = list.filter((a) => a.type === 'role').map((a) => {
    const { desc, voice } = splitVoice(a.description || '')
    return {
      id: a.asset_id,
      name: a.name,
      relation: ({ protagonist: '主角', supporting: '配角', antagonist: '反派' })[a.role_type] || a.role_type || '主角',
      desc,
      voice,
      thumb: a.cover_url || '',
      stages: (a.stages || []).map(toStage),
    }
  })
  project.assets = {
    scenes: list.filter((a) => a.type === 'scene').map((a) => ({ id: a.asset_id, name: a.name, type: 'scene', thumb: a.cover_url || '', stages: (a.stages || []).map(toStage) })),
    images: [],
    props: list.filter((a) => a.type === 'prop').map((a) => ({ id: a.asset_id, name: a.name, type: 'prop', thumb: a.cover_url || '', stages: (a.stages || []).map(toStage) })),
  }
}

const toFrontend = (project, segments, segmentWithShots, assets, kind) => {
  const activeSeg = segmentWithShots.segment
  const shots = segmentWithShots.shots || []

  const out = {
    id: project.project_id,
    title: project.name,
    creation_model: '创作旗舰 Pro',
    ai_model: 'Seedance 2.0 Pro',
    preset_id: project.preset_id || 'flash-long',
    first_frame_mode: project.first_frame_mode || 'no_output', // STR-03 首帧机制：no_output/output
    // 活动片段派生字段（resolution/ratio 先取项目级兜底，applyActiveSegment 会按片段覆盖）
    resolution: project.resolution || '720p',
    ratio: project.ratio || '16:9',
    chapters: segments.map((s) => ({ id: s.segment_id, title: s.title, duration: s.duration, count: s.shot_count, thumb: s.thumbnail_url || '' })),
    palette: mockProject.palette,
    anchors: mockProject.anchors,
    // 内部状态（不渲染）
    _project: project,
    _segments: segments,
  }

  // 角色/场景/道具资产映射（抽出便于刷新）
  applyAssets(out, assets)

  // 活动片段（含分镜）统一映射
  applyActiveSegment(out, activeSeg, shots)

  // 回忆录专属字段（后端 memoirs 表尚未暴露独立 API；此处给安全默认值，避免模板崩溃）
  if (kind === 'memoir') {
    const totalDur = segments.reduce((a, s) => a + (s.duration || 0), 0) || 12
    Object.assign(out, {
      person: { name: '主人公', born: '', stages: [] },
      story: { timeline: [], environment: [], visual_cues: [], lighting_mood: '', emotion_keywords: [], voice_narration: '', notes: '' },
      strengthen_emotion: [],
      reference_photos: [],
      storyboard_count: 4,
      negative_prompt: '',
      output_params: {
        ratio: project.ratio || '16:9',
        spec: project.resolution || '720p',
        boards: 1,
        total_duration: totalDur,
        subtitle: '中文',
        models: { plan: 'Qwen3.5', board: 'Qwen3.5', video: 'Seedance 2.0', audio: '本地 TTS' },
      },
      checks: [
        { ok: true, label: '人物档案已填写' },
        { ok: assets.length > 0, label: '已关联至少一个角色资产' },
        { ok: shots.length > 0, label: '分镜已生成' },
        { ok: true, label: '本地 AI（Ollama）已连接' },
      ],
    })
  }
  return out
}

export function useProject(id, kind) {
  const project = ref(null)
  const loading = ref(false)

  const seed = () => structuredClone(kind === 'memoir' ? mockMemoir : mockProject)

  const load = async (activeSegId) => {
    loading.value = true
    if (USE_MOCK) {
      project.value = seed()
      loading.value = false
      return
    }
    try {
      const p = await getProject(id)
      const segments = await listSegments(id)
      if (!segments.length) {
        // 无片段时自动创建一个
        const ns = await createSegment(id, { title: '片段一', duration: 12, resolution: p.resolution || '720p', ratio: p.ratio || '16:9' })
        segments.push(ns)
      }
      const activeSegId2 = activeSegId || segments[0].segment_id
      const segWithShots = await getSegment(id, activeSegId2)
      const assets = await listAssets()
      const assetsWithStages = await loadAssetStages(assets)
      project.value = toFrontend(p, segments, segWithShots, assetsWithStages, kind)
    } catch (e) {
      console.warn('project load failed, falling back to mock', e.message)
      toast('后端加载失败，使用演示数据')
      project.value = seed()
    }
    loading.value = false
  }

  // 切换片段：从后端拉取目标片段及其分镜并替换活动片段状态（项目级字段不变）
  const switchSegment = async (segId, saveFirst = false) => {
    if (USE_MOCK || !project.value) return
    try {
      if (saveFirst) { await save() }
      project.value._activeSegmentId = segId // 乐观切换高亮，避免闪烁
      loading.value = true
      const sw = await getSegment(id, segId)
      applyActiveSegment(project.value, sw.segment, sw.shots)
      const ci = project.value.chapters.findIndex((c) => c.id === segId)
      if (ci >= 0) {
        project.value.chapters[ci] = {
          id: sw.segment.segment_id, title: sw.segment.title,
          duration: sw.segment.duration, count: sw.shots.length,
          thumb: sw.segment.thumbnail_url || '',
        }
      }
    } catch (e) {
      toast('切换片段失败: ' + (e.message || e))
    } finally {
      loading.value = false
    }
  }

  // 新增片段：调后端创建（自带合法分镜时长），重载并切到新片段
  const addSegment = async (title, duration) => {
    if (USE_MOCK || !project.value) return
    try {
      const ns = await createSegment(id, {
        title: title || ('新片段 ' + (project.value.chapters.length + 1)),
        duration: duration || project.value.duration || 8,
        resolution: project.value.resolution || '720p',
        ratio: project.value.ratio || '16:9',
      })
      await load(ns.segment_id)
    } catch (e) {
      toast('新增片段失败: ' + (e.message || e))
      throw e
    }
  }

  // 删除片段：调后端删除（保留最后一个），重载并切到剩余首片段
  const removeSegment = async (segId) => {
    if (USE_MOCK) {
      project.value.chapters = project.value.chapters.filter((c) => c.id !== segId)
      project.value.scenes = project.value.scenes.filter((s) => s.chapter_id !== segId)
      if (project.value._activeSegmentId === segId) {
        const first = project.value.chapters[0]
        if (first) project.value._activeSegmentId = first.id
      }
      return
    }
    try {
      await deleteSegment(id, segId)
      const remaining = project.value.chapters.filter((c) => c.id !== segId)
      const nextActive = (segId === project.value._activeSegmentId)
        ? (remaining[0]?.id)
        : project.value._activeSegmentId
      await load(nextActive)
    } catch (e) {
      toast('删除片段失败: ' + (e.message || e))
      throw e
    }
  }

  // 刷新资产库（新建资产后即时反映在编辑器左侧资产库 / 分镜引用候选中）
  const refreshAssets = async () => {
    if (USE_MOCK || !project.value) return
    try {
      const assets = await listAssets()
      const assetsWithStages = await loadAssetStages(assets)
      applyAssets(project.value, assetsWithStages)
      await nextTick()
    } catch (e) {
      console.warn('refresh assets failed', e.message)
    }
  }

  const save = async (payload) => {
    if (USE_MOCK || !project.value) {
      if (payload) Object.assign(project.value, payload)
      toast('已保存（演示模式）')
      return
    }
    try {
      const p = project.value
      const pid = p.id
      const sid = p._activeSegmentId
      const activeCh = p.chapters.find((c) => c.id === sid)
      await updateProject(pid, { name: p.title, preset_id: p.preset_id, first_frame_mode: p.first_frame_mode || 'no_output' })
      await updateSegment(pid, sid, {
        title: (activeCh?.title || p.subtitle.split('·')[1]?.trim() || '片段'),
        duration: p.duration,
        resolution: p.resolution,
        ratio: p.ratio,
        audio_enabled: p.audio ? 1 : 0,
        asset_humanize: p.real_person ? 1 : 0,
        return_last_frame: p.return_tail ? 1 : 0,
        seed: p.seed,
        scene_description: p.scene_description || '',
      })
      // 保存分镜
      const shots = p.scenes.map((s) => ({
        shot_id: s.scene_id,
        index: s.no,
        duration: s.duration,
        shot_type: s.shot_type,
        camera_movement: s.camera_movement,
        camera_angle: s.camera_angle,
        focal_length: s.focal_length,
        description: s.description,
        image_prompt: s.prompt,
        voiceover: s.voiceover,
        dialogue: s.dialogue,
        negative_prompt: s.negative_prompt || '',
        setting: s.setting || '',
        asset_refs: s.refs.map((rid) => ({ asset_id: rid, asset_type: 'role', used_in: 'shot' })),
      }))
      await putShots(pid, sid, shots)
      toast('已保存')
      return true
    } catch (e) {
      toast('保存失败: ' + (e.message || e))
      return false
    }
  }

  const generate = async () => {
    if (USE_MOCK || !project.value) {
      toast('生成任务（演示）')
      return { task_id: 'demo' }
    }
    try {
      const p = project.value
      const pid = p.id
      const sid = p._activeSegmentId
      const params = {
        duration: p.duration,
        resolution: p.resolution,
        ratio: p.ratio,
        audio: p.audio ? 1 : 0,
        model_id: p.ai_model,
        asset_humanize: p.real_person ? 1 : 0,
        return_last_frame: p.return_tail ? 1 : 0,
        seed: p.seed,
      }
      const est = await estimatePoints({ operation: 'estimate', ...params })
      const idem = 'idem_' + Date.now() + '_' + Math.random().toString(36).slice(2)
      const pre = await preAuthPoints(est.estimated_credits, 'segment_generate', idem)
      const res = await generateSegment(pid, sid, pre.pre_auth_id, params)
      toast('生成任务已提交')
      return res
    } catch (e) {
      toast('生成失败: ' + (e.message || e))
      throw e
    }
  }

  const pollTask = async (taskId, onUpdate) => {
    if (USE_MOCK || taskId === 'demo') return
    let attempts = 0
    const max = 120 // 5秒×120=10分钟
    const timer = setInterval(async () => {
      attempts++
      try {
        const t = await getTask(taskId)
        if (onUpdate) onUpdate(t)
        if (t.status === 'succeeded' || t.status === 'failed' || t.status === 'cancelled' || attempts >= max) {
          clearInterval(timer)
        }
      } catch (e) {
        console.warn('poll task failed', e.message)
      }
    }, 5000)
  }

  return { project, loading, load, save, generate, pollTask, switchSegment, addSegment, removeSegment, refreshAssets }
}
