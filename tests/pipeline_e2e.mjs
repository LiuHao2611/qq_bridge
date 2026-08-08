// 五阶段全流程端到端自动化测试（Phase 1~5）
// 通过真实后端 API 驱动：资产上传 → 分场拆解(LLM) → 分镜编排+引用 → 异步生成 → 合成全集
// 运行：node tests/pipeline_e2e.mjs   （需后端 7788 与 ComfyUI 8188 在线）
const BASE = 'http://127.0.0.1:7788/api/v1'
const results = []
let PASS = 0, FAIL = 0
function log(line) { console.log(line); }
async function check(name, fn) {
  const t0 = Date.now()
  try { await fn(); PASS++; results.push(`PASS  ${name}  (${Date.now()-t0}ms)`) }
  catch (e) { FAIL++; results.push(`FAIL  ${name}  :: ${e.message || e}`) }
}
async function api(path, opts = {}) {
  const r = await fetch(BASE + path, opts)
  const ct = r.headers.get('content-type') || ''
  let body = null
  if (ct.includes('application/json')) { try { body = await r.json() } catch {} }
  else if (!r.ok) { const t = await r.text().catch(()=>''); throw new Error('HTTP '+r.status+' '+t.slice(0,120)) }
  if (!r.ok) throw new Error('HTTP '+r.status+' '+(body?.message||body?.detail||''))
  if (body && typeof body==='object' && 'code' in body) {
    if (body.code !== 0) throw new Error('API code='+body.code+' '+body.message)
    return body.data
  }
  return body
}
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC'
function pngBlob(){ return new Blob([Buffer.from(PNG,'base64')], {type:'image/png'}) }
async function uploadGlobal(name, type){
  const fd = new FormData()
  fd.append('file', pngBlob(), 'a.png'); fd.append('name', name); fd.append('type', type)
  return api('/assets/upload', { method:'POST', body: fd })
}
async function uploadProject(pid, name, type){
  const fd = new FormData()
  fd.append('file', pngBlob(), 'a.png'); fd.append('name', name); fd.append('type', type)
  return api(`/projects/${pid}/assets`, { method:'POST', body: fd })
}
async function pollTask(tid, cap=120, gap=5000){
  for (let i=0;i<cap;i++){
    const d = await api(`/tasks/${tid}`)
    const t = d.task || d
    const st = t.status
    if (st==='succeeded'||st==='failed'||st==='cancelled') return t
    await new Promise(r=>setTimeout(r,gap))
  }
  throw new Error('task '+tid+' 轮询超时')
}

const ctx = {}
async function main(){
  // 0. 健康检查
  await check('Phase0 健康检查', async () => {
    const h = await api('/health'); if (h.status!=='active') throw new Error('status='+h.status)
  })

  // 创建测试项目
  let pid
  await check('准备 创建测试项目', async () => {
    const p = await api('/projects', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ name:'E2E测试项目_'+Date.now(), resolution:'720p', ratio:'16:9' }) })
    pid = p.project?.project_id; ctx.pid = pid
  })
  if (!pid) { log('无法创建项目，终止后续阶段'); return finish() }

  // Phase 1：全局资产上传 + 封面回流
  let gAssetId, gCover
  await check('Phase1 全局资产上传+封面', async () => {
    const r = await uploadGlobal('阿浩_E2E', 'role')
    if (!r.asset?.asset_id) throw new Error('无 asset_id')
    gAssetId = r.asset.asset_id; gCover = r.cover_url
    const cr = await fetch('http://127.0.0.1:7788' + gCover)
    if (!cr.ok) throw new Error('封面 HTTP '+cr.status)
    const buf = Buffer.from(await cr.arrayBuffer())
    if (buf.length < 50) throw new Error('封面内容异常 '+buf.length+'B')
  })

  // Phase 1：本集资产上传 + 列表隔离（捕获 scene 资产 id 供 Phase3 引用）
  let sceneAssetId
  await check('Phase1 本集资产上传+隔离列表', async () => {
    const r = await uploadProject(pid, '客厅_E2E', 'scene')
    if (!r.asset?.asset_id) throw new Error('无 asset_id')
    sceneAssetId = r.asset.asset_id; ctx.sceneAssetId = sceneAssetId
    const list = await api(`/projects/${pid}/assets?type=scene`)
    if (!Array.isArray(list.items) || !list.items.some(a=>a.asset_id===sceneAssetId))
      throw new Error('本集列表未包含刚上传资产')
    // 后端 /assets 返回全部资产（全局页前端按 is_global!==0 过滤），故本集资产会出现在列表里；
    // 真正的隔离契约是：该资产的 is_global===0，全局 UI 才会将其过滤掉，不会出现在全局库视图。
    const g = await api('/assets'); const gItems = g.items||[]
    const inGlobal = gItems.find(a=>a.asset_id===sceneAssetId)
    if (!inGlobal) throw new Error('本集资产未出现在 /assets（上传未持久化）')
    if (inGlobal.is_global !== 0) throw new Error('本集资产 is_global='+inGlobal.is_global+'，将被泄漏到全局库视图')
  })

  // Phase 2：分场拆解（本地 LLM Qwen）
  let segs
  await check('Phase2 LLM 分场拆解', async () => {
    const story = `林浩是一名退役刑警，回到故乡小镇调查旧案。\n\n深夜，林浩在老宅发现一封尘封的信，信中揭示当年真相另有隐情。`
    const r = await api(`/projects/${pid}/split-story`, { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ story }) })
    segs = r.segments || []
    if (r.source!=='llm') throw new Error('source='+r.source+'（未走 LLM 拆解）')
    if (!segs.length) throw new Error('未拆出任何场次')
  })

  // Phase 3：片段创建 + 分镜编排（@提及→refs 后端回环）
  // 单镜头且时长==片段时长(8s)，满足 BR-003；引用场景资产（走基础文生图，避免 IP-Adapter 双调用拖慢）
  let sid
  await check('Phase3 片段创建+分镜编排(@提及→refs)', async () => {
    const seg = segs[0]
    const c = await api(`/projects/${pid}/segments`, { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ title: seg.title||'场次01', duration: 8, resolution:'720p', ratio:'16:9' }) })
    sid = c.segment?.segment_id; ctx.sid = sid
    const shots = [{
      shot_id: 'sh1', index: 1, duration: 8, shot_type: 'medium',
      camera_movement: 'static', camera_angle:'eye_level', focal_length:'35mm',
      description: seg.shots?.[0]?.description || '示例画面', image_prompt:'',
      voiceover:'@客厅_E2E 房间内光线昏暗', dialogue:'', negative_prompt:'', setting:'',
      asset_refs:[{asset_id: sceneAssetId, asset_type:'scene', used_in:'shot'}],
    }]
    const pr = await api(`/projects/${pid}/segments/${sid}/shots`, { method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ shots }) })
    const got = await api(`/projects/${pid}/segments/${sid}`)
    const gshot = (got.shots||[])[0]
    const refs = (gshot?.asset_refs||[]).map(x=>x.asset_id||x)
    if (!refs.includes(sceneAssetId)) throw new Error('refs 未落库: '+JSON.stringify(refs))
    if (!(gshot?.voiceover||'').includes('@客厅_E2E')) throw new Error('voiceover 未保存')
  })
  if (!sid) { log('无法创建片段，跳过生成/合成'); return finish() }

  // Phase 4：预授权 → 异步生成 → 轮询
  await check('Phase4 预授权+异步生成+轮询', async () => {
    const est = await api('/user/points?operation=estimate&duration=8&resolution=720p&audio=0&model_id=seedance-2.0-mini')
    const credits = est.estimated_credits
    if (!credits || credits<=0) throw new Error('预估积分为0')
    const idem = 'e2e_gen_'+Date.now()
    const pa = await api('/points/pre-auth', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ amount: credits, operation_type:'segment_generate', idempotency_key: idem }) })
    if (!pa.pre_auth_id) throw new Error('无 pre_auth_id')
    const gen = await api(`/projects/${pid}/segments/${sid}/generate`, { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ pre_auth_id: pa.pre_auth_id, params:{ duration:8, resolution:'720p', ratio:'16:9', audio:0, model_id:'seedance-2.0-mini', seed:-1 } }) })
    const tid = gen.task_id; if (!tid) throw new Error('无 task_id')
    const t = await pollTask(tid)
    ctx.genStatus = t.status
    if (t.status!=='succeeded') throw new Error('生成状态='+t.status+' err='+(t.error_message||''))
    const url = t.result_url || t.video_url || ''
    if (!url) throw new Error('生成成功但无 result_url')
    log('   生成结果: '+url)
  })

  // Phase 5：合成全集（预授权 → 提交 → 轮询）
  await check('Phase5 合成全集+轮询', async () => {
    const segsAll = await api(`/projects/${pid}/segments`)
    const totalDur = (segsAll.items||[]).reduce((a,s)=>a+(Number(s.duration)||0),0) || 8
    const est = await api(`/user/points?operation=estimate&duration=${totalDur}&resolution=720p&audio=0&model_id=seedance-2.0-mini`)
    const idem = 'e2e_syn_'+Date.now()
    const pa = await api('/points/pre-auth', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ amount: est.estimated_credits, operation_type:'synthesize', idempotency_key: idem }) })
    if (!pa.pre_auth_id) throw new Error('无 pre_auth_id')
    const syn = await api('/synthesize', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ project_id: pid, pre_auth_id: pa.pre_auth_id, options:{} }) })
    const tid = syn.task_id; if (!tid) throw new Error('无 task_id')
    const t = await pollTask(tid)
    ctx.synStatus = t.status
    if (t.status!=='succeeded') throw new Error('合成状态='+t.status+' err='+(t.error_message||''))
    log('   合成结果: '+(t.result_url||t.video_url||''))
  })

  finish()
}

function finish(){
  log('\n================ E2E 测试报告 ================')
  for (const r of results) log(r)
  log(`\n合计: ${PASS} 通过 / ${FAIL} 失败`)
  process.exit(FAIL>0?1:0)
}
main().catch(e=>{ log('FATAL '+e.message); finish() })
