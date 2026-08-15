// 万能短剧 · 页面自定义 · 默认页面集（忠实复刻真实应用 19 个导航项 + 各自内部页签/弹窗）
// 左导航(sidebar) 完全镜像真实 src/components/SideBar.vue 的 7 组 19 项；
// 每个真实页面含：page-head(真实标题) + 真实内部 tab-row(switchSubpage) + subpage 面板(每页签一个) + 真实 dialog；
// 所有元素用 var(--*) 主题变量，全可选中编辑；切页时 index.vue 会把激活页签重置为该页首个 subpage。
// ============ 真实导航（镜像 SideBar.vue） ============
const REAL_NAV = [
  { group: '主导航', items: [
    { id: 'home', label: '首页' },
    { id: 'projects', label: '项目中心' },
    { id: 'tasks', label: '推理监控' },
  ] },
  { group: '页面自定义', items: [ { id: 'custom', label: '页面自定义' } ] },
  { group: 'AI 创作', items: [
    { id: 'txt2img', label: '文生图' },
    { id: 'img2img', label: '图生图' },
    { id: 'creative', label: '创意工坊' },
    { id: 'memoir', label: '人生回忆录' },
  ] },
  { group: '资源管理', items: [
    { id: 'global-assets', label: '全局资产库' },
    { id: 'img2prompt', label: '图片反推' },
    { id: 'lora-train', label: 'LoRA 训练' },
    { id: 'history', label: '历史记录' },
  ] },
  { group: '账户服务', items: [
    { id: 'membership', label: '会员中心' },
    { id: 'messages', label: '消息中心' },
    { id: 'help', label: '帮助与反馈' },
  ] },
  { group: '数据查询', items: [ { id: 'my-data', label: '我的数据' } ] },
  { group: '系统', items: [
    { id: 'logs', label: '操作日志' },
    { id: 'settings', label: '设置' },
    { id: 'system', label: '服务与版本' },
  ] },
]

// ============ 通用样式 ============
const cardStyle = { background: 'var(--card)', border: '1px solid var(--line)', borderRadius: '16px', padding: '20px' }
const gridAuto = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '18px', marginBottom: '24px' }
const grid3 = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '18px', marginBottom: '24px' }

// ============ 元素构造助手 ============
function card(id, title, children) { return { type: 'card', id, props: { title }, style: cardStyle, children } }
function stat(id, title, value, sub, unit) {
  return { type: 'statcard', id, targetSelector: '.stat-card', props: { title, value, unit: unit || '', sub: sub || '' }, style: cardStyle }
}
function statGrid(idp, arr) {
  return { type: 'row', id: idp, style: gridAuto, children: arr.map((s) => stat(s.id, s.title, s.value, s.sub, s.unit)) }
}
function btn(id, content, at, target, msg) {
  return { type: 'button', id, targetSelector: '.btn-primary', props: { content, actionType: at || 'toast', actionTarget: target || '', actionMessage: msg || '' }, style: { background: 'var(--pur)', color: '#fff', border: '0', borderRadius: '8px', padding: '8px 18px', fontWeight: '700' } }
}
function gbtn(id, content, at, target, msg) {
  return { type: 'button', id, props: { content, actionType: at || 'toast', actionTarget: target || '', actionMessage: msg || '' }, style: { background: 'var(--card)', color: 'var(--txt)', border: '1px solid var(--line)', borderRadius: '8px', padding: '8px 14px' } }
}
function inp(id, ph) {
  return { type: 'input', id, props: { placeholder: ph }, style: { width: '100%', border: '1px solid var(--line)', borderRadius: '8px', padding: '8px 12px', background: 'var(--card)', color: 'var(--txt)', boxSizing: 'border-box' } }
}
function txt(id, content, style) { return { type: 'text', id, props: { content }, style: style || { fontSize: '13px', color: 'var(--sub)', lineHeight: '1.7' } } }
function tag(id, content, variant) { return { type: 'tag', id, props: { content, variant: variant || 'default' }, style: { marginRight: '6px' } } }
function progress(id, percent, color) { return { type: 'progress', id, props: { percent, color }, style: {} } }
function img(id, label) {
  return { type: 'img', id, props: { src: '' }, style: { width: '100%', height: '110px', background: 'var(--card-2)', borderRadius: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--sub)', fontSize: '12px' } }
}
function field(id, label, ph) {
  return { type: 'row', id: id + '-row', style: { display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px' },
    children: [ txt(id + '-l', label, { fontSize: '13px', color: 'var(--sub)' }), inp(id, ph) ] }
}
// 左侧导航：完全镜像真实 SideBar（7 组 19 项）
function buildSidebar() {
  const children = [
    { type: 'text', id: 'side-logo', props: { content: '◆ 万能短剧' }, style: { fontSize: '16px', fontWeight: '800', color: 'var(--txt)', fontFamily: 'var(--font-display)', padding: '4px 8px 12px' } },
  ]
  REAL_NAV.forEach((g, gi) => {
    children.push({ type: 'text', id: 'grp-' + gi, props: { content: g.group }, style: { fontSize: '11px', color: 'var(--sub)', padding: '16px 8px 8px', letterSpacing: '1px', fontWeight: '700' } })
    g.items.forEach((it) => {
      children.push({ type: 'menuitem', id: 'sid-' + it.id, props: { content: it.label, actionType: 'switchPage', actionTarget: it.id }, style: { color: 'var(--sub)', fontSize: '14px', padding: '10px 12px', borderRadius: '8px' } })
    })
  })
  return { type: 'sidebar', id: 'side', targetSelector: '.sidebar', style: { width: '220px', flex: '0 0 220px', background: 'var(--panel)', borderRight: '1px solid var(--line)', padding: '16px 10px', display: 'flex', flexDirection: 'column', gap: '4px', borderRadius: '0' }, children }
}
// 页面外壳：page > sidebar + content(col)
function pageShell(id, contentChildren) {
  return { type: 'page', id, style: { display: 'flex', flexDirection: 'row', background: 'var(--bg)', minHeight: '100%', gap: '0' },
    children: [ buildSidebar(), { type: 'col', id: 'content-' + id, style: { flex: '1', minWidth: '0', display: 'flex', flexDirection: 'column', padding: '24px', gap: '0' }, children: contentChildren } ] }
}
// 页头：真实 .page-head（标题 + 操作按钮），始终可见（在 tab 之上）
function head(id, title, actions) {
  return { type: 'row', id: 'head-' + id, style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '20px', marginBottom: '20px', flexWrap: 'wrap' },
    children: [
      { type: 'title', id: id + '-title', targetSelector: '.p5-title', props: { content: title, level: 1 }, style: { fontSize: '24px', fontWeight: '800', color: 'var(--txt)' } },
      { type: 'row', id: 'head-actions-' + id, style: { display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }, children: actions || [] },
    ] }
}
// 页签行：一组 menuitem（switchSubpage）=> 对应 subpage 面板
function tabRow(tabs, rowId) {
  return { type: 'row', id: rowId || 'tabrow', style: { display: 'flex', gap: '6px', background: 'var(--card)', border: '1px solid var(--line)', borderRadius: '10px', padding: '4px', marginBottom: '18px', flexWrap: 'wrap' },
    children: tabs.map((t) => ({ type: 'menuitem', id: 'tab-' + t.id, props: { content: t.label, actionType: 'switchSubpage', actionTarget: t.id }, style: { color: 'var(--sub)', fontSize: '13px', padding: '6px 12px', borderRadius: '8px' } })) }
}
// 子页面面板（页签内容）
function sub(id, title, children) {
  return { type: 'subpage', id: 'sub-' + id, props: { title, subpageId: id }, style: { ...cardStyle, marginTop: '4px' }, children }
}
// 弹窗
function dialog(id, title, dialogId, children, w) {
  return { type: 'dialog', id, props: { title, dialogId, width: w || '480px' }, style: { ...cardStyle, width: w || '480px', maxWidth: '90vw' }, children }
}
// 有页签的页面
function tabPage(id, title, tabs, dialogs, actions) {
  const tabRowNode = tabRow(tabs.map((t) => ({ id: t.id, label: t.label })))
  const subpages = tabs.map((t) => sub(t.id, t.label, t.children))
  const dialogNodes = (dialogs || []).map((d) => (d && d.type === 'dialog' ? d : dialog(d.id, d.title, d.dialogId, d.children, d.w)))
  return pageShell(id, [ head(id, title, actions), tabRowNode, ...subpages, ...dialogNodes ])
}
// 无页签的页面
function plainPage(id, title, children, actions) {
  return pageShell(id, [ head(id, title, actions), ...children ])
}

// ============ 各真实页面 ============

// 1) 首页（无内部页签）
function buildHome() {
  const p = plainPage('home', '创作驾驶舱', [
    statGrid('home-stats', [
      { id: 'h-st1', title: '总项目数', value: '4', sub: '剧本/回忆录总数' },
      { id: 'h-st2', title: '今日 AI 调用', value: '0', sub: 'provider_calls' },
      { id: 'h-st3', title: '今日成功任务', value: '0', sub: 'cloud_jobs_succeeded' },
      { id: 'h-st4', title: '本月积分消耗', value: '0', sub: 'credit_ledger' },
      { id: 'h-st5', title: '本月厂商成本', value: '¥0.00', sub: 'provider_cost' },
      { id: 'h-st6', title: '7日活跃设备', value: '1', sub: '实时设备心跳' },
    ]),
    { type: 'row', id: 'home-cards', style: grid3, children: [
      card('home-recent', '最近项目', [
        { type: 'menuitem', id: 'h-rp1', props: { content: '示例短剧 · 12 镜头 · 草稿', actionType: 'switchPage', actionTarget: 'projects' }, style: { color: 'var(--txt)', fontSize: '14px', padding: '10px 0', borderBottom: '1px solid var(--line)', display: 'block' } },
        { type: 'menuitem', id: 'h-rp2', props: { content: '示例回忆录 · 8 镜头 · 草稿', actionType: 'switchPage', actionTarget: 'projects' }, style: { color: 'var(--txt)', fontSize: '14px', padding: '10px 0', display: 'block' } },
      ]),
      card('home-fail', '最近失败任务', [ txt('h-ft', '暂无失败任务', { fontSize: '13px', color: 'var(--sub)' }) ]),
      card('home-prov', '厂商健康状态', [ tag('h-pv1', 'Ollama 健康', 'green'), tag('h-pv2', 'ComfyUI 未配置') ]),
    ] },
  ], [ inp('home-idea', '一句话开启 AI 短剧…'), btn('home-create', '创作', 'openDialog', 'home-dlg'), gbtn('home-new', '＋ 新建项目', 'toast', '', '已创建示例项目（沙盒模拟）'), tag('home-on', 'ONLINE', 'green') ])
  p.children[1].children.push(
    dialog('home-dlg', '新建项目（沙盒模拟）', 'home-dlg', [
      txt('h-dlg-t', '这是弹窗内部内容，可在编辑模式下直接选中并修改样式。', { fontSize: '14px', color: 'var(--sub)', lineHeight: '1.6' }),
      { type: 'row', id: 'h-dlg-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('h-dlg-c', '关闭', 'closeDialog', 'home-dlg'), btn('h-dlg-ok', '确认', 'closeDialog', 'home-dlg') ] },
    ])
  )
  return p
}

// 2) 项目中心（真实内部页签：全部/短剧/回忆录/收藏/草稿 + 网格/列表 + 新建项目弹窗）
function buildProjects() {
  const pjCard = (id, name, meta) => card('pj-' + id, name, [
    txt('pj-' + id + '-t', meta, { fontSize: '13px', color: 'var(--sub)' }),
    { type: 'row', id: 'pj-' + id + '-a', style: { display: 'flex', gap: '8px', marginTop: '12px' }, children: [ btn('pj-open-' + id, '打开', 'switchSubpage', 'pj-detail'), gbtn('pj-edit-' + id, '编辑', 'toast', '', '进入编辑（模拟）') ] },
  ])
  const filters = [
    { id: 'pj-all', label: '全部', children: [ { type: 'row', id: 'pj-all-g', style: grid3, children: [ pjCard('c1', '示例短剧', '12 镜头 · 草稿'), pjCard('c2', '示例回忆录', '8 镜头 · 草稿'), pjCard('c3', '品牌广告片', '24 镜头 · 进行中'), pjCard('c4', '都市迷局', '16 镜头 · 已完成'), pjCard('c5', '岁月的余温', '10 镜头 · 草稿'), pjCard('c6', '星河彼岸', '20 镜头 · 进行中') ] } ] },
    { id: 'pj-short', label: '短剧', children: [ { type: 'row', id: 'pj-short-g', style: grid3, children: [ pjCard('s1', '示例短剧', '12 镜头 · 草稿'), pjCard('s2', '品牌广告片', '24 镜头 · 进行中'), pjCard('s3', '都市迷局', '16 镜头 · 已完成') ] } ] },
    { id: 'pj-memoir', label: '回忆录', children: [ { type: 'row', id: 'pj-mem-g', style: grid3, children: [ pjCard('m1', '示例回忆录', '8 镜头 · 草稿'), pjCard('m2', '岁月的余温', '10 镜头 · 草稿') ] } ] },
    { id: 'pj-fav', label: '收藏', children: [ { type: 'row', id: 'pj-fav-g', style: grid3, children: [ pjCard('f1', '星河彼岸', '20 镜头 · 进行中') ] } ] },
    { id: 'pj-draft', label: '草稿', children: [ { type: 'row', id: 'pj-draft-g', style: grid3, children: [ pjCard('d1', '示例短剧', '12 镜头 · 草稿'), pjCard('d2', '示例回忆录', '8 镜头 · 草稿'), pjCard('d3', '岁月的余温', '10 镜头 · 草稿') ] } ] },
  ]
  const viewTabs = [
    { id: 'pj-grid', label: '▦ 网格', children: [ { type: 'row', id: 'pj-grid-g', style: grid3, children: [ pjCard('g1', '示例短剧', '12 镜头 · 草稿'), pjCard('g2', '示例回忆录', '8 镜头 · 草稿') ] } ] },
    { id: 'pj-list', label: '☰ 列表', children: [ { type: 'card', id: 'pj-list-card', props: { title: '列表视图' }, style: cardStyle, children: [ txt('pj-list-t', '示例短剧 · 12 镜头 · 草稿 · 删除', { fontSize: '14px', color: 'var(--sub)' }), txt('pj-list-t2', '示例回忆录 · 8 镜头 · 草稿 · 删除', { fontSize: '14px', color: 'var(--sub)' }) ] } ] },
  ]
  const dialogs = [
    dialog('pj-dlg-new', '新建项目', 'pj-new', [
      { type: 'row', id: 'pj-type-row', style: { display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: '12px', marginBottom: '16px' }, children: [
        { type: 'menuitem', id: 'tab-pj-type-short', props: { content: '🎬 短剧', actionType: 'switchSubpage', actionTarget: 'pj-type-short' }, style: { background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: '12px', padding: '16px', cursor: 'pointer' } },
        { type: 'menuitem', id: 'tab-pj-type-memoir', props: { content: '📖 回忆录', actionType: 'switchSubpage', actionTarget: 'pj-type-memoir' }, style: { background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: '12px', padding: '16px', cursor: 'pointer' } },
      ] },
      field('pj-name', '项目名称', '给项目起个名字'),
      field('pj-preset', '预设套餐', '创作旗舰 Flash·长'),
      field('pj-desc', '项目描述（选填）', '一句话描述这个项目'),
      field('pj-ratio', '画面比例', '16:9 横屏'),
      field('pj-res', '分辨率', '720p'),
      { type: 'row', id: 'pj-new-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('pj-new-c', '取消', 'closeDialog', 'pj-new'), btn('pj-new-ok', '立即创建', 'closeDialog', 'pj-new', '已创建项目（沙盒模拟）') ] },
    ], '720px'),
    dialog('pj-dlg-hist', '浏览历史资产', 'pj-hist', [
      txt('pj-hist-t', '从已有项目沉淀的长期记忆中，带入角色卡 / 风格锚点 / LoRA。', { fontSize: '13px', color: 'var(--sub)' }),
      { type: 'row', id: 'pj-hist-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ btn('pj-hist-ok', '确认选择', 'closeDialog', 'pj-hist') ] },
    ], '640px'),
  ]
  // 把类型页签也作为子页面，避免嵌套：用 sub 承载（与项目中心同页）
  filters.push({ id: 'pj-detail', label: '项目详情', children: [ card('pj-detail-card', '项目详情（子页面）', [ txt('pj-detail-t', '展示项目内分镜、角色与生成记录，激活后可编辑任意元素。', { fontSize: '14px', color: 'var(--sub)' }), gbtn('pj-detail-back', '返回列表', 'switchSubpage', 'pj-all') ]) ] })
  const allTabs = [ ...filters, ...viewTabs, { id: 'pj-type-short', label: '短剧参数', children: [ card('pj-ts', '短剧参数', [ field('pj-ts-model', 'AI 视频模型', 'seedance-2.0-pro'), field('pj-ts-dur', '片段时长', '12 秒') ]) ] }, { id: 'pj-type-memoir', label: '回忆录参数', children: [ card('pj-tm', '回忆录参数', [ field('pj-tm-board', '预计故事板数', '6 板') ]) ] } ]
  return tabPage('projects', '我的项目', allTabs, dialogs, [ inp('pj-search', '搜索项目名…'), btn('pj-new-btn', '＋ 新建项目', 'openDialog', 'pj-new'), gbtn('pj-hist-btn', '📦 浏览历史资产', 'openDialog', 'pj-hist') ])
}

// 3) 推理监控（真实页签：全部/排队中/生成中/已完成/失败）
function buildTasks() {
  const taskCard = (id, name, statusTag, pct, color) => card('tk-' + id, name, [
    { type: 'row', id: 'tk-r-' + id, style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }, children: [ tag('tk-tag-' + id, statusTag.label, statusTag.variant), progress('tk-p-' + id, pct, color), gbtn('tk-log-' + id, '查看日志', 'openDialog', 'tk-log') ] },
  ])
  const mk = (id, label, cards) => ({ id, label, children: [ { type: 'row', id: 'tk-g-' + id, style: { display: 'flex', flexDirection: 'column', gap: '14px' }, children: cards } ] })
  const tabs = [
    mk('tk-all', '全部', [ taskCard('a1', '任务 a1b2 · 分镜生成', { label: '已完成', variant: 'green' }, 100, 'var(--green)'), taskCard('a2', '任务 c3d4 · 口型合成', { label: '失败', variant: 'default' }, 42, 'var(--warn)'), taskCard('a3', '任务 e5f6 · 成片渲染', { label: '生成中', variant: 'cyan' }, 68, 'var(--pur)') ]),
    mk('tk-queue', '排队中', [ txt('tk-q', '当前没有排队的任务', { fontSize: '13px', color: 'var(--sub)' }) ]),
    mk('tk-gen', '生成中', [ taskCard('g1', '任务 e5f6 · 成片渲染', { label: '生成中', variant: 'cyan' }, 68, 'var(--pur)') ]),
    mk('tk-done', '已完成', [ taskCard('d1', '任务 a1b2 · 分镜生成', { label: '已完成', variant: 'green' }, 100, 'var(--green)') ]),
    mk('tk-fail', '失败', [ taskCard('f1', '任务 c3d4 · 口型合成', { label: '失败', variant: 'default' }, 42, 'var(--warn)') ]),
  ]
  const p = tabPage('tasks', '推理监控', tabs, [
    dialog('tk-dlg-log', '任务日志', 'tk-log', [
      txt('tk-log-t', '[12:01] 任务开始\n[12:01] 拉取模型权重…\n[12:02] 推理完成 (cost 12 积分)\n[12:02] 输出写入对象存储', { fontSize: '13px', color: 'var(--sub)', whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)', lineHeight: '1.7' }),
      { type: 'row', id: 'tk-log-a', style: { display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }, children: [ gbtn('tk-log-c', '关闭', 'closeDialog', 'tk-log') ] },
    ], '560px'),
  ], [ inp('tk-search', '搜索任务 ID…'), gbtn('tk-refresh', '刷新', 'toast', '', '已刷新任务列表（沙盒模拟）') ])
  p.children[1].children.unshift(statGrid('tk-stats', [
    { id: 'tk-s1', title: '队列深度', value: '0', sub: '等待中' },
    { id: 'tk-s2', title: '生成中', value: '0', sub: '进行中' },
    { id: 'tk-s3', title: '今日成功', value: '0', sub: 'cloud_jobs_succeeded' },
    { id: 'tk-s4', title: '今日失败', value: '0', sub: 'cloud_jobs_failed' },
  ]))
  return p
}

// 4) 页面自定义（即本模块自身，无真实内部页签）
function buildCustom() {
  const groupCards = REAL_NAV.map((g, gi) => card('cus-g-' + gi, g.group + '（' + g.items.length + '）', [
    ...g.items.map((it) => ({
      type: 'menuitem', id: 'cus-go-' + it.id,
      props: { content: it.label + ' →', actionType: 'switchPage', actionTarget: it.id },
      style: { color: 'var(--txt)', fontSize: '14px', padding: '6px 0', display: 'block', cursor: 'pointer' },
    })),
    { type: 'row', id: 'cus-g-a-' + gi, style: { display: 'flex', gap: '8px', marginTop: '10px' }, children: [ btn('cus-enter-' + gi, '进入沙盒', 'switchPage', g.items[0].id), gbtn('cus-theme-' + gi, '定制主题', 'toast', '', '进入主题定制（沙盒模拟）') ] },
  ]))
  return plainPage('custom', '页面自定义', [
    card('cus-intro', '可视化页面与主题定制', [
      txt('cus-t1', '你正在使用「页面自定义」模块。左侧导航完整镜像真实应用（7 组 19 个页面），点选任意页面即可进入其沙盒。', { fontSize: '14px', color: 'var(--sub)' }),
      txt('cus-t2', '进入编辑模式后，可点选页面内任意元素（标题/按钮/输入框/弹窗/页签/卡片）修改文字与样式；可把具体颜色保存为「自定义主题」并在设置页启用。', { fontSize: '14px', color: 'var(--sub)', marginTop: '8px' }),
    ]),
    { type: 'row', id: 'cus-filter', style: { display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '18px', flexWrap: 'wrap' }, children: [ inp('cus-search', '搜索页面 / 功能…'), gbtn('cus-reset', '重置筛选', 'toast', '', '已重置筛选（沙盒模拟）') ] },
    { type: 'row', id: 'cus-grid', style: grid3, children: groupCards },
    { type: 'subpage', id: 'sub-cus-help', props: { title: '使用说明', subpageId: 'cus-help' }, style: { ...cardStyle, marginTop: '20px' }, children: [
      txt('cus-help-t', '① 预览模式点左侧导航切换真实页面；② 点「编辑当前页面」进入编辑；③ 点元素→右侧改属性；④「保存为自定义主题」。', { fontSize: '14px', color: 'var(--sub)', lineHeight: '1.8' }),
    ] },
  ], [ btn('cus-apply', '应用当前主题', 'toast', '', '已应用主题（沙盒模拟）') ])
}

// 5) 文生图（页签：文生图/漫画创作 + 本次结果/历史记录）
function buildTxt2img() {
  const tabs = [
    { id: 't2i-main', label: '文生图', children: [ card('t2i-card', '文生图参数', [ field('t2i-prompt', '正向提示词', '描述你想要的画面…'), field('t2i-neg', '负向提示词', '模糊/畸形'), field('t2i-size', '尺寸', '768×1280'), field('t2i-steps', '步数', '30') ]) ] },
    { id: 't2i-comic', label: '漫画创作', children: [ card('t2i-comic-card', '漫画创作参数', [ field('t2i-c-story', '剧情梗概', '一段分镜脚本…'), field('t2i-c-style', '画风', '美漫/日漫') ]) ] },
    { id: 't2i-res', label: '本次结果', children: [ { type: 'row', id: 't2i-res-g', style: grid3, children: [ card('t2i-r1', '结果 1', [ img('t2i-i1', '封面占位') ]), card('t2i-r2', '结果 2', [ img('t2i-i2', '封面占位') ]) ] } ] },
    { id: 't2i-hist', label: '历史记录', children: [ card('t2i-h-card', '历史记录', [ txt('t2i-h-t', '（历史生成记录占位 · 沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }) ]) ] },
  ]
  return tabPage('txt2img', '文生图', tabs, [
    dialog('t2i-dlg', '预览', 't2i-preview', [ img('t2i-pv', '预览大图占位'), { type: 'row', id: 't2i-pv-a', style: { display: 'flex', justifyContent: 'flex-end', marginTop: '12px' }, children: [ gbtn('t2i-pv-c', '关闭', 'closeDialog', 't2i-preview') ] } ], '640px'),
  ], [ btn('t2i-gen', '生成', 'toast', '', '已提交生成（沙盒模拟）'), gbtn('t2i-pv-btn', '预览', 'openDialog', 't2i-preview') ])
}

// 6) 图生图（页签：图生图/局部重绘/扩图/高清放大/面部修复/风格迁移）
function buildImg2img() {
  const modes = ['图生图', '局部重绘', '扩图', '高清放大', '面部修复', '风格迁移']
  const tabs = modes.map((m, i) => ({ id: 'im-' + i, label: m, children: [ card('im-card-' + i, m + ' 参数', [ field('im-src-' + i, '参考图', '上传参考图 URL'), field('im-prompt-' + i, '提示词', '描述目标效果…'), field('im-strength-' + i, '重绘幅度', '0.6') ]) ] }))
  return tabPage('img2img', '图生图', tabs, [
    dialog('im-dlg', '预览', 'im-preview', [ img('im-pv', '预览大图占位'), { type: 'row', id: 'im-pv-a', style: { display: 'flex', justifyContent: 'flex-end', marginTop: '12px' }, children: [ gbtn('im-pv-c', '关闭', 'closeDialog', 'im-preview') ] } ], '640px'),
  ], [ btn('im-gen', '生成', 'toast', '', '已提交生成（沙盒模拟）'), gbtn('im-pv-btn', '预览', 'openDialog', 'im-preview') ])
}

// 7) 创意工坊（页签：全部/剧情模板/广告模板/节日模板/知识科普）
function buildCreative() {
  const tmpl = (id, name) => card('cr-' + id, name, [ txt('cr-' + id + '-t', '模板简介占位（沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }), { type: 'row', id: 'cr-' + id + '-a', style: { display: 'flex', gap: '8px', marginTop: '12px' }, children: [ btn('cr-use-' + id, '使用模板', 'toast', '', '已载入模板（沙盒模拟）') ] } ])
  const tabs = [
    { id: 'cr-all', label: '全部', children: [ { type: 'row', id: 'cr-all-g', style: grid3, children: [ tmpl('a1', '逆袭短剧'), tmpl('a2', '新品发布会'), tmpl('a3', '春节祝福'), tmpl('a4', '科普冷知识') ] } ] },
    { id: 'cr-story', label: '剧情模板', children: [ { type: 'row', id: 'cr-story-g', style: grid3, children: [ tmpl('s1', '逆袭短剧'), tmpl('s2', '甜宠日常') ] } ] },
    { id: 'cr-ad', label: '广告模板', children: [ { type: 'row', id: 'cr-ad-g', style: grid3, children: [ tmpl('d1', '新品发布会'), tmpl('d2', '品牌种草') ] } ] },
    { id: 'cr-fest', label: '节日模板', children: [ { type: 'row', id: 'cr-fest-g', style: grid3, children: [ tmpl('f1', '春节祝福'), tmpl('f2', '中秋贺卡') ] } ] },
    { id: 'cr-know', label: '知识科普', children: [ { type: 'row', id: 'cr-know-g', style: grid3, children: [ tmpl('k1', '科普冷知识'), tmpl('k2', '历史典故') ] } ] },
  ]
  return tabPage('creative', '创意工坊', tabs, [
    dialog('cr-dlg', '模板详情', 'cr-detail', [ txt('cr-dlg-t', '模板详情（动态名称）', { fontSize: '14px', color: 'var(--sub)' }), { type: 'row', id: 'cr-dlg-a', style: { display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }, children: [ btn('cr-dlg-ok', '使用此模板', 'closeDialog', 'cr-detail', '已载入模板（沙盒模拟）') ] } ], '520px'),
  ], [ inp('cr-search', '搜索模板…'), btn('cr-new', '新建模板', 'toast', '', '已进入模板编辑器（沙盒模拟）') ])
}

// 8) 人生回忆录（页签：列表 + 导演板 6 阶段：资料/策划/指导板/审片/视频/合成，扁平顶层页签）
function buildMemoir() {
  const memCard = (id, name) => card('mem-' + id, name, [ txt('mem-' + id + '-t', '8 镜头 · 草稿', { fontSize: '13px', color: 'var(--sub)' }), { type: 'row', id: 'mem-' + id + '-a', style: { display: 'flex', gap: '8px', marginTop: '12px' }, children: [ btn('mem-open-' + id, '打开', 'toast', '', '进入回忆录（沙盒模拟）'), gbtn('mem-edit-' + id, '编辑', 'toast', '', '进入编辑（模拟）') ] } ])
  const stages = ['资料', '策划', '指导板', '审片', '视频', '合成']
  const stageTabs = stages.map((s, i) => ({ id: 'mem-stage-' + i, label: s, children: [ card('mem-stage-card-' + i, s + '（导演板）', [ txt('mem-stage-t-' + i, s + '阶段内容占位（沙盒模拟），激活后可编辑任意元素。', { fontSize: '14px', color: 'var(--sub)' }) ]) ] }))
  const tabs = [
    { id: 'mem-list', label: '列表', children: [ { type: 'row', id: 'mem-list-g', style: grid3, children: [ memCard('m1', '示例回忆录'), memCard('m2', '岁月的余温'), memCard('m3', '童年时光') ] } ] },
    ...stageTabs,
  ]
  return tabPage('memoir', '人生回忆录', tabs, [], [ btn('mem-new', '新建回忆录', 'toast', '', '已创建回忆录（沙盒模拟）') ])
}

// 9) 全局资产库（页签：角色/场景/道具/音色 + 弹窗：编辑/新建/导入/编辑角色）
function buildGlobalAssets() {
  const assetCard = (id, name) => card('ga-' + id, name, [ img('ga-i-' + id, '封面占位'), txt('ga-cap-' + id, '元数据占位', { fontSize: '12px', color: 'var(--sub)', marginTop: '8px', textAlign: 'center' }) ])
  const mk = (id, label, cards) => ({ id, label, children: [ { type: 'row', id: 'ga-g-' + id, style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px,1fr))', gap: '16px' }, children: cards } ] })
  const tabs = [
    mk('ga-role', '角色', [ assetCard('r1', '角色 · 林深'), assetCard('r2', '角色 · 苏晚'), assetCard('r3', '角色 · 顾辰') ]),
    mk('ga-scene', '场景', [ assetCard('s1', '场景 · 天台'), assetCard('s2', '场景 · 书房'), assetCard('s3', '场景 · 街角') ]),
    mk('ga-prop', '道具', [ assetCard('p1', '道具 · 怀表'), assetCard('p2', '道具 · 信笺') ]),
    mk('ga-voice', '音色', [ assetCard('v1', '音色 · 磁性男'), assetCard('v2', '音色 · 清甜女') ]),
  ]
  return tabPage('global-assets', '全局资产库', tabs, [
    dialog('ga-dlg-edit', '编辑资产', 'ga-edit', [ field('ga-edit-name', '名称', '资产名称'), field('ga-edit-desc', '类型描述', '一句话描述'), field('ga-edit-src', '来源项目', '项目 ID'), { type: 'row', id: 'ga-edit-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('ga-edit-c', '取消', 'closeDialog', 'ga-edit'), btn('ga-edit-ok', '保存', 'closeDialog', 'ga-edit', '已保存（沙盒模拟）') ] } ], '480px'),
    dialog('ga-dlg-new', '新建资产', 'ga-new', [ field('ga-new-name', '名称', '资产名称'), field('ga-new-desc', '类型描述', '一句话描述'), field('ga-new-role', '角色定位', '主角/配角'), { type: 'row', id: 'ga-new-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('ga-new-c', '取消', 'closeDialog', 'ga-new'), btn('ga-new-ok', '创建', 'closeDialog', 'ga-new', '已创建（沙盒模拟）') ] } ], '480px'),
    dialog('ga-dlg-imp', '导入到项目', 'ga-import', [ field('ga-imp-proj', '目标项目', '项目 ID'), { type: 'row', id: 'ga-imp-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('ga-imp-c', '取消', 'closeDialog', 'ga-import'), btn('ga-imp-ok', '导入', 'closeDialog', 'ga-import', '已导入（沙盒模拟）') ] } ], '440px'),
  ], [ inp('ga-search', '搜索资产…'), btn('ga-new-btn', '新建资产', 'openDialog', 'ga-new'), gbtn('ga-imp-btn', '导入到项目', 'openDialog', 'ga-import') ])
}

// 10) 图片反推（无页签，三栏）
function buildImg2Prompt() {
  return plainPage('img2prompt', '图片反推', [
    { type: 'row', id: 'i2p-cols', style: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '18px' }, children: [
      card('i2p-up', '上传图片', [ img('i2p-up-img', '拖拽/选择图片'), gbtn('i2p-up-btn', '选择图片', 'toast', '', '已选择（沙盒模拟）') ]),
      card('i2p-res', '反推结果', [ txt('i2p-res-t', '（提示词反推结果占位 · 沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }), inp('i2p-res-edit', '编辑提示词…') ]),
      card('i2p-hist', '反推历史', [ txt('i2p-hist-t', '（历史记录占位 · 沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }) ]),
    ] },
  ], [ btn('i2p-run', '开始反推', 'toast', '', '反推完成（沙盒模拟）') ])
}

// 11) LoRA 训练（无页签，分区面板）
function buildLoraTrain() {
  const panel = (id, title, fields) => card('lt-' + id, title, fields)
  return plainPage('lora-train', 'LoRA 训练', [
    { type: 'row', id: 'lt-grid', style: { display: 'flex', flexDirection: 'column', gap: '18px' }, children: [
      panel('type', '① LoRA 类型', [ field('lt-type', '类型', '角色 LoRA') ]),
      panel('data', '② 数据集', [ field('lt-data', '数据集目录', 'dataset/'), field('lt-cap', '触发词', 'someone') ]),
      panel('param', '③ 参数配置', [ field('lt-lr', '学习率', '1e-4'), field('lt-step', '训练步数', '2000'), field('lt-bs', '批次', '4') ]),
      panel('train', '④ 训练', [ btn('lt-start', '开始训练', 'toast', '', '训练已提交（沙盒模拟）') ]),
      panel('reg', '⑤ 已注册 LoRA 模型', [ txt('lt-reg-t', '（已注册模型列表占位 · 沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }) ]),
      panel('job', '⑥ 训练任务记录', [ txt('lt-job-t', '（任务记录占位 · 沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }) ]),
    ] },
  ])
}

// 12) 历史记录（页签：全部/视频/图片/文本 + 项目筛选）
function buildHistory() {
  const item = (id, name, meta) => card('hi-' + id, name, [ txt('hi-' + id + '-t', meta, { fontSize: '13px', color: 'var(--sub)' }) ])
  const mk = (id, label, cards) => ({ id, label, children: [ { type: 'row', id: 'hi-g-' + id, style: { display: 'flex', flexDirection: 'column', gap: '14px' }, children: cards } ] })
  const tabs = [
    mk('hi-all', '全部', [ item('a1', '成片 · 示例短剧', '视频 · 都市迷局 · 2:14'), item('a2', '分镜 · 天台对话', '图片 · 8 张'), item('a3', '剧本 · 开篇独白', '文本 · 1.2k 字') ]),
    mk('hi-video', '视频', [ item('v1', '成片 · 示例短剧', '视频 · 2:14'), item('v2', '成片 · 星河彼岸', '视频 · 3:01') ]),
    mk('hi-img', '图片', [ item('i1', '分镜 · 天台对话', '图片 · 8 张'), item('i2', '分镜 · 书房', '图片 · 6 张') ]),
    mk('hi-text', '文本', [ item('t1', '剧本 · 开篇独白', '文本 · 1.2k 字'), item('t2', '剧本 · 结局', '文本 · 980 字') ]),
  ]
  return tabPage('history', '历史记录', tabs, [], [ inp('hi-search', '搜索…'), gbtn('hi-filter', '项目筛选', 'toast', '', '已筛选（沙盒模拟）'), gbtn('hi-time', '时间筛选', 'toast', '', '已筛选（沙盒模拟）') ])
}

// 13) 会员中心（无页签）
function buildMembership() {
  return plainPage('membership', '会员中心', [
    statGrid('mb-stats', [ { id: 'mb-s1', title: '当前套餐', value: '免费版', sub: '个人使用' }, { id: 'mb-s2', title: '剩余积分', value: '10000', sub: '本月初重置' }, { id: 'mb-s3', title: '已解锁能力', value: '12', sub: '能力维度' } ]),
    { type: 'row', id: 'mb-plans', style: grid3, children: [
      card('mb-per', '个人版', [ txt('mb-per-t', '适合个人创作者：无限项目、基础模型额度。', { fontSize: '13px', color: 'var(--sub)' }), btn('mb-per-buy', '升级', 'toast', '', '已发起升级（沙盒模拟）') ]),
      card('mb-team', '团队版', [ txt('mb-team-t', '适合协作：多人项目、角色资产共享、权限管理。', { fontSize: '13px', color: 'var(--sub)' }), btn('mb-team-buy', '升级', 'toast', '', '已发起升级（沙盒模拟）') ]),
      card('mb-agree', '协议与说明', [ txt('mb-agree-t', '会员服务协议、自动续费说明、退款政策。', { fontSize: '13px', color: 'var(--sub)' }) ]),
    ] },
  ])
}

// 14) 我的数据（页签：我的项目/我的资产/我的成片/我的积分）
function buildMyData() {
  const mk = (id, label, cards) => ({ id, label, children: [ { type: 'row', id: 'md-g-' + id, style: { display: 'flex', flexDirection: 'column', gap: '14px' }, children: cards } ] })
  const tabs = [
    mk('md-proj', '我的项目', [ card('md-p1', '示例短剧', [ txt('md-p1-t', '12 镜头 · 草稿', { fontSize: '13px', color: 'var(--sub)' }) ]), card('md-p2', '示例回忆录', [ txt('md-p2-t', '8 镜头 · 草稿', { fontSize: '13px', color: 'var(--sub)' }) ]) ]),
    mk('md-asset', '我的资产', [ card('md-a1', '角色 · 林深', [ txt('md-a1-t', '已沉淀为长期记忆', { fontSize: '13px', color: 'var(--sub)' }) ]), card('md-a2', '场景 · 天台', [ txt('md-a2-t', '已沉淀为长期记忆', { fontSize: '13px', color: 'var(--sub)' }) ]) ]),
    mk('md-film', '我的成片', [ card('md-f1', '成片 · 示例短剧', [ txt('md-f1-t', '视频 · 2:14', { fontSize: '13px', color: 'var(--sub)' }) ]) ]),
    mk('md-credit', '我的积分', [ statGrid('md-credit-stats', [ { id: 'md-c1', title: '当前余额', value: '10000', sub: '积分' }, { id: 'md-c2', title: '本月消耗', value: '0', sub: 'credit_ledger' }, { id: 'md-c3', title: '本月充值', value: '0', sub: '充值记录' } ]) ]),
  ]
  return tabPage('my-data', '我的数据', tabs, [], [ gbtn('md-export', '导出数据', 'toast', '', '已导出（沙盒模拟）') ])
}

// 15) 消息中心（页签：全部/系统通知/积分变动/任务完成/活动）
function buildMessages() {
  const msg = (id, title, body) => card('ms-' + id, title, [ txt('ms-' + id + '-t', body, { fontSize: '13px', color: 'var(--sub)' }) ])
  const mk = (id, label, cards) => ({ id, label, children: [ { type: 'row', id: 'ms-g-' + id, style: { display: 'flex', flexDirection: 'column', gap: '14px' }, children: cards } ] })
  const tabs = [
    mk('ms-all', '全部', [ msg('a1', '系统通知', '新版本 v1.3.0 已发布'), msg('a2', '积分变动', '创作消耗 12 积分'), msg('a3', '任务完成', '分镜生成已完成'), msg('a4', '活动', '邀请好友得积分') ]),
    mk('ms-sys', '系统通知', [ msg('s1', '系统通知', '新版本 v1.3.0 已发布'), msg('s2', '系统通知', '服务维护通知') ]),
    mk('ms-credit', '积分变动', [ msg('c1', '积分变动', '创作消耗 12 积分'), msg('c2', '积分变动', '每日签到 +5 积分') ]),
    mk('ms-task', '任务完成', [ msg('t1', '任务完成', '分镜生成已完成'), msg('t2', '任务完成', '成片渲染已完成') ]),
    mk('ms-act', '活动', [ msg('ac1', '活动', '邀请好友得积分'), msg('ac2', '活动', '限时创作大赛') ]),
  ]
  return tabPage('messages', '消息中心', tabs, [], [ gbtn('ms-read', '全部已读', 'toast', '', '已标记已读（沙盒模拟）') ])
}

// 16) 帮助与反馈（页签：快速开始/文生图教程/片段编辑/人生回忆录/快捷键/常见问题 + 提交反馈 + 我的工单）
function buildHelp() {
  const doc = (id, title, body) => ({ id, label: title, children: [ card('hp-card-' + id, title, [ txt('hp-t-' + id, body, { fontSize: '14px', color: 'var(--sub)', lineHeight: '1.8' }) ]) ] })
  const tabs = [
    doc('quick', '快速开始', '① 新建项目 → ② 写剧本/分镜 → ③ 选模型生成 → ④ 剪辑成片。'),
    doc('t2i', '文生图教程', '在「文生图」页输入提示词，选择尺寸与步数，点击生成。'),
    doc('edit', '片段编辑', '在项目中进入片段，调整分镜、角色与音频。'),
    doc('memoir', '人生回忆录', '用照片与文字生成时光相册式回忆录。'),
    doc('shortcut', '快捷键', 'Ctrl+S 保存 · Ctrl+E 编辑 · ← → 翻页。'),
    doc('faq', '常见问题', 'Q: 积分如何获得？A: 每日签到与活动。'),
  ]
  return tabPage('help', '帮助与反馈', tabs, [
    dialog('hp-dlg', '提交反馈', 'hp-dlg', [
      { type: 'row', id: 'hp-type-row', style: { display: 'flex', gap: '8px', marginBottom: '12px' }, children: [ tag('hp-t1', '功能建议'), tag('hp-t2', 'Bug反馈'), tag('hp-t3', '咨询'), tag('hp-t4', '其他') ] },
      field('hp-title', '标题', '一句话描述'),
      field('hp-detail', '详细描述', '请尽量详细…'),
      { type: 'row', id: 'hp-dlg-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('hp-dlg-c', '取消', 'closeDialog', 'hp-dlg'), btn('hp-dlg-ok', '提交', 'closeDialog', 'hp-dlg', '已提交反馈（沙盒模拟）') ] },
    ], '520px'),
  ], [ btn('hp-feedback', '提交反馈', 'openDialog', 'hp-dlg'), gbtn('hp-ticket', '我的工单', 'toast', '', '查看我的工单（沙盒模拟）') ])
}

// 17) 操作日志（无页签，左文件列表 + 右内容）
function buildLogs() {
  return plainPage('logs', '操作日志', [
    { type: 'row', id: 'log-cols', style: { display: 'grid', gridTemplateColumns: '260px 1fr', gap: '18px' }, children: [
      card('log-files', '日志文件', [ txt('log-f1', 'manju.2026-08-14.log', { fontSize: '13px', color: 'var(--txt)', padding: '8px 0', borderBottom: '1px solid var(--line)', display: 'block' }), txt('log-f2', 'manju.2026-08-13.log', { fontSize: '13px', color: 'var(--txt)', padding: '8px 0', display: 'block' }) ]),
      card('log-view', '内容查看', [ inp('log-search', '关键字搜索…'), txt('log-t', '[12:01] 任务开始\n[12:02] 推理完成\n[12:03] 输出写入对象存储', { fontSize: '13px', color: 'var(--sub)', whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)', lineHeight: '1.7', marginTop: '10px' }) ]),
    ] },
  ])
}

// 18) 设置（页签：外观/AI模型/存储/网络/通知/快捷键/备份/维护/关于/能力维度/模板/模型）
function buildSettings() {
  const tabs = [
    { id: 'st-appear', label: '外观', children: [ card('st-appear-card', '配色主题', [ txt('st-appear-t', '在「配色主题」中选择预设或启用「自定义」主题（由页面自定义模块保存）。', { fontSize: '13px', color: 'var(--sub)' }), { type: 'row', id: 'st-appear-acc', style: { display: 'flex', gap: '8px', marginTop: '12px', flexWrap: 'wrap' }, children: [ tag('st-a1', '默认'), tag('st-a2', 'P5 怪盗', 'magenta'), tag('st-a3', '赛博朋克', 'cyan'), tag('st-a4', '自定义', 'green') ] } ]) ] },
    { id: 'st-ai', label: 'AI 模型', children: [ card('st-ai-card', '模型配置', [ field('st-ai-ep', 'API 地址', 'http://localhost:11434'), field('st-ai-key', 'API Key', 'sk-…') ]) ] },
    { id: 'st-store', label: '存储', children: [ card('st-store-card', '存储配置', [ field('st-store-path', '对象存储路径', './storage'), field('st-store-quota', '配额', '10GB') ]) ] },
    { id: 'st-net', label: '网络', children: [ card('st-net-card', '网络配置', [ field('st-net-proxy', '代理', '无'), field('st-net-timeout', '超时', '30s') ]) ] },
    { id: 'st-notify', label: '通知', children: [ card('st-notify-card', '通知设置', [ tag('st-n1', '任务完成'), tag('st-n2', '积分变动'), tag('st-n3', '系统公告') ]) ] },
    { id: 'st-short', label: '快捷键', children: [ card('st-short-card', '快捷键', [ txt('st-short-t', 'Ctrl+S 保存 · Ctrl+E 编辑 · ← → 翻页', { fontSize: '13px', color: 'var(--sub)' }) ]) ] },
    { id: 'st-back', label: '备份', children: [ card('st-back-card', '备份与恢复', [ btn('st-back-btn', '立即备份', 'toast', '', '已备份（沙盒模拟）'), gbtn('st-restore-btn', '恢复', 'toast', '', '已恢复（沙盒模拟）') ]) ] },
    { id: 'st-maint', label: '维护', children: [ card('st-maint-card', '维护', [ gbtn('st-clear', '清理缓存', 'toast', '', '已清理（沙盒模拟）') ]) ] },
    { id: 'st-about', label: '关于', children: [ card('st-about-card', '关于', [ txt('st-about-t', '万能短剧 AI 创作平台 · 版本 1.3.0', { fontSize: '13px', color: 'var(--sub)' }) ]) ] },
    { id: 'st-cap', label: '能力维度', children: [ card('st-cap-card', '能力维度', [ txt('st-cap-t', '角色一致性 / 分镜 / 口型 / 成片 …（12 维）', { fontSize: '13px', color: 'var(--sub)' }) ]) ] },
    { id: 'st-tpl', label: '模板', children: [ card('st-tpl-card', '模板管理', [ txt('st-tpl-t', '（模板列表占位 · 沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }) ]) ] },
    { id: 'st-model', label: '模型', children: [ card('st-model-card', '模型管理', [ txt('st-model-t', '（本地/云端模型列表占位 · 沙盒模拟）', { fontSize: '13px', color: 'var(--sub)' }) ]) ] },
  ]
  return tabPage('settings', '设置', tabs, [], [ btn('st-save', '保存设置', 'toast', '', '已保存设置（沙盒模拟）') ])
}

// 19) 服务与版本（页签：服务状态/模块版本 + 弹窗：发布快照/版本回退）
function buildSystem() {
  const tabs = [
    { id: 'sy-stat', label: '服务状态', children: [ statGrid('sy-stats', [ { id: 'sy-s1', title: '用户数', value: '1', sub: '当前账户' }, { id: 'sy-s2', title: '今日积分消耗', value: '0', sub: 'credit_ledger' }, { id: 'sy-s3', title: '存储用量', value: '128MB', sub: '对象存储' }, { id: 'sy-s4', title: '在线设备', value: '1', sub: '实时心跳' } ]), card('sy-health', '服务健康', [ tag('sy-h1', 'Ollama 健康', 'green'), tag('sy-h2', 'ComfyUI 未配置') ]) ] },
    { id: 'sy-ver', label: '模块版本', children: [ card('sy-ver-card', '模块版本', [ txt('sy-ver-t', 'manju-agent v1.3.0 · 数据库 schema 12 · 前端 build 1', { fontSize: '13px', color: 'var(--sub)' }) ]) ] },
  ]
  return tabPage('system', '服务与版本', tabs, [
    dialog('sy-dlg-snap', '发布模块版本快照', 'sy-snap', [ field('sy-snap-note', '快照说明', '发版备注…'), { type: 'row', id: 'sy-snap-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('sy-snap-c', '取消', 'closeDialog', 'sy-snap'), btn('sy-snap-ok', '发布', 'closeDialog', 'sy-snap', '已发布快照（沙盒模拟）') ] } ], '440px'),
    dialog('sy-dlg-roll', '版本回退', 'sy-rollback', [ field('sy-roll-target', '回退到', 'v1.2.0'), { type: 'row', id: 'sy-roll-a', style: { display: 'flex', gap: '12px', marginTop: '16px', justifyContent: 'flex-end' }, children: [ gbtn('sy-roll-c', '取消', 'closeDialog', 'sy-rollback'), btn('sy-roll-ok', '回退', 'closeDialog', 'sy-rollback', '已回退（沙盒模拟）') ] } ], '440px'),
  ], [ btn('sy-snap-btn', '发布快照', 'openDialog', 'sy-snap'), gbtn('sy-roll-btn', '版本回退', 'openDialog', 'sy-rollback') ])
}

export const defaultPages = {
  home: buildHome(),
  projects: buildProjects(),
  tasks: buildTasks(),
  custom: buildCustom(),
  txt2img: buildTxt2img(),
  img2img: buildImg2img(),
  creative: buildCreative(),
  memoir: buildMemoir(),
  'global-assets': buildGlobalAssets(),
  img2prompt: buildImg2Prompt(),
  'lora-train': buildLoraTrain(),
  history: buildHistory(),
  membership: buildMembership(),
  messages: buildMessages(),
  help: buildHelp(),
  'my-data': buildMyData(),
  logs: buildLogs(),
  settings: buildSettings(),
  system: buildSystem(),
}
