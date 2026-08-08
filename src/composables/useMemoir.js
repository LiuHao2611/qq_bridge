// ============================================================
// useMemoir —— 回忆录导演板（AGENT_SPEC_15）真实后端数据层
// 路由 /memoirs/:memoirId/chapters/:chapterId/director-board
// 字段严格对齐 spec §3：memoirs / memoir_chapters / shot_data
// 不硬编码任何实例数据（如「高启强」「岁月的余温」）。
// ============================================================
import { ref, computed, watch } from 'vue'
import {
  getOrCreateMemoir, getMemoir, patchMemoir, addTimelineEvent, deleteTimelineEvent,
  uploadReferencePhoto, createChapter, planMemoir, generateStoryboard, generateChapterVideo,
  getChapterTask, preCheck, exportPackage, preAuthPoints, STORYBOARD_COST,
} from '../api'
import { fetchProfile } from '../api'
import { toast } from '../store'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// 章节 shot_data 默认值（平台约定，非实例数据）
function emptyShotData() {
  return {
    characters: [], scenes: [], camera_plan: '', lighting: '',
    mood_keywords: [], shots: [], audio_description: '', voiceover_notes: [],
    color_palette: [], meta: {},
  }
}

export function useMemoir(projectId) {
  const memoir = ref(null)
  const chapters = ref([])
  const activeChapterId = ref('')
  const loading = ref(false)
  const busy = ref(false)            // 生成中
  const taskStatus = ref('')         // queued/running/succeeded/failed
  const preCheckResult = ref(null)
  const pointsBalance = ref(0)

  const activeChapter = computed(() =>
    chapters.value.find((c) => c.chapter_id === activeChapterId.value) || null)
  const chapterCount = computed(() => chapters.value.length)
  const activeIndex = computed(() =>
    chapters.value.findIndex((c) => c.chapter_id === activeChapterId.value))
  const target = computed(() => Number(memoir.value?.storyboard_count) || 4)
  const totalDuration = computed(() =>
    chapterCount.value * (Number(activeChapter.value?.duration) || 15))

  // 副标题（spec §3.1 project-subtitle 模板）
  const subtitle = computed(() => {
    if (!memoir.value) return ''
    const r = memoir.value.target_ratio || '16:9'
    const res = memoir.value.target_resolution || '720p'
    return `人生回忆录制作中 · 已自动保存 · ${r} · ${res} · ${totalDuration.value}s · ${chapterCount.value}张指导板`
  })

  const load = async () => {
    loading.value = true
    try {
      const r = await getOrCreateMemoir(projectId)
      memoir.value = r.memoir
      chapters.value = r.chapters || []
      if (!activeChapterId.value && chapters.value.length) {
        activeChapterId.value = chapters.value[0].chapter_id
      }
      await refreshProfile()
      await refreshPreCheck()
    } catch (e) {
      if (USE_MOCK) {
        memoir.value = { memoir_id: 'mock', subject_name: '', background_text: '', storyboard_count: 4,
          target_ratio: '16:9', target_resolution: '720p', enhance_emotion: '', exclude_content: '',
          reference_photos: [], timeline_events: [], person_perspective: '第一人称' }
        chapters.value = []
      } else {
        toast('回忆录加载失败: ' + (e.message || e))
      }
    }
    loading.value = false
  }

  const selectChapter = (id) => { activeChapterId.value = id; refreshPreCheck() }

  // ---- 回忆录字段（spec §3.2 左栏） ----
  const updateMemoir = async (fields) => {
    if (!memoir.value) return
    try {
      const m = await patchMemoir(memoir.value.memoir_id, fields)
      memoir.value = { ...memoir.value, ...m }
      toast('已保存')
    } catch (e) { toast('保存失败: ' + (e.message || e)) }
  }

  const addTimeline = async (age, description) => {
    if (!memoir.value) return
    try {
      const events = await addTimelineEvent(memoir.value.memoir_id, age, description)
      memoir.value = { ...memoir.value, timeline_events: events }
    } catch (e) { toast('添加事件失败: ' + (e.message || e)) }
  }
  const removeTimeline = async (index) => {
    if (!memoir.value) return
    try {
      const events = await deleteTimelineEvent(memoir.value.memoir_id, index)
      memoir.value = { ...memoir.value, timeline_events: events }
    } catch (e) { toast('删除事件失败: ' + (e.message || e)) }
  }
  const uploadPhoto = async (file) => {
    if (!memoir.value) return
    try {
      const r = await uploadReferencePhoto(memoir.value.memoir_id, file)
      memoir.value = { ...memoir.value, reference_photos: r.reference_photos }
      await refreshPreCheck()
      toast('参考照已上传')
    } catch (e) { toast('上传失败: ' + (e.message || e)) }
  }

  const addChapter = async (title) => {
    if (!memoir.value) return
    if (chapterCount.value >= target.value) {
      toast(`章节数已达故事板数量上限 ${target.value}（BR-019）`)
      return
    }
    try {
      const ch = await createChapter(memoir.value.memoir_id, title)
      chapters.value.push(ch)
      activeChapterId.value = ch.chapter_id
      await refreshPreCheck()
    } catch (e) { toast('新增章节失败: ' + (e.message || e)) }
  }

  // ---- AI 工作流（spec §8） ----
  const plan = async () => {
    if (!memoir.value) return
    busy.value = true
    try {
      const r = await planMemoir(memoir.value.memoir_id)
      chapters.value = r.chapters || []
      activeChapterId.value = chapters.value.length ? chapters.value[0].chapter_id : ''
      toast('AI 策划完成，已生成 ' + chapters.value.length + ' 章')
      await refreshPreCheck()
    } catch (e) { toast('策划失败: ' + (e.message || e)) }
    busy.value = false
  }

  const _pollTask = (memoirId, chapterId, onDone) => {
    let n = 0
    const timer = setInterval(async () => {
      n++
      try {
        const t = await getChapterTask(memoirId, chapterId)
        taskStatus.value = t?.status || ''
        if (t && (t.status === 'succeeded' || t.status === 'failed')) {
          clearInterval(timer)
          onDone(t)
        } else if (n >= 120) {
          clearInterval(timer)
          onDone({ status: 'timeout' })
        }
      } catch (e) {
        clearInterval(timer)
        onDone({ status: 'error', error_message: e.message })
      }
    }, 3000)
  }

  const storyboard = async () => {
    const ch = activeChapter.value
    if (!memoir.value || !ch) return
    busy.value = true; taskStatus.value = 'queued'
    try {
      const pre = await preAuthPoints(STORYBOARD_COST, 'memoir_storyboard', 'idem_' + Date.now())
      const res = await generateStoryboard(memoir.value.memoir_id, ch.chapter_id, pre.pre_auth_id)
      _pollTask(memoir.value.memoir_id, ch.chapter_id, async (t) => {
        if (t.status === 'succeeded') {
          await reloadChapter(ch.chapter_id)
          toast('指导板生成完成')
        } else {
          toast('指导板生成失败: ' + (t.error_message || t.status))
        }
        busy.value = false; taskStatus.value = ''
        await refreshPreCheck()
      })
    } catch (e) { toast('指导板提交失败: ' + (e.message || e)); busy.value = false; taskStatus.value = '' }
  }

  const video = async () => {
    const ch = activeChapter.value
    if (!memoir.value || !ch) return
    if (!memoir.value.reference_photos?.length) { toast('请至少上传 1 张人物参考照（BR-015）'); return }
    if (!ch.storyboard_image_url) { toast('请先生成指导板（BR-021）'); return }
    busy.value = true; taskStatus.value = 'queued'
    try {
      const est = { duration: Number(ch.duration) || 15, resolution: memoir.value.target_resolution || '720p', audio: false, model_id: 'seedance-2.0-mini' }
      const pre = await preAuthPoints(est.duration * 180, 'memoir_video', 'idem_' + Date.now())
      const res = await generateChapterVideo(memoir.value.memoir_id, ch.chapter_id, pre.pre_auth_id)
      _pollTask(memoir.value.memoir_id, ch.chapter_id, async (t) => {
        if (t.status === 'succeeded') {
          await reloadChapter(ch.chapter_id)
          toast('章节视频生成完成')
        } else {
          toast('视频生成失败: ' + (t.error_message || t.status))
        }
        busy.value = false; taskStatus.value = ''
        await refreshPreCheck()
      })
    } catch (e) { toast('视频提交失败: ' + (e.message || e)); busy.value = false; taskStatus.value = '' }
  }

  const reloadChapter = async (chapterId) => {
    if (!memoir.value) return
    try {
      const r = await getMemoir(memoir.value.memoir_id)
      memoir.value = r.memoir
      chapters.value = r.chapters || []
    } catch (e) { /* ignore */ }
  }

  // ---- 分镜保存（BR-016：分镜时长之和 == 章节时长） ----
  const saveChapterShots = async (shots) => {
    const ch = activeChapter.value
    if (!memoir.value || !ch) return
    const sum = shots.reduce((a, s) => a + (Number(s.duration) || 0), 0)
    if (sum !== (Number(ch.duration) || 15)) {
      toast(`分镜时长之和 ${sum}s 不等于章节时长 ${ch.duration}s（BR-016）`)
      return false
    }
    const sd = { ...(ch.shot_data || emptyShotData()), shots }
    // §09 MEM-06：从每镜 camera_path 派生 shot_table_camera_paths 一并持久化
    const cameraPaths = shots.map((s, i) => ({ shot_index: i, camera_path: s.camera_path || 'static', camera_speed: s.camera_speed || 3 }))
    try {
      await patchChapter(memoir.value.memoir_id, ch.chapter_id, { shot_data: sd, shot_table_camera_paths: cameraPaths })
      await reloadChapter(ch.chapter_id)
      toast('分镜已保存')
      return true
    } catch (e) { toast('保存失败: ' + (e.message || e)); return false }
  }

  const refreshProfile = async () => {
    try { const p = await fetchProfile(); pointsBalance.value = p.points_balance || 0 } catch (e) {}
  }
  const refreshPreCheck = async () => {
    if (!memoir.value) return
    try { preCheckResult.value = await preCheck(memoir.value.memoir_id) } catch (e) {}
  }
  const exportPkg = async () => {
    if (!memoir.value) return
    try {
      const r = await exportPackage(memoir.value.memoir_id)
      if (r.download_url) {
        const a = document.createElement('a'); a.href = r.download_url; a.download = ''; a.click()
        toast('交付包已开始下载')
      }
    } catch (e) { toast('导出失败: ' + (e.message || e)) }
  }

  return {
    memoir, chapters, activeChapterId, activeChapter, chapterCount, activeIndex, target,
    totalDuration, subtitle, loading, busy, taskStatus, preCheckResult, pointsBalance,
    load, selectChapter, updateMemoir, addTimeline, removeTimeline, uploadPhoto, addChapter,
    plan, storyboard, video, saveChapterShots, refreshPreCheck, exportPkg, emptyShotData,
  }
}
