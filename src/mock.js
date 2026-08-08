// ============================================================
// Mock 数据层（不调用本地模型 / 不连后端时使用）
// 结构对齐现有 web_app.py 的 ProjectStore 字段，回忆录扩展字段放入 story 子键。
// ============================================================

// ---- 顶部 / 模型下拉选项（来自知识库精确选项清单）----
export const OPTION_CREATION_MODELS = ['创作旗舰 Pro', '创作旗舰 Flash·短', '创作旗舰 Flash·长', '创作旗舰 Max']
export const OPTION_AI_MODELS = ['Seedance 2.0 Mini', 'Seedance 2.0 Pro', '可灵 1.6', '即梦视频 3.0', 'Vidu Q3']
export const OPTION_RESOLUTIONS = ['480p', '720p', '1080p', '2K', '4K']
export const OPTION_RATIOS = ['16:9', '9:16', '1:1', '4:3', '21:9']
export const OPTION_DURATIONS = [5, 8, 10, 12, 15]
export const OPTION_STORYBOARD_COUNTS = [4, 6, 8, 10]
export const OPTION_MEMOIR_RATIOS = ['16:9', '9:16', '1:1']
export const OPTION_MEMOIR_RESOLUTIONS = ['720p', '1080p', '2K']
export const OPTION_EMOTIONS = ['克制', '温暖', '怀旧', '张力', '治愈', '宿命感', '孤独', '希望']

// ---- 分镜枚举（KB spec §02 字段规范：英文枚举值 + 中文显示标签）----
// 与后端 db.py 字段 (shot_type / camera_movement / camera_angle / focal_length) 及知识库模型对齐
export const SHOT_TYPE_OPTIONS = [
  { value: 'extreme_close_up', label: '大特写' },
  { value: 'close_up', label: '特写' },
  { value: 'medium_close', label: '中近景' },
  { value: 'medium', label: '中景' },
  { value: 'medium_full', label: '中全景' },
  { value: 'full', label: '全景' },
  { value: 'long', label: '远景' },
  { value: 'extreme_long', label: '大远景' },
]
export const CAMERA_MOVEMENT_OPTIONS = [
  { value: 'static', label: '固定机位' },
  { value: 'pan', label: '摇镜' },
  { value: 'tilt', label: '升降' },
  { value: 'dolly', label: '推拉镜头' },
  { value: 'zoom', label: '变焦' },
  { value: 'handheld', label: '手持' },
  { value: 'aerial', label: '航拍' },
  { value: 'follow', label: '跟拍' },
]
export const CAMERA_ANGLE_OPTIONS = [
  { value: 'eye_level', label: '平视机位' },
  { value: 'low_angle', label: '低角度' },
  { value: 'high_angle', label: '俯视机位' },
  { value: 'dutch', label: '荷兰角' },
  { value: 'overhead', label: '顶视 / 航拍' },
  { value: 'shoulder', label: '过肩' },
  { value: 'pov', label: '主观视角' },
]
// §09 MEM-06 摄影机路径枚举（7 元，本地栈替代 Gemini 自动决策，可手动编辑）
export const CAMERA_PATH_OPTIONS = [
  { value: 'push_in', label: '推 (push_in)' },
  { value: 'pull_out', label: '拉 (pull_out)' },
  { value: 'pan_lr', label: '摇 (pan_lr)' },
  { value: 'track_lr', label: '移 (track_lr)' },
  { value: 'crane_up', label: '升降上 (crane_up)' },
  { value: 'crane_down', label: '升降下 (crane_down)' },
  { value: 'follow', label: '跟 (follow)' },
  { value: 'static', label: '固定 (static)' },
]
// §09 MEM-09 旁白音色枚举（本地 TTS 等价替代）
export const NARRATION_VOICE_OPTIONS = [
  { value: 'zh-CN-XiaoxiaoNeural', label: '晓晓（女声·温柔）' },
  { value: 'zh-CN-YunxiNeural', label: '云希（男声·沉稳）' },
  { value: 'zh-CN-YunyangNeural', label: '云扬（男声·解说）' },
  { value: 'zh-CN-XiaoyiNeural', label: '晓伊（女声·知性）' },
  { value: 'local_fallback', label: '本地降级（仅文本）' },
]
export const FOCAL_LENGTH_OPTIONS = [
  { value: '24mm', label: '24mm 广角' },
  { value: '35mm', label: '35mm 标准' },
  { value: '50mm', label: '50mm 中焦' },
  { value: '85mm', label: '85mm 人像' },
  { value: '135mm', label: '135mm 长焦' },
]
// 旧中文值 → KB 英文枚举（兼容历史数据迁移）
export const LEGACY_SHOT_MAP = {
  '大特写': 'extreme_close_up', '特写': 'close_up', '中近景': 'medium_close',
  '近景': 'medium_close', '中景': 'medium', '中全景': 'medium_full',
  '全景': 'full', '远景': 'long', '大远景': 'extreme_long',
}
export const LEGACY_ANGLE_MAP = {
  '平视机位': 'eye_level', '低角度': 'low_angle', '俯视机位': 'high_angle',
  '荷兰角': 'dutch', '顶视': 'overhead', '航拍': 'overhead',
  '过肩': 'shoulder', '主观视角': 'pov',
}
export const LEGACY_MOVE_MAP = {
  '固定机位': 'static', '固定': 'static', '推镜': 'dolly', '缓推': 'dolly',
  '拉镜': 'dolly', '缓慢环绕': 'pan', '摇镜': 'pan', '升降': 'tilt',
  '变焦': 'zoom', '手持': 'handheld', '航拍': 'aerial', '跟拍': 'follow',
}
const ALL_SHOT_VALUES = SHOT_TYPE_OPTIONS.map((o) => o.value)
const ALL_ANGLE_VALUES = CAMERA_ANGLE_OPTIONS.map((o) => o.value)
const ALL_MOVE_VALUES = CAMERA_MOVEMENT_OPTIONS.map((o) => o.value)
const ALL_FOCAL_VALUES = FOCAL_LENGTH_OPTIONS.map((o) => o.value)
const mapOr = (val, map, all, def) => {
  if (val === undefined || val === null || val === '') return def
  if (map[val]) return map[val]
  if (all.includes(val)) return val
  return def
}
// 将分镜字段原地归一化为 KB 英文枚举（兼容旧 camera_move 字段名与中文历史值）
export const normalizeShotEnums = (s) => {
  if (!s) return s
  if (s.camera_move !== undefined) {
    if (s.camera_movement === undefined) s.camera_movement = s.camera_move
    delete s.camera_move
  }
  s.shot_type = mapOr(s.shot_type, LEGACY_SHOT_MAP, ALL_SHOT_VALUES, 'medium')
  s.camera_movement = mapOr(s.camera_movement, LEGACY_MOVE_MAP, ALL_MOVE_VALUES, 'static')
  s.camera_angle = mapOr(s.camera_angle, LEGACY_ANGLE_MAP, ALL_ANGLE_VALUES, 'eye_level')
  s.focal_length = mapOr(s.focal_length, {}, ALL_FOCAL_VALUES, '35mm')
  return s
}

// ---- Phase 1 资产库枚举（单一来源，禁止散落硬编码）----
export const ASSET_TAB_OPTIONS = [
  { key: 'characters', label: '角色', type: 'role' },
  { key: 'scenes', label: '场景', type: 'scene' },
  { key: 'props', label: '道具', type: 'prop' },
]
export const ASSET_STAGE_OPTIONS = [
  { value: 'childhood', label: '童年期' },
  { value: 'juvenile', label: '少年期' },
  { value: 'youth', label: '青年期' },
  { value: 'middle_age', label: '中年期' },
  { value: 'old_age', label: '老年期' },
]
export const ASSET_POSE_OPTIONS = [
  { value: 'front', label: '正面' },
  { value: 'side', label: '侧面' },
  { value: 'back', label: '背面' },
  { value: 'full_body', label: '全身' },
  { value: 'custom', label: '自定义' },
]
export const ASSET_ROLE_TYPE_OPTIONS = [
  { value: 'protagonist', label: '主角' },
  { value: 'supporting', label: '配角' },
  { value: 'antagonist', label: '反派' },
]

// ---- 顶部模型下拉（结构同 GET /api/models）----
export const mockModels = [
  { group: '创作模型', items: OPTION_CREATION_MODELS.map((n) => ({ name: n, credit: 600 })) },
  { group: 'AI 视频模型', items: OPTION_AI_MODELS.map((n) => ({ name: n, credit: 800 })) },
]

// ---- 通知（顶栏铃铛）----
export const mockNotifications = [
  { id: 1, type: 'success', title: '「都市迷局」第 3 集已生成完成', time: '2 分钟前', read: false },
  { id: 2, type: 'info', title: '会员权益已升级至「至尊 VIP」', time: '1 小时前', read: false },
  { id: 3, type: 'warn', title: '「岁月的余温」指导板 4 张已就绪，待合成', time: '今天 09:12', read: true },
  { id: 4, type: 'info', title: '版本 v1.3.0 已发布：新增人生回忆录工作流', time: '昨天', read: true },
]

// ============================================================
// 短剧项目样例（核心页 1 · 片段编辑）
// ============================================================
export const mockProject = {
  id: 'demo-short',
  title: '都市迷局·第一季',
  subtitle: '第 3 集 · 暗流',
  creation_model: '创作旗舰 Pro',
  ai_model: 'Seedance 2.0 Pro',
  preset_id: 'flash-long',
  scene_description: '',
  resolution: '1080p',
  ratio: '16:9',
  duration: 10,
  audio: true,
  seed: -1,
  real_person: true,
  return_tail: false,
  credits: 2160,
  status: 'done',
  chapters: [
    { id: 'ch1', title: '片段一 · 咖啡馆偶遇', duration: 12, count: 4, thumb: '' },
    { id: 'ch2', title: '片段二 · 雨夜追踪', duration: 10, count: 3, thumb: '' },
    { id: 'ch3', title: '片段三 · 真相边缘', duration: 8, count: 3, thumb: '' },
  ],
  characters: [
    {
      id: 'c1', name: '林夏', relation: '主角',
      desc: '25 岁，刑侦支队新人，敏锐却冲动。',
      thumb: '', stages: [
        { stage_id: 'c1s1', stage_name: 'juvenile', age: 18, reference_image_url: '', poses: [] },
        { stage_id: 'c1s2', stage_name: 'youth', age: 23, reference_image_url: '', poses: [] },
        { stage_id: 'c1s3', stage_name: 'youth', age: 25, reference_image_url: '', poses: [] },
      ],
    },
    {
      id: 'c2', name: '顾沉', relation: '主角',
      desc: '32 岁，神秘投资人，亦正亦邪。',
      thumb: '', stages: [
        { stage_id: 'c2s1', stage_name: 'youth', age: 28, reference_image_url: '', poses: [] },
        { stage_id: 'c2s2', stage_name: 'middle_age', age: 32, reference_image_url: '', poses: [] },
      ],
    },
    {
      id: 'c3', name: '老周', relation: '配角',
      desc: '50 岁，林夏的师父，老刑警。',
      thumb: '', stages: [{ stage_id: 'c3s1', stage_name: 'middle_age', age: 50, reference_image_url: '', poses: [] }],
    },
    {
      id: 'c4', name: '苏蔓', relation: '配角',
      desc: '27 岁，咖啡馆老板，关键证人。',
      thumb: '', stages: [{ stage_id: 'c4s1', stage_name: 'youth', age: 27, reference_image_url: '', poses: [] }],
    },
  ],
  assets: {
    scenes: [
      { id: 's1', name: '深夜咖啡馆', type: 'scene', thumb: '' },
      { id: 's2', name: '天台雨夜', type: 'scene', thumb: '' },
      { id: 's3', name: '地下车库', type: 'scene', thumb: '' },
    ],
    props: [
      { id: 'p1', name: '银色怀表', type: 'prop', thumb: '' },
      { id: 'p2', name: '黑色公文包', type: 'prop', thumb: '' },
      { id: 'p3', name: '旧照片', type: 'prop', thumb: '' },
    ],
    images: [
      { id: 'i1', name: '林夏定妆', type: 'image', thumb: '' },
      { id: 'i2', name: '顾沉侧影', type: 'image', thumb: '' },
    ],
  },
  scenes: [
    { scene_id: 'sc1', chapter_id: 'ch1', no: 1, caption: '林夏推门入店', description: '雨夜，林夏推开咖啡馆门，铃声清脆。', prompt: 'cinematic, rainy night cafe interior, neon light, young female detective', negative_prompt: 'blurry, low quality', duration: 4, shot_type: 'medium_close', camera_movement: 'dolly', thumb: '', dialogue: '（画外）又是你……', refs: ['c1', 's1'], setting: '深夜咖啡馆，暖黄灯光' },
    { scene_id: 'sc2', chapter_id: 'ch1', no: 2, caption: '苏蔓递来照片', description: '苏蔓将一张旧照片滑过桌面。', prompt: 'close up, woman hand passing old photo, mysterious atmosphere', negative_prompt: 'extra hands', duration: 3, shot_type: 'close_up', camera_movement: 'static', thumb: '', dialogue: '这东西，你最好收好。', refs: ['c4', 'p3'], setting: '咖啡馆卡座' },
    { scene_id: 'sc3', chapter_id: 'ch1', no: 3, caption: '林夏凝视怀表', description: '林夏低头看银色怀表，神情凝重。', prompt: 'medium shot, female detective looking at silver pocket watch, dramatic lighting', negative_prompt: 'watermark', duration: 3, shot_type: 'medium', camera_movement: 'pan', thumb: '', dialogue: '', refs: ['c1', 'p1'], setting: '咖啡馆角落' },
    { scene_id: 'sc4', chapter_id: 'ch1', no: 4, caption: '门外阴影', description: '窗外闪过一道人影，林夏警觉。', prompt: 'silhouette behind window glass, rain, suspense', negative_prompt: 'cartoon', duration: 2, shot_type: 'full', camera_movement: 'dolly', thumb: '', dialogue: '', refs: ['s1'], setting: '咖啡馆窗边' },
    { scene_id: 'sc5', chapter_id: 'ch2', no: 1, caption: '天台对峙', description: '顾沉与林夏在天台雨中对峙。', prompt: 'rooftop rainy night, two figures confrontation, cinematic', negative_prompt: 'blurry', duration: 5, shot_type: 'long', camera_movement: 'aerial', thumb: '', dialogue: '你到底是谁？', refs: ['c1', 'c2', 's2'], setting: '天台雨夜' },
    { scene_id: 'sc6', chapter_id: 'ch2', no: 2, caption: '公文包落地', description: '黑色公文包从顾沉手中滑落。', prompt: 'black briefcase falling slow motion, rain splashes', negative_prompt: 'extra objects', duration: 3, shot_type: 'close_up', camera_movement: 'static', thumb: '', dialogue: '', refs: ['c2', 'p2'], setting: '天台' },
  ],
  palette: [
    { color: '#1a2233', name: '午夜蓝' },
    { color: '#ff4d8d', name: '霓虹粉' },
    { color: '#8a5cff', name: '神秘紫' },
    { color: '#3a2e2a', name: '咖啡棕' },
    { color: '#0e1016', name: '暗夜黑' },
  ],
  anchors: [
    { type: '人物锚点', text: '林夏 · 刑侦支队新人' },
    { type: '事实锚点', text: '怀表是案件关键证物' },
    { type: '事实锚点', text: '苏蔓为目击证人' },
  ],
  remote_task_id: '2075021591330562049',
  local_task_id: 'camrb0va90crqv0rthvpzx2mJ',
  video_url: '',
  gen_log: '> 任务已提交\n> 分镜 1/6 生成中…\n> 分镜 1 完成 (3.2s)\n> 分镜 2 完成 (2.9s)\n> 合成视频完成\n',
}

// ============================================================
// 回忆录项目样例（核心页 2 · 导演创意指导板）
// ============================================================
export const mockMemoir = {
  id: 'demo-memoir',
  title: '岁月的余温·高启强',
  subtitle: '已自动保存 · 16:9-720p · 60秒 · 4张指导板',
  person: { name: '高启强', born: '1968', summary: '从街边卖鱼到家族掌舵，一生沉浮。' },
  storyboard_count: 4,
  ratio: '16:9',
  resolution: '720p',
  strengthen_emotion: ['克制', '温暖', '怀旧'],
  negative_prompt: '避免出现现代高楼、电子设备、卡通风格',
  story: {
    setting: '改革开放至当代的南方小城，时代变迁下的家族记忆。',
    timeline: [
      { age: '20', event: '在菜市场卖鱼，初识命运转折' },
      { age: '35', event: '接手家族生意，迎來第一桶金' },
      { age: '50', event: '家族事业登顶，却渐感孤独' },
      { age: '65', event: '回到旧居，翻看老照片' },
    ],
    notes: '以克制温情的视角回望一生，避免戏剧化夸张。',
    environment: [
      { name: '菜市场', desc: '潮湿喧闹，鱼腥与烟火气' },
      { name: '老式客厅', desc: '木沙发、挂钟、泛黄照片墙' },
      { name: '江边步道', desc: '黄昏，微风，孤独剪影' },
    ],
    visual_cues: [
      { name: '机位一 · 低角度', desc: '贴近地面，表现压迫与挣扎' },
      { name: '机位二 · 过肩', desc: '对话场景，营造亲密' },
      { name: '机位三 · 远景剪影', desc: '孤独感，留白' },
    ],
    lighting_mood: '暖黄怀旧光，少量逆光勾勒轮廓，避免冷硬打光。',
    emotion_keywords: ['克制', '温暖', '怀旧', '宿命感', '治愈'],
    voice_narration: '那年我二十岁，以为一辈子就在鱼摊上过完了。',
  },
  characters: [
    { id: 'mc1', name: '青年启强', relation: '主角', thumb: '', stages: [{ stage_id: 'mcs1', stage_name: 'youth', age: 20, reference_image_url: '', poses: [] }, { stage_id: 'mcs2', stage_name: 'middle_age', age: 35, reference_image_url: '', poses: [] }] },
    { id: 'mc2', name: '晚年启强', relation: '主角', thumb: '', stages: [{ stage_id: 'mcs3', stage_name: 'old_age', age: 65, reference_image_url: '', poses: [] }] },
    { id: 'mc3', name: '阿菊', relation: '配角', thumb: '', stages: [{ stage_id: 'mcs4', stage_name: 'youth', age: 22, reference_image_url: '', poses: [] }] },
  ],
  // 指导板分镜表
  scenes: [
    { no: 1, shot_type: 'medium_close', camera_movement: 'static', duration: 4, visual: '菜市场鱼摊，青年启强低头刮鳞。', narration: '二十岁那年，我守着一方鱼摊。', thumb: '', emotion: '克制' },
    { no: 2, shot_type: 'medium', camera_movement: 'dolly', duration: 4, visual: '老式客厅，阿菊递来一碗汤。', narration: '她说，日子会好起来的。', thumb: '', emotion: '温暖' },
    { no: 3, shot_type: 'long', camera_movement: 'aerial', duration: 4, visual: '江边黄昏，中年启强独行。', narration: '登顶之后，竟是更深的孤独。', thumb: '', emotion: '宿命感' },
    { no: 4, shot_type: 'close_up', camera_movement: 'static', duration: 4, visual: '晚年启强翻看旧照片，手指微颤。', narration: '原来最想回的，是最穷的那年。', thumb: '', emotion: '怀旧' },
  ],
  palette: [
    { color: '#caa472', name: '旧时光黄' },
    { color: '#7a5b43', name: '木色' },
    { color: '#2e3b4e', name: '黄昏蓝' },
    { color: '#b5503f', name: '砖红' },
    { color: '#1c1a17', name: '暗褐' },
  ],
  anchors: [
    { type: '人物锚点', text: '高启强 · 从卖鱼到掌舵' },
    { type: '事实锚点', text: '阿菊为其青梅竹马' },
    { type: '事实锚点', text: '时间线：20 / 35 / 50 / 65 岁' },
  ],
  output_params: {
    ratio: '16:9', spec: '720p · H.264 · MP4', boards: 4, total_duration: 60, subtitle: '简体中文 · 底部居中',
    models: {
      plan: '创作旗舰 Pro', board: '创作旗舰 Flash·长', video: 'Seedance 2.0 Pro', audio: '云端配音 · 男中音',
    },
  },
  checks: [
    { label: '人物参考照已上传', ok: true },
    { label: '时间轴事件完整', ok: true },
    { label: '强化情绪标签已确认', ok: true },
    { label: '反向提示词已填写', ok: false },
  ],
  reference_photos: [
    { id: 'r1', name: '青年照', thumb: '' },
    { id: 'r2', name: '全家福', thumb: '' },
  ],
  video_url: '',
  gen_log: '> 回忆录项目已就绪\n> 指导板 4 张生成完成\n> 等待合成全集\n',
}

// ============================================================
// 首页近期项目
// ============================================================
export const mockHome = {
  recent: [
    { id: 'demo-short', title: '都市迷局·第一季', status: 'done', scene_count: 6, type: '短剧', cover: '' },
    { id: 'demo-memoir', title: '岁月的余温·高启强', status: 'rendering', scene_count: 4, type: '回忆录', cover: '' },
    { id: 'p3', title: '山海异闻录', status: 'planning', scene_count: 0, type: '短剧', cover: '' },
    { id: 'p4', title: '夏日告白计划', status: 'draft', scene_count: 2, type: '短剧', cover: '' },
  ],
  templates: [
    { icon: '🎬', name: '都市悬疑短剧', desc: '快节奏反转，适合竖屏' },
    { icon: '📖', name: '人生回忆录', desc: '老照片 + 温情旁白' },
    { icon: '💡', name: '知识科普', desc: '把一个知识点讲透' },
    { icon: '🎉', name: '节日祝福', desc: '生日 / 新年短视频' },
  ],
}

// ============================================================
// 任务中心（Mock）
// ============================================================
export const mockTasks = [
  { id: 't1', title: '都市迷局 第3集 · 全集合成', thumb: '', status: 'generating', progress: 42, params: { model: 'Seedance 2.0 Pro', resolution: '1080p', ratio: '16:9', duration: '60s' }, created_at: '刚刚' },
  { id: 't2', title: '岁月的余温 · 指导板 1', thumb: '', status: 'done', progress: 100, params: { model: '创作旗舰 Flash·长', resolution: '720p', ratio: '16:9', duration: '15s' }, created_at: '10 分钟前' },
  { id: 't3', title: '山海异闻录 · 片头', thumb: '', status: 'queued', progress: 0, params: { model: '可灵 1.6', resolution: '1080p', ratio: '9:16', duration: '8s' }, created_at: '12 分钟前' },
  { id: 't4', title: '夏日告白 · 分镜 3', thumb: '', status: 'failed', progress: 64, params: { model: '即梦视频 3.0', resolution: '720p', ratio: '1:1', duration: '5s' }, created_at: '20 分钟前' },
  { id: 't5', title: '都市迷局 第2集 · 全集合成', thumb: '', status: 'done', progress: 100, params: { model: 'Seedance 2.0 Pro', resolution: '1080p', ratio: '16:9', duration: '55s' }, created_at: '1 小时前' },
]
