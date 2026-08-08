/* =========================================================================
   漫剧工作台 — Vue 3 SPA (参考万能短剧 / 导演工作台)
   零运行时联网：Vue / VueRouter / LiteGraph 均本地化于 /static/vendor
   ========================================================================= */
(function () {
  "use strict";
  const { createApp, ref, reactive, computed, onMounted, nextTick, watch } = Vue;
  const { createRouter, createWebHistory, useRoute, useRouter } = VueRouter;

  /* ---------------- 基础工具 ---------------- */
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let e; try { e = await r.json(); } catch { e = { error: r.statusText }; }
      throw new Error(e.error || ("HTTP " + r.status));
    }
    const ct = r.headers.get("content-type") || "";
    return ct.indexOf("application/json") >= 0 ? r.json() : r.text();
  }

  function pollStatus(tid, onProgress, timeoutMs) {
    timeoutMs = timeoutMs || 1800000;
    const start = Date.now();
    return new Promise((resolve, reject) => {
      const tick = async () => {
        try {
          const s = await api("/api/status?task_id=" + tid);
          if (onProgress) onProgress(s);
          if (s.done) return resolve(s);
          if (s.error) return reject(new Error(s.error));
          if (Date.now() - start > timeoutMs) return reject(new Error("轮询超时"));
          setTimeout(tick, 2500);
        } catch (e) { reject(e); }
      };
      tick();
    });
  }

  const toastState = reactive({ msg: "" });
  let _toastTimer = null;
  function toast(m) {
    toastState.msg = m;
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => (toastState.msg = ""), 2600);
  }

  function fmtTime(t) {
    if (!t) return "";
    const d = new Date(t);
    const p = (n) => (n < 10 ? "0" + n : "" + n);
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function hashHue(s) {
    let h = 0; for (let i = 0; i < (s || "").length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  }
  function coverStyle(id) {
    const h = hashHue(id);
    return { background: "linear-gradient(135deg, hsl(" + h + ",55%,32%), hsl(" + ((h + 60) % 360) + ",55%,22%))" };
  }
  const STATUS_TXT = { planning: "规划中", rendering: "生成中", done: "已完成", error: "失败", draft: "草稿" };
  function statusText(s) { return STATUS_TXT[s] || "草稿"; }
  function deepClone(o) { return JSON.parse(JSON.stringify(o || {})); }

  /* =======================================================================
     顶部栏
     ======================================================================== */
  const TopBar = {
    template: `
    <header class="topbar">
      <div class="topbar-left">
        <div class="logo"><span class="mark">漫</span> 漫剧工作台</div>
        <div v-if="project.title" class="project-name">{{ project.title }}</div>
      </div>
      <div class="topbar-center"><slot name="center"></slot></div>
      <div class="topbar-right">
        <span class="save-dot" :class="saved?'saved':'dirty'"></span>
        <span class="save-txt">{{ saved?'已保存':'未保存' }}</span>
        <slot name="right"></slot>
      </div>
    </header>`,
    props: { project: { type: Object, default: () => ({}) }, saved: { type: Boolean, default: true } },
  };

  /* =======================================================================
     侧边栏（完整导航）
     ======================================================================== */
  const Sidebar = {
    template: `
    <aside class="sidebar">
      <router-link to="/projects" class="nav-item" :class="{active: route.path==='/projects'||route.path==='/'}">
        <span class="icon">🏠</span>首页
      </router-link>
      <div class="nav-group">创作</div>
      <router-link to="/projects" class="nav-item" :class="{active: route.path==='/projects'}">
        <span class="icon">📁</span>项目中心
      </router-link>
      <router-link to="/tasks" class="nav-item" :class="{active: route.path==='/tasks'}">
        <span class="icon">📝</span>任务中心
      </router-link>
      <router-link to="/txt2img" class="nav-item" :class="{active: route.path==='/txt2img'}">
        <span class="icon">✨</span>文生图
      </router-link>
      <router-link to="/img2img" class="nav-item" :class="{active: route.path==='/img2img'}">
        <span class="icon">🖼️</span>图生图
      </router-link>
      <router-link to="/creative" class="nav-item" :class="{active: route.path==='/creative'}">
        <span class="icon">🎨</span>创意工坊
      </router-link>
      <router-link to="/memoir" class="nav-item" :class="{active: route.path==='/memoir'}">
        <span class="icon">📖</span>人生回忆录
      </router-link>

      <div class="nav-group">资产</div>
      <router-link v-if="pid" :to="'/project/'+pid+'/assets'" class="nav-item" :class="{active: route.path==='/project/'+pid+'/assets'}">
        <span class="icon">👤</span>我的资产
      </router-link>
      <router-link to="/global-assets" class="nav-item" :class="{active: route.path==='/global-assets'}">
        <span class="icon">🌐</span>全局资产库
      </router-link>
      <router-link to="/img2prompt" class="nav-item" :class="{active: route.path==='/img2prompt'}">
        <span class="icon">🔍</span>图片反推
      </router-link>

      <div class="nav-group">项目内</div>
      <router-link v-if="pid" :to="'/project/'+pid" class="nav-item" :class="{active: route.path==='/project/'+pid}">
        <span class="icon">🎬</span>工作台
      </router-link>
      <router-link v-if="pid" :to="'/project/'+pid+'/assets'" class="nav-item" :class="{active: route.path==='/project/'+pid+'/assets'}">
        <span class="icon">🖼️</span>素材库
      </router-link>
      <router-link v-if="pid" :to="'/project/'+pid+'/canvas'" class="nav-item" :class="{active: route.path==='/project/'+pid+'/canvas'}">
        <span class="icon">🧩</span>工作流画布
      </router-link>

      <div class="nav-group">系统</div>
      <router-link to="/history" class="nav-item" :class="{active: route.path==='/history'}">
        <span class="icon">🕒</span>历史记录
      </router-link>
      <router-link to="/messages" class="nav-item" :class="{active: route.path==='/messages'}">
        <span class="icon">💬</span>消息中心
      </router-link>
      <router-link to="/settings" class="nav-item" :class="{active: route.path==='/settings'}">
        <span class="icon">⚙️</span>设置
      </router-link>
      <router-link to="/help" class="nav-item" :class="{active: route.path==='/help'}">
        <span class="icon">❓</span>帮助与反馈
      </router-link>
      <router-link to="/logs" class="nav-item" :class="{active: route.path==='/logs'}">
        <span class="icon">📋</span>操作日志
      </router-link>

      <div class="sidebar-foot"><span class="dot" :class="online?'on':'off'"></span>{{ online?'服务在线':'服务离线' }}</div>
    </aside>`,
    props: { pid: { type: String, default: "" } },
    setup() {
      const route = useRoute();
      const online = ref(false);
      const check = async () => { try { await api("/api/models"); online.value = true; } catch (e) { online.value = false; } };
      onMounted(check);
      return { route, online };
    }
  };

  /* =======================================================================
     组件 1：项目列表
     ======================================================================== */
  const ProjectsList = {
    template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>我的项目</h1>
          <p class="muted">本地离线 · 免费 AI 漫剧生成工作台</p>
        </div>
        <button class="btn primary" @click="createProject">＋ 新建项目</button>
      </div>
      <div class="proj-grid">
        <div v-for="p in projects" :key="p.id" class="proj-card" @click="open(p.id)">
          <div class="proj-cover" :style="coverStyle(p.id)">
            <img v-if="p.cover" :src="p.cover" style="width:100%;height:100%;object-fit:cover">
            <span class="badge" :class="(p.status||'draft')">{{ statusText(p.status) }}</span>
          </div>
          <div class="proj-body">
            <div class="proj-title">{{ p.title }}</div>
            <div class="proj-meta">{{ p.scene_count || 0 }} 镜头 · {{ fmt(p.updated_at) }}</div>
          </div>
          <div class="proj-actions">
            <button class="btn sm" @click.stop="open(p.id)">打开</button>
            <button class="btn sm danger" @click.stop="del(p)">删除</button>
          </div>
        </div>
        <div v-if="!projects.length" class="empty" style="grid-column:1/-1">还没有项目，点右上角「新建项目」开始创作</div>
      </div>
    </div>`,
    setup() {
      const projects = ref([]);
      const load = async () => { try { projects.value = await api("/api/projects"); } catch (e) { toast("加载失败: " + e.message); } };
      const createProject = async () => {
        const title = prompt("项目名称：", "未命名项目");
        if (title === null) return;
        try {
          const p = await api("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
          router.push("/project/" + p.id);
        } catch (e) { toast("创建失败: " + e.message); }
      };
      const open = (id) => router.push("/project/" + id);
      const del = async (p) => {
        if (!confirm("确定删除项目「" + p.title + "」？")) return;
        try { await api("/api/projects/" + p.id, { method: "DELETE" }); toast("已删除"); load(); }
        catch (e) { toast("删除失败: " + e.message); }
      };
      onMounted(load);
      return { projects, createProject, open, del, fmt: fmtTime, statusText, coverStyle };
    },
  };

  /* =======================================================================
     组件 2：导演工作台（参考万能短剧）
     ======================================================================== */
  const DirectorWorkbench = {
    template: `
    <div class="workspace-shell">
      <div class="ws-toolbar">
        <div class="title">
          <input v-model="project.title" @change="saveMeta" placeholder="项目名称" />
          <div class="sub">本地 AI 漫剧工作台 · 导演创意模式</div>
        </div>
        <div class="sep"></div>
        <div class="group"><label>模型</label>
          <select v-model="project.model_config.model" @change="saveConfig">
            <option>Seedance 2.0 Mini</option>
            <option>Seedance 2.0 Pro</option>
            <option>Flash · 长</option>
            <option>Realistic Vision</option>
            <option>MeinaMix</option>
          </select>
        </div>
        <div class="group"><label>分辨率</label>
          <select v-model="project.model_config.resolution" @change="saveConfig">
            <option>720p</option><option>1080p</option><option>480p</option>
          </select>
        </div>
        <div class="group"><label>比例</label>
          <select v-model="project.model_config.ratio" @change="saveConfig">
            <option>16:9</option><option>9:16</option><option>1:1</option>
          </select>
        </div>
        <div class="group"><label>时长</label>
          <input type="number" min="1" max="120" v-model.number="project.model_config.duration" @change="saveConfig" /> 秒
        </div>
        <div class="group" style="margin-left:auto">
          <button class="btn" :disabled="planning" @click="plan">🧠 AI 拆镜</button>
          <button class="btn primary" :disabled="!canRender || rendering" @click="render">🎬 合成全集</button>
          <button class="btn blue" @click="quickExport">📤 导出</button>
        </div>
      </div>

      <div class="ws-director">
        <!-- 左栏：人物资料 + 资产库 -->
        <div class="ws-col">
          <h3>人物资料 <span class="sub">{{ project.characters.length }}</span></h3>
          <div class="char-list">
            <div v-for="(c,i) in project.characters" :key="c.id" class="char-card" :class="{active: activeChar===i}" @click="activeChar=i">
              <div class="name">{{ c.name }} <span class="meta">{{ c.relation }}</span></div>
              <div class="meta">{{ c.age }} · {{ c.notes || '暂无备注' }}</div>
              <div class="age-pills">
                <span class="pill active">青年</span><span class="pill">中年</span><span class="pill">老年</span>
              </div>
            </div>
            <button class="btn sm ghost" style="width:100%;margin-top:6px" @click="addCharacter">＋ 添加人物</button>
          </div>

          <h3 style="margin-top:18px">资产库</h3>
          <div class="asset-tabs">
            <div class="t" :class="{on:assetTab==='characters'}" @click="assetTab='characters'">角色</div>
            <div class="t" :class="{on:assetTab==='scenes'}" @click="assetTab='scenes'">场景</div>
            <div class="t" :class="{on:assetTab==='props'}" @click="assetTab='props'">道具</div>
          </div>
          <div class="asset-grid-mini">
            <div v-for="(a,i) in assetList" :key="i" class="asset-mini" @click="preview=a.url">
              <img v-if="a.url" :src="a.url" />
              <div v-else class="empty" style="padding:0;font-size:11px">无图</div>
              <span class="tag">{{ a.name || '未命名' }}</span>
            </div>
            <label class="asset-mini" style="display:flex;align-items:center;justify-content:center;border-style:dashed">
              <span style="color:var(--muted);font-size:20px">＋</span>
              <input type="file" multiple accept="image/*" @change="uploadAssets" style="display:none">
            </label>
          </div>

          <h3 style="margin-top:18px">初始故事设定</h3>
          <textarea class="textarea" style="min-height:80px;font-size:12px" v-model="project.story.setting" @change="saveStory" placeholder="故事背景、年代、地点…"></textarea>
          <h3 style="margin-top:12px;font-size:12px">时间轴</h3>
          <textarea class="textarea" style="min-height:50px;font-size:12px" v-model="project.story.timeline" @change="saveStory" placeholder="时间线 / 关键事件…"></textarea>
        </div>

        <!-- 中栏：章节 + 导演创意卡片 -->
        <div class="ws-col" style="background:var(--bg-2)">
          <div class="chapter-tabs">
            <div v-for="(ch,i) in project.chapters" :key="ch.id" class="ch" :class="{on:activeChapter===i}" @click="switchChapter(i)">
              {{ ch.title }}
            </div>
            <div class="add" @click="addChapter">＋</div>
          </div>

          <div class="director-card">
            <h2>导演创意指导：{{ project.title || '未命名' }}</h2>

            <h4>角色与风格参考</h4>
            <div class="ref-row">
              <div v-for="(c,i) in topCharacters" :key="i" class="ref-item">
                <img v-if="c.refs && c.refs[0]" :src="c.refs[0]">
                <div v-else class="empty" style="padding:0;height:100%;display:flex;align-items:center;justify-content:center;font-size:12px">无参考图</div>
                <div class="cap">{{ c.name }}</div>
              </div>
              <div class="ref-item" style="border-style:dashed;cursor:pointer" @click="goAssets">
                <div class="empty" style="padding:0;height:100%;display:flex;align-items:center;justify-content:center;font-size:24px">＋</div>
              </div>
            </div>

            <h4>分镜表 <button class="btn sm" @click="addShot" style="margin-left:auto">＋ 添加分镜</button></h4>
            <table class="shot-table">
              <thead>
                <tr><th style="width:40px">镜头</th><th>景别</th><th>运镜</th><th>时长</th><th>画面描述</th><th>旁白/台词</th><th>参考图</th></tr>
              </thead>
              <tbody>
                <tr v-for="(s,i) in chapterShots" :key="s.scene_id||i">
                  <td><span class="idx">{{ i+1 }}</span></td>
                  <td><select v-model="s.shot_type" @change="saveScenes"><option>特写</option><option>近景</option><option>中景</option><option>全景</option><option>远景</option></select></td>
                  <td><select v-model="s.camera_move" @change="saveScenes"><option>固定</option><option>推镜</option><option>拉镜</option><option>摇镜</option><option>跟拍</option><option>升降</option></select></td>
                  <td><input type="number" v-model.number="s.duration" @change="saveScenes" style="width:50px" /> 秒</td>
                  <td><textarea v-model="s.description" @change="saveScenes" placeholder="画面描述"></textarea></td>
                  <td><textarea v-model="s.caption" @change="saveScenes" placeholder="旁白/台词"></textarea></td>
                  <td>
                    <img v-if="s.thumb" :src="s.thumb" class="thumb" @click="preview=s.thumb">
                    <span v-else class="muted" style="font-size:11px">待生成</span>
                  </td>
                </tr>
              </tbody>
            </table>

            <h4>色彩参考</h4>
            <div class="color-palette">
              <div v-for="(c,i) in palette" :key="i" class="color-swatch" :style="{background:c.color}">{{ c.name }}</div>
              <div class="color-swatch" style="background:var(--bg-3);border-style:dashed;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:18px;cursor:pointer">＋</div>
            </div>

            <h4>事实与人物锚点</h4>
            <div class="check-list">
              <div class="check-item" :class="{ok: project.checks.character_photo}"><span class="ck">✓</span> 人物照片 / 角色参考已上传</div>
              <div class="check-item" :class="{ok: project.checks.storyboard}"><span class="ck">✓</span> 分镜表已填写</div>
              <div class="check-item" :class="{ok: project.checks.director_guide}"><span class="ck">✓</span> 导演创意指导已确认</div>
              <div class="check-item" :class="{ok: project.checks.video_clip}"><span class="ck">✓</span> 成片视频已生成</div>
            </div>
          </div>
        </div>

        <!-- 右栏：预览 + 参数 -->
        <div class="ws-col">
          <h3>视频预览</h3>
          <div class="preview-box">
            <video v-if="videoUrl" :src="videoUrl" controls></video>
            <img v-else-if="preview" :src="preview" />
            <div v-else class="empty">暂无视频，点击「合成全集」开始生成</div>
          </div>

          <div class="param-group">
            <div class="g-title">输出参数</div>
            <div class="param-row"><span class="lab">成片比例</span><select v-model="project.model_config.ratio" @change="saveConfig"><option>16:9</option><option>9:16</option><option>1:1</option></select></div>
            <div class="param-row"><span class="lab">分辨率</span><select v-model="project.model_config.resolution" @change="saveConfig"><option>720p</option><option>1080p</option><option>480p</option></select></div>
            <div class="param-row"><span class="lab">总时长</span><input type="number" v-model.number="project.model_config.duration" @change="saveConfig" style="width:60px" /> 秒</div>
            <div class="param-row"><span class="lab">输出格式</span><select v-model="project.output_params.format" @change="saveConfig"><option>mp4</option><option>mov</option></select></div>
            <div class="param-row"><span class="lab">帧率</span><input type="number" v-model.number="project.output_params.fps" @change="saveConfig" style="width:60px" /></div>
            <div class="param-row"><span class="lab">编码</span><select v-model="project.output_params.codec" @change="saveConfig"><option>H.264</option><option>H.265</option></select></div>
          </div>

          <div class="param-group">
            <div class="g-title">模型配置</div>
            <div class="param-row"><span class="lab">底模</span><select v-model="project.ckpt" @change="saveConfig"><option v-for="c in models.checkpoints" :key="c" :value="c">{{ c }}</option></select></div>
            <div class="param-row"><span class="lab">真·动画</span><span class="toggle" :class="{on: project.animated}" @click="project.animated=!project.animated; saveConfig()"><span class="sw"></span></span></div>
            <div class="param-row"><span class="lab">角色一致</span><span class="toggle" :class="{on: project.use_ip}" @click="project.use_ip=!project.use_ip; saveConfig()"><span class="sw"></span></span></div>
          </div>

          <div class="param-group">
            <div class="g-title">生成前检查</div>
            <div class="check-list">
              <div class="check-item" :class="{ok: canRender}"><span class="ck">✓</span> 分镜数据已就绪</div>
              <div class="check-item" :class="{ok: models.checkpoints && models.checkpoints.length}"><span class="ck">✓</span> 本地底模可调用</div>
              <div class="check-item" :class="{ok: models.animatediff}"><span class="ck">✓</span> AnimateDiff 可用</div>
            </div>
          </div>

          <div class="progress" v-if="prog.stage">
            <div class="bar"><i :style="{width: pct+'%'}"></i></div>
            <div class="lab"><span>{{ prog.message || prog.stage }}</span><span>{{ prog.current }}/{{ prog.total }}</span></div>
          </div>

          <div class="ws-actions">
            <button class="btn" @click="saveAll" :disabled="saving">💾 保存</button>
            <button class="btn primary" :disabled="!canRender || rendering" @click="render">🎬 生成</button>
            <button class="btn pink" @click="reRender" :disabled="rendering">🔄 重新生成</button>
          </div>
        </div>
      </div>

      <div class="modal-mask" v-if="preview" @click="preview=''"><div class="modal"><img :src="preview" @click.stop /></div></div>
    </div>`,
    setup() {
      const route = useRoute();
      const pid = route.params.id;
      const project = reactive({
        id: pid, title: "加载中…", scenes: [], refs: [], assets: { characters: [], scenes: [], props: [] },
        status: "draft", ckpt: "", animated: false, use_ip: false, script_text: "",
        characters: [], chapters: [{ id: "ch1", title: "片段 01", active: true }],
        story: { setting: "", timeline: "" },
        model_config: { model: "Seedance 2.0 Mini", resolution: "720p", ratio: "16:9", duration: 12, audio: true, seed: -1 },
        output_params: { format: "mp4", fps: 24, codec: "H.264", quality: "标准" },
        checks: { character_photo: false, storyboard: false, director_guide: false, video_clip: false }
      });
      const models = reactive({ checkpoints: [], animatediff: false, ipadapter: false });
      const activeChar = ref(0);
      const assetTab = ref("characters");
      const activeChapter = ref(0);
      const preview = ref("");
      const videoUrl = ref("");
      const planning = ref(false), rendering = ref(false), saving = ref(false);
      const prog = reactive({ stage: "", current: 0, total: 0, message: "" });
      const palette = ref([
        { name: "主色", color: "#3a5bd9" }, { name: "辅色1", color: "#d9a23a" },
        { name: "辅色2", color: "#c23ad9" }, { name: "肤色", color: "#e8c4a2" }
      ]);

      watch(() => project.title, () => { });

      const pct = computed(() => prog.total ? Math.round((prog.current / prog.total) * 100) : 0);
      const canRender = computed(() => project.scenes && project.scenes.length > 0);
      const assetList = computed(() => (project.assets && project.assets[assetTab.value]) || []);
      const topCharacters = computed(() => project.characters.slice(0, 4));
      const chapterShots = computed(() => {
        const ch = project.chapters[activeChapter.value];
        if (!ch) return [];
        return (project.scenes || []).filter(s => (!s.chapter_id) || s.chapter_id === ch.id);
      });

      const loadModels = async () => {
        try { const m = await api("/api/models"); Object.assign(models, m); if (!project.ckpt && m.default) project.ckpt = m.default; }
        catch (e) { toast("模型检测失败: " + e.message); }
      };
      const load = async () => {
        try {
          const p = await api("/api/projects/" + pid);
          Object.assign(project, p);
          if (p.video_url) videoUrl.value = p.video_url;
          if (p.run_dir) loadThumbs(p.run_dir);
          updateChecks();
        } catch (e) { toast("加载失败: " + e.message); }
      };
      const loadThumbs = (run) => {};

      const updateChecks = () => {
        project.checks.character_photo = project.refs && project.refs.length > 0;
        project.checks.storyboard = project.scenes && project.scenes.length > 0;
        project.checks.director_guide = project.story && (project.story.setting || project.story.timeline);
        project.checks.video_clip = !!videoUrl.value;
      };

      const saveMeta = async () => {
        try { await api("/api/projects/" + pid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: project.title }) }); }
        catch (e) { toast("保存失败: " + e.message); }
      };
      const saveConfig = async () => {
        try {
          await api("/api/projects/" + pid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
            ckpt: project.ckpt, animated: project.animated, use_ip: project.use_ip,
            model_config: project.model_config, output_params: project.output_params, checks: project.checks
          }) });
        } catch (e) { toast("配置保存失败: " + e.message); }
      };
      const saveStory = async () => {
        try { await api("/api/projects/" + pid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ story: project.story }) }); updateChecks(); }
        catch (e) { toast("保存失败: " + e.message); }
      };
      const saveScenes = async () => {
        try { await api("/api/projects/" + pid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenes: project.scenes }) }); updateChecks(); }
        catch (e) { toast("保存失败: " + e.message); }
      };
      const saveAll = async () => {
        saving.value = true;
        try {
          await api("/api/projects/" + pid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
            title: project.title, scenes: project.scenes, refs: project.refs, assets: project.assets,
            characters: project.characters, chapters: project.chapters, story: project.story,
            model_config: project.model_config, output_params: project.output_params, checks: project.checks,
            ckpt: project.ckpt, animated: project.animated, use_ip: project.use_ip
          }) });
          toast("已保存");
        } catch (e) { toast("保存失败: " + e.message); }
        finally { saving.value = false; }
      };

      const addCharacter = () => {
        const id = "c" + (project.characters.length + 1);
        project.characters.push({ id, name: "人物" + (project.characters.length + 1), relation: "配角", age: "青年", notes: "", refs: [] });
        saveAll();
      };
      const addChapter = () => {
        const n = project.chapters.length + 1;
        project.chapters.push({ id: "ch" + n, title: "片段 " + String(n).padStart(2, "0"), active: false });
        activeChapter.value = n - 1;
        saveAll();
      };
      const switchChapter = (i) => { activeChapter.value = i; };
      const addShot = () => {
        const ch = project.chapters[activeChapter.value];
        const n = (project.scenes || []).length + 1;
        project.scenes.push({
          scene_id: n, caption: "", description: "", prompt: "", negative_prompt: "",
          duration: 4, shot_type: "中景", camera_move: "固定", thumb: "", chapter_id: ch ? ch.id : "ch1"
        });
        saveScenes();
      };
      const goAssets = () => router.push("/project/" + pid + "/assets");

      const plan = async () => {
        planning.value = true; prog.stage = "planning"; prog.current = 0; prog.total = 0; prog.message = "AI 拆解分镜…";
        const fd = new FormData(); fd.append("text", project.title || "未命名项目"); fd.append("mode", "full");
        try {
          const r = await api("/api/projects/" + pid + "/plan", { method: "POST", body: fd });
          await pollStatus(r.task_id, (s) => Object.assign(prog, s));
          await load(); toast("分镜规划完成");
        } catch (e) { toast("规划失败: " + e.message); prog.stage = ""; }
        finally { planning.value = false; }
      };
      const render = async () => {
        rendering.value = true; prog.stage = "rendering"; prog.current = 0; prog.total = 0; prog.message = "生成中…";
        const fd = new FormData();
        fd.append("scenes", JSON.stringify(project.scenes));
        fd.append("ckpt", project.ckpt || "");
        fd.append("animated", project.animated ? "1" : "0");
        fd.append("use_ip", project.use_ip ? "1" : "0");
        try {
          const r = await api("/api/projects/" + pid + "/render", { method: "POST", body: fd });
          await pollStatus(r.task_id, (s) => Object.assign(prog, s));
          await load(); toast("视频生成完成 🎉");
        } catch (e) { toast("生成失败: " + e.message); prog.stage = ""; }
        finally { rendering.value = false; }
      };
      const reRender = () => render();
      const quickExport = () => {
        if (!videoUrl.value) { toast("暂无成片可导出"); return; }
        const a = document.createElement("a"); a.href = videoUrl.value; a.download = (project.title || "manju") + ".mp4"; a.click();
        toast("开始下载成片");
      };
      const uploadAssets = async (ev) => {
        const files = ev.target.files; if (!files || !files.length) return;
        const fd = new FormData(); fd.append("type", assetTab.value); for (const f of files) fd.append("images", f);
        try { const r = await api("/api/projects/" + pid + "/assets", { method: "POST", body: fd }); project.assets = r.assets; toast("上传成功"); }
        catch (e) { toast("上传失败: " + e.message); }
      };

      onMounted(() => { loadModels(); load(); });
      return { pid, project, models, activeChar, assetTab, activeChapter, preview, videoUrl, planning, rendering, saving, prog,
        palette, pct, canRender, assetList, topCharacters, chapterShots,
        saveMeta, saveConfig, saveStory, saveScenes, saveAll, addCharacter, addChapter, switchChapter, addShot, goAssets,
        plan, render, reRender, quickExport, uploadAssets };
    },
  };

  /* =======================================================================
     组件 3：素材库（角色 / 场景 / 道具）
     ======================================================================== */
  const Assets = {
    template: `
    <div>
      <TopBar :project="project" />
      <div class="page" style="padding-top:18px">
        <div class="tabs">
          <div class="tab" :class="{on: tab==='characters'}" @click="tab='characters'">角色</div>
          <div class="tab" :class="{on: tab==='scenes'}" @click="tab='scenes'">场景</div>
          <div class="tab" :class="{on: tab==='props'}" @click="tab='props'">道具</div>
          <div style="flex:1"></div>
          <label class="btn sm upload-inline">＋ 上传素材<input type="file" multiple accept="image/*" @change="upload" /></label>
        </div>
        <div class="asset-grid">
          <div class="asset-card" v-for="(a,i) in list" :key="i">
            <img :src="a.url" @click="preview=a.url" />
            <div class="body">
              <div class="name"><input class="input" v-model="a.name" @change="save" /></div>
              <textarea class="input" v-model="a.prompt" placeholder="提示词…" @change="save"></textarea>
              <div class="acts"><button class="btn sm danger" @click="del(i)">删除</button></div>
            </div>
          </div>
          <div v-if="!list.length" class="empty" style="grid-column:1/-1">该分类暂无素材，点「上传素材」添加</div>
        </div>
      </div>
      <div class="modal-mask" v-if="preview" @click="preview=''"><div class="modal"><img :src="preview" @click.stop /></div></div>
    </div>`,
    setup() {
      const route = useRoute();
      const pid = route.params.id;
      const project = reactive({ title: "", assets: { characters: [], scenes: [], props: [] } });
      const tab = ref("characters");
      const preview = ref("");
      const list = computed(() => (project.assets && project.assets[tab.value]) || []);
      const load = async () => { try { const p = await api("/api/projects/" + pid); Object.assign(project, p); } catch (e) { toast("加载失败: " + e.message); } };
      const save = async () => {
        try { await api("/api/projects/" + pid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assets: project.assets }) }); toast("已保存"); }
        catch (e) { toast("保存失败: " + e.message); }
      };
      const del = (i) => { project.assets[tab.value].splice(i, 1); save(); };
      const upload = async (ev) => {
        const files = ev.target.files; if (!files || !files.length) return;
        const fd = new FormData(); fd.append("type", tab.value); for (const f of files) fd.append("images", f);
        try { const r = await api("/api/projects/" + pid + "/assets", { method: "POST", body: fd }); project.assets = r.assets; toast("上传成功"); }
        catch (e) { toast("上传失败: " + e.message); }
      };
      onMounted(load);
      return { pid, project, tab, preview, list, save, del, upload };
    },
  };

  /* =======================================================================
     组件 4：工作流画布（ComfyUI 同款 LiteGraph 节点编辑器）
     ======================================================================== */
  const NODE_CATALOG = [
    { type: "manju/script", title: "剧本解析", color: "#3a5bd9", desc: "输入主题/剧本" },
    { type: "manju/plan", title: "分镜规划", color: "#7a3ad9", desc: "拆分镜头数" },
    { type: "manju/prompt", title: "提示词增强", color: "#c23ad9", desc: "风格与优化" },
    { type: "manju/gen", title: "出图 (ComfyUI)", color: "#d94f3a", desc: "SD 生图参数" },
    { type: "manju/anim", title: "动画 (AnimateDiff)", color: "#d9a23a", desc: "动画帧数设置" },
    { type: "manju/comp", title: "合成 (FFmpeg)", color: "#3ad97a", desc: "视频输出设置" },
  ];

  function registerNodes(initial) {
    if (LiteGraph.registered_node_types["manju/script"]) return;

    class ScriptNode {
      constructor() {
        this.title = "剧本解析";
        this.color = "#3a5bd9";
        this.addInput("项目数据", "*");
        this.addOutput("主题", "string");
        this.addWidget("text", "主题/灵感", initial.theme || "", null, { property: "theme" });
      }
    }
    ScriptNode.title = "剧本解析";
    LiteGraph.registerNodeType("manju/script", ScriptNode);

    class PlanNode {
      constructor() {
        this.title = "分镜规划";
        this.color = "#7a3ad9";
        this.addInput("主题", "string");
        this.addOutput("分镜", "array");
        this.addWidget("combo", "模式", initial.mode || "full", null, { values: ["simple", "full"], property: "mode" });
        this.addWidget("number", "镜头数", initial.scenes_count || 8, null, { min: 1, max: 64, step: 1, property: "scenes_count" });
      }
    }
    PlanNode.title = "分镜规划";
    LiteGraph.registerNodeType("manju/plan", PlanNode);

    class PromptNode {
      constructor() {
        this.title = "提示词增强";
        this.color = "#c23ad9";
        this.addInput("分镜", "array");
        this.addOutput("提示词", "array");
        this.addWidget("combo", "风格", initial.style || "anime", null, { values: ["anime", "realistic", "cinematic"], property: "style" });
      }
    }
    PromptNode.title = "提示词增强";
    LiteGraph.registerNodeType("manju/prompt", PromptNode);

    class GenNode {
      constructor() {
        this.title = "出图 (ComfyUI)";
        this.color = "#d94f3a";
        this.addInput("提示词", "array");
        this.addOutput("图片", "image");
        this.addWidget("combo", "底模", initial.ckpt || "", null, { values: initial.ckpts || [], property: "ckpt" });
        this.addWidget("number", "步数", initial.steps || 25, null, { min: 1, max: 60, step: 1, property: "steps" });
        this.addWidget("number", "CFG", initial.cfg || 7, null, { min: 1, max: 20, step: 0.5, property: "cfg" });
        this.addWidget("combo", "采样器", initial.sampler || "DPM++ 2M Karras", null, { values: ["Euler", "DPM++ 2M Karras", "DPM++ SDE Karras", "UniPC"], property: "sampler" });
        this.addWidget("number", "宽", initial.width || 768, null, { min: 256, max: 2048, step: 64, property: "width" });
        this.addWidget("number", "高", initial.height || 432, null, { min: 256, max: 2048, step: 64, property: "height" });
        this.addWidget("combo", "角色一致(IP)", initial.use_ip || "否", null, { values: ["否", "是"], property: "use_ip" });
      }
    }
    GenNode.title = "出图 (ComfyUI)";
    LiteGraph.registerNodeType("manju/gen", GenNode);

    class AnimNode {
      constructor() {
        this.title = "动画 (AnimateDiff)";
        this.color = "#d9a23a";
        this.addInput("图片", "image");
        this.addOutput("动画", "video");
        this.addWidget("combo", "启用动画", initial.animated || "是", null, { values: ["否", "是"], property: "animated" });
        this.addWidget("combo", "运动模型", initial.motion || "", null, { values: initial.motions || [], property: "motion" });
        this.addWidget("number", "帧数", initial.frames || 16, null, { min: 4, max: 64, step: 1, property: "frames" });
        this.addWidget("number", "帧率", initial.fps || 8, null, { min: 1, max: 60, step: 1, property: "fps" });
      }
    }
    AnimNode.title = "动画 (AnimateDiff)";
    LiteGraph.registerNodeType("manju/anim", AnimNode);

    class CompNode {
      constructor() {
        this.title = "合成 (FFmpeg)";
        this.color = "#3ad97a";
        this.addInput("动画", "video");
        this.addOutput("成片", "video");
        this.addWidget("number", "输出帧率", initial.out_fps || 24, null, { min: 1, max: 60, step: 1, property: "out_fps" });
        this.addWidget("combo", "分辨率", initial.resolution || "768x432", null, { values: ["768x432", "1280x720", "1920x1080"], property: "resolution" });
      }
    }
    CompNode.title = "合成 (FFmpeg)";
    LiteGraph.registerNodeType("manju/comp", CompNode);
  }

  function buildDefaultGraph(graph, initial, project) {
    registerNodes(initial);
    const order = ["manju/script", "manju/plan", "manju/prompt", "manju/gen", "manju/anim", "manju/comp"];
    const nodes = [];
    order.forEach((type, i) => {
      const n = LiteGraph.createNode(type);
      if (!n) return;
      n.pos = [60 + i * 60, 60 + i * 150];
      graph.add(n);
      nodes.push(n);
    });
    for (let i = 0; i < nodes.length - 1; i++) {
      if (nodes[i].outputs.length && nodes[i + 1].inputs.length) {
        nodes[i].connect(0, nodes[i + 1], 0);
      }
    }
    const scriptNode = nodes[0];
    if (scriptNode && project) {
      const theme = project.title || (project.script_text ? project.script_text.slice(0, 40) : "");
      if (theme) {
        scriptNode.properties.theme = theme;
        if (scriptNode.widgets && scriptNode.widgets[0]) scriptNode.widgets[0].value = theme;
      }
    }
  }

  function nodeParam(n, key, fallback) {
    if (!n) return fallback;
    if (n.properties && n.properties[key] !== undefined) return n.properties[key];
    if (n.widgets) {
      const w = n.widgets.find((x) => x.name === key || x.options.property === key);
      if (w) return w.value;
    }
    return fallback;
  }

  function buildComfyWorkflow(params, prompt, negative) {
    const w = {}; let id = 1;
    const add = (cls, inputs) => { w[String(id)] = { class_type: cls, inputs }; return String(id++); };
    const ckpt = add("CheckpointLoaderSimple", { ckpt_name: params.ckpt });
    const pos = add("CLIPTextEncode", { text: prompt || "anime illustration, 1girl, masterpiece", clip: [ckpt, 1] });
    const neg = add("CLIPTextEncode", { text: negative || "lowres, bad anatomy, extra limbs, watermark", clip: [ckpt, 1] });
    let modelOut = [ckpt, 0];
    const animated = params.animated !== "否";
    const frames = parseInt(params.frames) || 16;
    if (animated) {
      const ad = add("ADE_AnimateDiffLoaderGen1", {
        model: [ckpt, 0], motion_model: params.motion, batch_size: frames,
        frames: frames, beta_schedule: "autoselect", full_precision: false });
      modelOut = [ad, 0];
    }
    if (params.use_ip === "是") {
      const ld = add("LoadImage", { image: "ref.png" });
      const ip = add("IPAdapter", { model: modelOut, ipadapter: "ip-adapter_sd15.safetensors", image: [ld, 0], weight: 0.8 });
      modelOut = [ip, 0];
    }
    const wdt = parseInt(params.width) || 768, hgt = parseInt(params.height) || 432;
    const latent = add("EmptyLatentImage", { width: wdt, height: hgt, batch_size: animated ? frames : 1 });
    const ks = add("KSampler", {
      model: modelOut, positive: [pos, 0], negative: [neg, 0], latent_image: [latent, 0],
      seed: Math.floor(Math.random() * 1e9), steps: parseInt(params.steps) || 25, cfg: parseFloat(params.cfg) || 7,
      sampler_name: params.sampler, scheduler: "karras", denoise: 1.0 });
    const vae = add("VAEDecode", { samples: [ks, 0], vae: [ckpt, 2] });
    add("SaveImage", { images: [vae, 0], filename_prefix: "manju" });
    return { prompt: w, extra_data: { "1": { inputs: { ckpt_name: params.ckpt } } }, last_node_id: id - 1, last_link_id: id };
  }

  const CanvasPage = {
    template: `
    <div class="canvas-shell">
      <TopBar :project="project" :saved="saved">
        <template #center><span class="canvas-title">工作流画布</span></template>
        <template #right>
          <button class="btn" @click="saveCanvas" :disabled="saving">💾 保存画布</button>
          <button class="btn" @click="fitView">适配视图</button>
          <button class="btn" @click="resetGraph">重置</button>
          <button class="btn" @click="exportComfy">导出 ComfyUI 工作流</button>
          <button class="btn primary" :disabled="running" @click="runWorkflow">▶ 运行工作流</button>
        </template>
      </TopBar>
      <div class="canvas-body">
        <div class="canvas-library">
          <h4>节点库</h4>
          <div class="lib-item" v-for="n in catalog" :key="n.type" @click="addNode(n.type)">
            <span class="dot" :style="{background:n.color}"></span>
            <div>
              <div class="lib-title">{{ n.title }}</div>
              <div class="lib-desc">{{ n.desc }}</div>
            </div>
          </div>
          <div class="lib-hint">点击节点添加到画布中央。拖拽节点、拖拽连线、双击标题改节点名、点击 widget 改参数。</div>
        </div>
        <div class="canvas-wrap" ref="wrap">
          <canvas ref="cv" tabindex="0"></canvas>
          <div class="canvas-hint" v-if="!ran">ComfyUI 同款节点画布 · 支持拖拽/连线/编辑参数</div>
        </div>
      </div>
      <div class="canvas-status" v-if="prog.stage">
        <span>状态: <b>{{ prog.stage }}</b> · {{ prog.current }}/{{ prog.total }} · {{ prog.message }}</span>
      </div>
    </div>`,
    setup() {
      const route = useRoute();
      const pid = route.params.id;
      const wrap = ref(null), cv = ref(null);
      const running = ref(false), ran = ref(false), saving = ref(false), saved = ref(true);
      const prog = reactive({ stage: "", current: 0, total: 0, message: "" });
      const project = reactive({ title: "" });
      let graph = null, canvas = null;
      const catalog = ref(NODE_CATALOG);

      const resizeCanvas = () => {
        if (!cv.value || !wrap.value) return;
        const rect = wrap.value.getBoundingClientRect();
        cv.value.width = rect.width;
        cv.value.height = rect.height;
        if (canvas) { canvas.setDirty(true, true); canvas.draw(true, true); }
      };

      const fitView = () => {
        if (!canvas || !graph || !wrap.value) return;
        const ns = graph._nodes || [];
        if (!ns.length) return;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        ns.forEach((n) => { minX = Math.min(minX, n.pos[0]); minY = Math.min(minY, n.pos[1]); maxX = Math.max(maxX, n.pos[0] + n.size[0]); maxY = Math.max(maxY, n.pos[1] + n.size[1]); });
        const w = wrap.value.clientWidth, h = wrap.value.clientHeight;
        const gw = Math.max(maxX - minX, 1), gh = Math.max(maxY - minY, 1);
        const scale = Math.min(w / gw, h / gh, 1.5) * 0.85;
        canvas.ds.scale = scale;
        canvas.ds.offset[0] = (w - gw * scale) / 2 - minX * scale;
        canvas.ds.offset[1] = (h - gh * scale) / 2 - minY * scale;
        canvas.setDirty(true, true);
      };

      const resetGraph = () => {
        if (!graph) return;
        if (!confirm("重置会清空当前画布，是否继续？")) return;
        graph.clear();
        buildDefaultGraph(graph, lastInitial, project);
        fitView();
        saved.value = false;
      };

      const addNode = (type) => {
        if (!graph) return;
        const n = LiteGraph.createNode(type);
        if (!n) { toast("创建节点失败"); return; }
        const rect = wrap.value.getBoundingClientRect();
        n.pos = [
          (rect.width / 2 - canvas.ds.offset[0]) / canvas.ds.scale - n.size[0] / 2,
          (rect.height / 2 - canvas.ds.offset[1]) / canvas.ds.scale - n.size[1] / 2
        ];
        graph.add(n);
        saved.value = false;
      };

      const saveCanvas = async () => {
        if (!graph) return;
        saving.value = true;
        try {
          const data = graph.serialize();
          await api("/api/projects/" + pid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ canvas_state: data }) });
          saved.value = true;
          toast("画布已保存");
        } catch (e) { toast("保存失败: " + e.message); }
        finally { saving.value = false; }
      };

      const findFirstNode = (type) => { if (!graph) return null; return graph._nodes.find((n) => n.type === type); };

      const collectParams = () => {
        const p = {};
        NODE_CATALOG.forEach((cat) => {
          const n = findFirstNode(cat.type);
          if (n) {
            const keys = Object.keys(n.properties || {});
            keys.forEach((k) => { p[k] = n.properties[k]; });
          }
        });
        return p;
      };

      const runWorkflow = async () => {
        if (!graph) return;
        running.value = true; ran.value = true; saved.value = false;
        const params = collectParams();
        const scriptNode = findFirstNode("manju/script");
        const theme = nodeParam(scriptNode, "theme", project.title || "");
        try {
          prog.stage = "planning"; prog.current = 0; prog.total = 0; prog.message = "规划中…";
          const fd = new FormData(); fd.append("text", theme); fd.append("mode", params.mode || "full");
          const r = await api("/api/projects/" + pid + "/plan", { method: "POST", body: fd });
          await pollStatus(r.task_id, (s) => Object.assign(prog, s));
          const prj = await api("/api/projects/" + pid);
          Object.assign(project, prj);

          prog.stage = "rendering"; prog.current = 0; prog.total = 0; prog.message = "生成中…";
          const fd2 = new FormData();
          fd2.append("scenes", JSON.stringify(prj.scenes || []));
          fd2.append("ckpt", params.ckpt || "");
          fd2.append("animated", params.animated === "否" ? "0" : "1");
          fd2.append("use_ip", params.use_ip === "是" ? "1" : "0");
          const r2 = await api("/api/projects/" + pid + "/render", { method: "POST", body: fd2 });
          await pollStatus(r2.task_id, (s) => Object.assign(prog, s));
          await saveCanvas();
          toast("工作流执行完成 🎉");
        } catch (e) { toast("执行失败: " + e.message); prog.stage = ""; }
        finally { running.value = false; }
      };

      const exportComfy = () => {
        const params = collectParams();
        let prompt = "anime illustration, 2d animation";
        let negative = "lowres, bad anatomy, watermark, text";
        if (project.scenes && project.scenes[0]) {
          prompt = project.scenes[0].prompt || prompt;
          negative = project.scenes[0].negative_prompt || negative;
        }
        const data = buildComfyWorkflow(params, prompt, negative);
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "comfyui_workflow_" + (project.title || "manju") + ".json";
        a.click();
        URL.revokeObjectURL(a.href);
        toast("已导出 ComfyUI 工作流 JSON");
      };

      let lastInitial = null;
      const loadInitial = async () => {
        const init = { ckpt: "MeinaMix_V5.1_bakedVAE.safetensors", ckpts: [], motion: "mm_sd_v15_v2", motions: [] };
        try {
          const m = await api("/api/models");
          if (m.checkpoints && m.checkpoints.length) { init.ckpts = m.checkpoints; init.ckpt = m.default || m.checkpoints[0]; }
          if (m.motion_model) { init.motion = m.motion_model; init.motions = [m.motion_model]; }
        } catch (e) {}
        try {
          const p = await api("/api/projects/" + pid);
          Object.assign(project, p);
          if (p.title) init.theme = p.title;
          if (p.canvas_state) init.canvas_state = p.canvas_state;
        } catch (e) {}
        return init;
      };

      onMounted(async () => {
        lastInitial = await loadInitial();
        graph = new LiteGraph.LGraph();
        canvas = new LiteGraph.LGraphCanvas(cv.value, graph);
        canvas.allow_dragcanvas = true;
        canvas.allow_interaction = true;
        canvas.allow_searchbox = true;
        cv.value.focus();

        if (lastInitial.canvas_state) {
          try { graph.configure(lastInitial.canvas_state); }
          catch (e) { buildDefaultGraph(graph, lastInitial, project); }
        } else {
          buildDefaultGraph(graph, lastInitial, project);
        }

        resizeCanvas();
        window.addEventListener("resize", resizeCanvas);
        graph.start();
        graph.onAfterChange = () => { saved.value = false; };
        nextTick(fitView);
      });

      return { pid, project, catalog, wrap, cv, running, ran, saving, saved, prog, fitView, resetGraph, addNode, saveCanvas, runWorkflow, exportComfy };
    },
  };

  /* =======================================================================
     组件 5~N：占位页面
     ======================================================================== */
  function makePlaceholder(icon, title, desc) {
    return {
      template: `<div class="placeholder-page"><div class="big">${icon}</div><h2>${title}</h2><p>${desc}</p><button class="btn primary" style="margin-top:16px" @click="$router.push('/projects')">返回项目中心</button></div>`
    };
  }
  const TaskCenter = makePlaceholder("📝", "任务中心", "这里将展示所有项目的生成任务队列与历史记录。");
  const Txt2Img = makePlaceholder("✨", "文生图", "输入提示词直接调用本地 ComfyUI 生成单张图片。");
  const Img2Img = makePlaceholder("🖼️", "图生图", "上传参考图并输入提示词进行图生图重绘。");
  const Creative = makePlaceholder("🎨", "创意工坊", "模板化创作：广告片、短视频、表情包等一键生成。");
  const Memoir = makePlaceholder("📖", "人生回忆录", "上传老照片与故事，自动生成怀旧风格漫剧。");
  const GlobalAssets = makePlaceholder("🌐", "全局资产库", "跨项目共享的角色、场景、道具素材库。");
  const Img2Prompt = makePlaceholder("🔍", "图片反推", "上传图片反推 Stable Diffusion 提示词。");
  const HistoryPage = makePlaceholder("🕒", "历史记录", "查看所有生成过的视频、图片与操作记录。");
  const Messages = makePlaceholder("💬", "消息中心", "系统通知、任务完成提醒、版本更新。");
  const SettingsPage = {
    template: `
    <div class="page">
      <div class="page-head"><h1>⚙️ 设置</h1><p class="muted">本地运行参数配置（演示数据，可随意填写）</p></div>
      <div class="ws-col" style="max-width:600px">
        <h3>用户资料</h3>
        <div class="field"><label>昵称</label><input class="input" value="小志" /></div>
        <div class="field"><label>工作室</label><input class="input" value="本地 AI 漫剧工坊" /></div>
        <h3 style="margin-top:16px">生成偏好</h3>
        <div class="field"><label>默认模型</label><select class="select"><option>Seedance 2.0 Mini</option><option>Realistic Vision</option></select></div>
        <div class="field"><label>默认分辨率</label><select class="select"><option>720p</option><option>1080p</option></select></div>
        <div class="field"><label>字幕字体</label><input class="input" value="Microsoft YaHei" /></div>
        <button class="btn primary" @click="toast('设置已保存（演示）')">保存设置</button>
      </div>
    </div>`
  };
  const HelpPage = makePlaceholder("❓", "帮助与反馈", "常见问题、快捷键、联系开发者。");
  const LogsPage = makePlaceholder("📋", "操作日志", "本地操作审计日志。");

  /* =======================================================================
     根组件 + 路由
     ======================================================================== */
  const App = {
    template: `
    <div id="app-shell">
      <Sidebar :pid="pid" />
      <main class="main"><router-view /></main>
      <div v-if="toastState.msg" class="toast">{{ toastState.msg }}</div>
    </div>`,
    setup() {
      const route = useRoute();
      const pid = computed(() => route.params.id || "");
      return { pid, toastState };
    },
  };

  const routes = [
    { path: "/", component: ProjectsList },
    { path: "/projects", component: ProjectsList },
    { path: "/tasks", component: TaskCenter },
    { path: "/txt2img", component: Txt2Img },
    { path: "/img2img", component: Img2Img },
    { path: "/creative", component: Creative },
    { path: "/memoir", component: Memoir },
    { path: "/global-assets", component: GlobalAssets },
    { path: "/img2prompt", component: Img2Prompt },
    { path: "/history", component: HistoryPage },
    { path: "/messages", component: Messages },
    { path: "/settings", component: SettingsPage },
    { path: "/help", component: HelpPage },
    { path: "/logs", component: LogsPage },
    { path: "/project/:id", component: DirectorWorkbench },
    { path: "/project/:id/assets", component: Assets },
    { path: "/project/:id/canvas", component: CanvasPage },
    { path: "/:pathMatch(.*)*", component: ProjectsList },
  ];
  const router = createRouter({ history: createWebHistory(), routes });

  const app = createApp(App);
  app.component("TopBar", TopBar);
  app.component("Sidebar", Sidebar);
  app.use(router);
  app.mount("#app");
})();
