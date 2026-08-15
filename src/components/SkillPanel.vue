<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listSkills, createSkill } from '../api'

const emit = defineEmits(['run'])
const skills = ref([])
const loading = ref(false)
const showCreate = ref(false)
const newName = ref('')
const newIntent = ref(
  JSON.stringify(
    {
      task_type: 'shot_txt2img',
      intent_tags: ['写实'],
      subjects: [{ asset_id: '{{subject_asset_id}}', role: 'identity', weight: 0.85 }],
      style_constraints: { lighting: '{{style_lighting}}' },
    },
    null,
    2
  )
)

async function load() {
  loading.value = true
  try {
    skills.value = await listSkills()
  } catch (e) {
    ElMessage.error('技能加载失败: ' + (e.message || e))
  } finally {
    loading.value = false
  }
}

function onDragStart(e, skill) {
  e.dataTransfer.setData('skillId', skill.skill_id)
}

async function onCreate() {
  try {
    const intent_template = JSON.parse(newIntent.value)
    await createSkill({ name: newName.value || '未命名技能', intent_template })
    ElMessage.success('技能已保存')
    showCreate.value = false
    newName.value = ''
    await load()
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.message || e))
  }
}

onMounted(load)
</script>

<template>
  <div class="skill-panel">
    <div class="sp-head">
      <span>🧩 技能库</span>
      <button class="sp-btn" title="新建技能" @click="showCreate = !showCreate">＋</button>
    </div>

    <div class="sp-list">
      <div
        v-for="s in skills"
        :key="s.skill_id"
        class="sp-card"
        draggable="true"
        @dragstart="onDragStart($event, s)"
        @dblclick="emit('run', s)"
      >
        <div class="sp-name">{{ s.name }}</div>
        <div class="sp-meta">
          <span class="sp-tag">{{ s.intent_template?.task_type }}</span>
          <span v-if="s.is_builtin" class="sp-badge">内置</span>
          <span v-else class="sp-badge user">我的</span>
        </div>
        <button class="sp-run" @click.stop="emit('run', s)">运行 ▶</button>
      </div>
    </div>

    <div v-if="showCreate" class="sp-create">
      <input v-model="newName" placeholder="技能名称" />
      <textarea v-model="newIntent" rows="7" spellcheck="false"></textarea>
      <button class="sp-btn primary" @click="onCreate">保存技能</button>
    </div>

    <div class="sp-hint">拖拽技能卡到画布，或点「运行」。</div>
  </div>
</template>

<style scoped>
.skill-panel {
  width: 220px;
  flex: 0 0 220px;
  border-right: 1px solid var(--line);
  padding: 12px 10px;
  overflow-y: auto;
  background: var(--bg-2);
}
.sp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 700;
  color: var(--txt);
  margin-bottom: 10px;
}
.sp-btn {
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--txt);
  border-radius: 8px;
  padding: 2px 10px;
  cursor: pointer;
}
.sp-btn.primary {
  background: var(--grad);
  color: #fff;
  border: 0;
  width: 100%;
  margin-top: 6px;
}
.sp-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sp-card {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 8px 10px;
  background: var(--card);
  cursor: grab;
  position: relative;
}
.sp-card:active {
  cursor: grabbing;
}
.sp-name {
  font-weight: 600;
  color: var(--txt);
}
.sp-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 4px 0;
}
.sp-tag {
  font-size: 11px;
  color: var(--sub);
  background: var(--bg-2);
  border-radius: 6px;
  padding: 1px 6px;
}
.sp-badge {
  font-size: 11px;
  color: #fff;
  background: #6d28d9;
  border-radius: 6px;
  padding: 1px 6px;
}
.sp-badge.user {
  background: #0ea5e9;
}
.sp-run {
  position: absolute;
  top: 8px;
  right: 8px;
  border: 0;
  background: var(--grad);
  color: #fff;
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 12px;
  cursor: pointer;
}
.sp-create {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sp-create input,
.sp-create textarea {
  background: var(--bg-2);
  color: var(--txt);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 8px;
  font-family: monospace;
  font-size: 12px;
}
.sp-hint {
  margin-top: 10px;
  font-size: 11px;
  color: var(--sub);
}
</style>
