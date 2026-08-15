<script setup>
import { computed, inject, ref, unref } from 'vue'
import { getNodeSchema, themeTokens } from '../schema/nodeTypes'
import { useEditor, getNodeByPath, getByPath, setByPath } from './useEditor'

const tree = inject('tree')
const { selectedPath } = useEditor()

const node = computed(() => (selectedPath.value ? getNodeByPath(unref(tree), selectedPath.value) : null))
const schema = computed(() => (node.value ? getNodeSchema(node.value.type) : []))

const semanticItems = computed(() => schema.value.filter((s) => !s.common))
const styleItems = computed(() => schema.value.filter((s) => s.common))

const activeField = ref(null)
function val(key) { return node.value ? getByPath(node.value, key) : '' }
function setVal(key, v) { if (node.value) setByPath(node.value, key, v) }
function onFocus(key) { activeField.value = key }
function resetField(key) {
  if (!node.value) return
  const parts = key.split('.')
  let o = node.value
  for (let i = 0; i < parts.length - 1; i++) { if (!o[parts[i]]) return; o = o[parts[i]] }
  delete o[parts[parts.length - 1]]
}
function insertToken(tok) {
  if (!activeField.value || !tok) return
  const name = tok.startsWith('--') ? tok : '--' + tok
  setVal(activeField.value, `var(${name})`)
}
const tokenModel = ref('')
</script>

<template>
  <div class="prop-panel">
    <div class="pp-head">属性面板</div>
    <div v-if="!node" class="pp-empty">在编辑模式下点击任意元素以编辑其文本与样式</div>
    <div v-else class="pp-body">
      <div class="pp-nodeinfo">
        <span class="pp-type">{{ node.type }}</span>
        <span class="pp-path">{{ selectedPath }}</span>
      </div>

      <template v-if="semanticItems.length">
        <div class="pp-group">属性</div>
        <div v-for="item in semanticItems" :key="item.key" class="pp-field" :class="{ active: activeField === item.key }">
          <label>{{ item.label }}</label>
          <el-input v-if="item.control === 'text'" :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" size="small" />
          <el-input v-else-if="item.control === 'textarea'" type="textarea" :rows="2" :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" size="small" />
          <el-color-picker v-else-if="item.control === 'color'" :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v || '')" @focus="onFocus(item.key)" />
          <el-slider v-else-if="item.control === 'slider'" :model-value="val(item.key)" :min="item.min" :max="item.max" :step="item.step || 1" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" />
          <el-select v-else-if="item.control === 'select'" :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" size="small" style="width:100%">
            <el-option v-for="opt in item.options" :key="String(opt)" :label="String(opt)" :value="opt" />
          </el-select>
          <el-switch v-else-if="item.control === 'switch'" :model-value="!!val(item.key)" @update:model-value="v => setVal(item.key, v)" />
          <el-input-number v-else-if="item.control === 'number'" :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v)" size="small" />
          <button class="pp-reset" type="button" @click="resetField(item.key)">删除值</button>
        </div>
      </template>

      <template v-if="styleItems.length">
        <div class="pp-group">样式</div>
        <div v-for="item in styleItems" :key="item.key" class="pp-field" :class="{ active: activeField === item.key }">
          <label>{{ item.label }}</label>
          <el-input v-if="item.control === 'text'" :model-value="val(item.key)" :placeholder="item.placeholder" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" size="small" />
          <el-color-picker v-else-if="item.control === 'color'" :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v || '')" @focus="onFocus(item.key)" />
          <el-slider v-else-if="item.control === 'slider'" :model-value="val(item.key)" :min="item.min" :max="item.max" :step="item.step || 1" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" />
          <el-select v-else-if="item.control === 'select'" :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" size="small" style="width:100%">
            <el-option v-for="opt in item.options" :key="String(opt)" :label="String(opt)" :value="opt" />
          </el-select>
          <el-input v-else :model-value="val(item.key)" @update:model-value="v => setVal(item.key, v)" @focus="onFocus(item.key)" size="small" />
          <button class="pp-reset" type="button" @click="resetField(item.key)">删除值</button>
        </div>
      </template>

      <div class="pp-group">主题令牌</div>
      <div class="pp-token">
        <span class="pp-hint">聚焦某字段后，选择令牌插入主题色：</span>
        <el-select v-model="tokenModel" placeholder="选择 var(--token)" size="small" style="width:100%" @change="insertToken">
          <el-option v-for="t in themeTokens" :key="t" :label="t" :value="t" />
        </el-select>
      </div>
    </div>
  </div>
</template>

<style scoped>
.prop-panel { width: 300px; flex: 0 0 300px; background: var(--panel); border-left: 1px solid var(--line); display: flex; flex-direction: column; height: 100%; position: relative; z-index: 3000; }
.pp-head { font-size: 14px; font-weight: 800; color: var(--txt); padding: 14px 16px; border-bottom: 1px solid var(--line); }
.pp-empty { padding: 24px 16px; color: var(--sub); font-size: 13px; line-height: 1.6; }
.pp-body { flex: 1; overflow-y: auto; padding: 12px 16px; }
.pp-nodeinfo { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
.pp-type { font-size: 11px; padding: 2px 8px; border-radius: 6px; background: var(--pur); color: #fff; font-weight: 700; }
.pp-path { font-size: 11px; color: var(--sub); font-family: var(--font-mono, monospace); word-break: break-all; }
.pp-group { font-size: 12px; color: var(--sub); letter-spacing: 1px; margin: 14px 0 8px; border-top: 1px dashed var(--line); padding-top: 10px; }
.pp-group:first-child { border-top: 0; padding-top: 0; }
.pp-field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; padding: 6px; border-radius: 8px; }
.pp-field.active { background: rgba(138,92,255,.08); outline: 1px solid var(--pur); }
.pp-field label { font-size: 12px; color: var(--sub); }
.pp-reset { align-self: flex-start; font-size: 11px; color: var(--pink); background: transparent; border: 0; cursor: pointer; padding: 0; }
.pp-token { display: flex; flex-direction: column; gap: 6px; }
.pp-hint { font-size: 11px; color: var(--sub); }
</style>
