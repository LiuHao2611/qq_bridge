<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { submitFeedback } from '../api'

const docs = [
  {
    key: 'start', title: '快速开始', icon: '🚀',
    paras: [
      '欢迎使用「万能短剧 · AI 创作平台」。三步即可产出你的第一部竖屏短剧：创建项目 → 用 AI 生成分镜与画面 → 一键合成导出。',
      '建议先到「设置」中选定默认创作模型与文生图模型，这样后续所有生成都会沿用，避免反复切换。',
      '新手上路推荐从「创意工坊」挑一个模板一键套用，5 分钟内就能看到成片雏形。',
    ],
    code: '# 示例：一条最小工作流\n1. 新建项目：首页 → 项目中心 → 新建\n2. 生成分镜：导演工作台 → 智能写剧本\n3. 文生图：选中分镜 → 图生图 → 应用风格\n4. 导出：合成视频 → 1080x1920 竖屏',
  },
  {
    key: 'txt2img', title: '文生图教程', icon: '🎨',
    paras: [
      '文生图是把文字描述转成画面的最快方式。提示词建议遵循「主体 + 环境 + 风格 + 镜头」的结构。',
      '负向提示词用于排除不想要的元素，例如“模糊、多余手指、水印”。可在全局资产库中保存常用提示词片段复用。',
      '分辨率与步数越高越精细，但耗时更长；竖屏短剧推荐 576x1024 起步。',
    ],
    code: '正向：电影感竖屏，都市夜景，冷蓝色调，女孩红色风衣，浅景深\n负向：模糊, 畸形, 水印, 低分辨率\n参数：采样步数 28 · 引导系数 7.5 · 尺寸 576x1024',
  },
  {
    key: 'clip', title: '片段编辑', icon: '✂️',
    paras: [
      '在导演工作台中，每个分镜就是一个片段。你可以单独重绘、替换参考图、调整时长与转场。',
      '支持拖拽排序、批量选中后统一应用风格。时间轴模式下可精确对齐口播与画面。',
      '剪辑建议：黄金 3 秒放钩子，每 5-8 秒一个信息点，结尾预留关注引导。',
    ],
    code: '片段属性示例：\n- 时长：3.5s\n- 转场：淡入淡出\n- 配音：女声·温柔\n- 字幕：自动识别 + 手动校对',
  },
  {
    key: 'memoir', title: '人生回忆录', icon: '📖',
    paras: [
      '人生回忆录是面向家庭记忆的专题功能：上传老照片与口述文字，AI 自动生成旁白式短纪录片。',
      '支持年代滤镜（80/90 年代胶片感）与家人声音克隆，让回忆更有温度。',
      '生成后可导出为可分享的 H5 或视频，适合节日赠礼。',
    ],
    code: '回忆录模板结构：\n序章：老照片翻页动画\n正文：口述 + 时代空镜\n尾声：今昔对比 + 寄语',
  },
  {
    key: 'hotkey', title: '快捷键', icon: '⌨️',
    paras: [
      '熟练使用快捷键能显著提升剪辑效率。以下为工作台常用组合（可在设置中自定义）。',
      '提示：空格在大纲区用于播放/暂停预览，在输入框中则是普通空格。',
    ],
    code: 'Space      播放 / 暂停预览\nCtrl + S    保存项目\nCtrl + Z    撤销\nDel         删除选中分镜\n[  /  ]     上一片段 / 下一片段\nCtrl + K    唤起命令面板',
  },
  {
    key: 'faq', title: '常见问题 (FAQ)', icon: '❓',
    paras: [
      'Q：生成一直排队？A：并发任务数可在「设置 → 模型」中调高（最高 8），但会占用更多额度。',
      'Q：画面人物脸崩？A：开启“人脸修复”或在图生图阶段用参考图锁定角色一致性。',
      'Q：数据存在哪？A：项目文件存于本机浏览器缓存，清除缓存会丢失未导出内容，请提前导出。',
    ],
    code: '更多问题请提交反馈工单，工单状态可在本页“我的工单”查看。',
  },
]

const activeKey = ref('start')
const activeDoc = ref(docs[0])
const selectDoc = (d) => { activeKey.value = d.key; activeDoc.value = d }

const tickets = [
  { no: 'WD-20260801-042', type: '建议', status: '处理中', time: '08-01 14:22' },
  { no: 'WD-20260728-117', type: 'BUG', status: '已解决', time: '07-28 09:10' },
  { no: 'WD-20260725-088', type: '投诉', status: '待回复', time: '07-25 21:47' },
]
const statusType = (s) => s === '已解决' ? 'success' : s === '处理中' ? 'warning' : 'info'

const form = reactive({ type: '', title: '', desc: '', contact: '' })
const rules = {
  type: [{ required: true, message: '请选择反馈类型', trigger: 'change' }],
  title: [{ required: true, message: '请填写标题', trigger: 'blur' }],
  desc: [
    { required: true, message: '请填写描述', trigger: 'blur' },
    { min: 10, message: '描述至少 10 个字，便于我们定位问题', trigger: 'blur' },
  ],
}
const formRef = ref(null)
const submitting = ref(false)
const submit = () => {
  formRef.value.validate(async (ok) => {
    if (!ok) return
    submitting.value = true
    try {
      const fb = await submitFeedback({
        type: form.type,
        title: form.title.trim(),
        content: form.desc.trim(),
        contact_email: form.contact.trim(),
      })
      ElMessage.success('反馈已提交，工单号 ' + (fb?.id ? fb.id.slice(0, 12) : '') + '，我们会尽快处理')
      form.type = ''; form.title = ''; form.desc = ''; form.contact = ''
    } catch (e) {
      ElMessage.error('提交失败：' + (e.message || e))
    } finally {
      submitting.value = false
    }
  })
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>帮助与反馈</h1>
        <span class="muted">使用教程、快捷键速查，以及你的反馈入口</span>
      </div>
    </div>

    <div style="display:flex;gap:20px;align-items:flex-start">
      <!-- 左侧目录 -->
      <div style="width:200px;flex:0 0 200px">
        <div
          v-for="d in docs" :key="d.key"
          class="list-row" :style="{ cursor:'pointer', marginBottom:'8px', borderColor: activeKey===d.key ? 'var(--pur)' : 'var(--line)', background: activeKey===d.key ? '#1f2330' : 'var(--card)' }"
          @click="selectDoc(d)"
        >
          <span style="font-size:16px">{{ d.icon }}</span>
          <span style="font-weight:600">{{ d.title }}</span>
        </div>
      </div>

      <!-- 右侧内容 -->
      <div style="flex:1;min-width:0">
        <div style="background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px">
          <h2 style="margin:0 0 14px;font-size:18px">{{ activeDoc.title }}</h2>
          <p v-for="(p, i) in activeDoc.paras" :key="i" style="line-height:1.8;color:var(--txt);margin:0 0 12px">{{ p }}</p>
          <el-divider content-position="left">示例</el-divider>
          <pre class="code-block">{{ activeDoc.code }}</pre>
        </div>

        <!-- 我的工单 -->
        <h3 style="margin:22px 0 12px;font-size:15px;color:var(--sub)">我的工单</h3>
        <div v-for="tk in tickets" :key="tk.no" class="list-row">
          <span style="font-family:ui-monospace,monospace;color:var(--blue)">{{ tk.no }}</span>
          <el-tag size="small" effect="dark">{{ tk.type }}</el-tag>
          <el-tag size="small" :type="statusType(tk.status)" effect="dark">{{ tk.status }}</el-tag>
          <span class="t" style="margin-left:auto">{{ tk.time }}</span>
        </div>
      </div>

      <!-- 右下反馈表单 -->
      <div style="width:320px;flex:0 0 320px">
        <div style="background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;position:sticky;top:0">
          <h3 style="margin:0 0 14px;font-size:15px">提交反馈</h3>
          <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
            <el-form-item label="类型" prop="type">
              <el-select v-model="form.type" placeholder="请选择" style="width:100%">
                <el-option label="功能建议" value="feature" />
                <el-option label="Bug 反馈" value="bug" />
                <el-option label="咨询" value="question" />
                <el-option label="其他" value="other" />
              </el-select>
            </el-form-item>
            <el-form-item label="标题" prop="title">
              <el-input v-model="form.title" placeholder="一句话概括" />
            </el-form-item>
            <el-form-item label="描述" prop="desc">
              <el-input v-model="form.desc" type="textarea" :rows="4" placeholder="详细描述问题或建议（至少 10 字）" />
            </el-form-item>
            <el-form-item label="联系方式（选填）">
              <el-input v-model="form.contact" placeholder="邮箱 / 微信" />
            </el-form-item>
            <el-button type="primary" style="width:100%" :loading="submitting" @click="submit">提交反馈</el-button>
          </el-form>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.code-block {
  background: #0f1117; border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 14px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px; line-height: 1.7; color: var(--txt); white-space: pre-wrap; word-break: break-all;
  margin: 0;
}
</style>
