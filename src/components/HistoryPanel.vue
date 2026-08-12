<script setup>
// 左侧默认折叠的「历史生成记录」面板。
// 用法：<HistoryPanel ref="hp" :storage-key="genKey.xxx(pid)" title="提取历史" kind-filter="text" @use="onUse" />
// - 默认 collapsed：仅左侧一条竖排标签；点击展开为 240px 面板。
// - 每次模型生成成功后，宿主调用 pushHistory(key, record) 再 hp.value.refresh() 即可刷新列表。
// - 点击某条记录的「覆盖使用」会 emit('use', record)，由宿主决定如何回填/覆盖。
import { ref, onMounted } from 'vue'
import { draftAge } from '../composables/useDraft'
import { loadHistory, clearHistory } from '../composables/useGenHistory'

const props = defineProps({
  storageKey: { type: String, required: true },
  title: { type: String, default: '历史生成' },
  kindFilter: { type: String, default: 'all' }, // all | text | image | video
  collapsed: { type: Boolean, default: true },
  embedded: { type: Boolean, default: false }, // true: 作为父容器（如弹窗）左侧一栏，不再 fixed 锚定视口最左
  floating: { type: Boolean, default: false }, // true: 页面级用法，固定在内容区左侧（避开 232px 侧栏），不锚定视口最左
})
const emit = defineEmits(['use'])

const isCollapsed = ref(props.collapsed)
const items = ref([])

function refresh() {
  let list = loadHistory(props.storageKey)
  if (props.kindFilter !== 'all') list = list.filter((it) => it.kind === props.kindFilter)
  items.value = list
}
function onUse(it) { emit('use', it) }
function clearAll() {
  clearHistory(props.storageKey)
  items.value = []
}
onMounted(refresh)
defineExpose({ refresh })
</script>

<template>
  <div class="hist-root" :class="{ collapsed: isCollapsed, embedded: embedded, floating: floating }">
    <button v-if="isCollapsed" class="hist-tab" type="button" @click="isCollapsed = false" :title="title">
      🕘 {{ title }}
    </button>
    <aside v-else class="hist-box">
      <div class="hist-head">
        <span class="hist-title">🕘 {{ title }}</span>
        <div class="hist-acts">
          <button class="hist-x" type="button" title="清空" @click="clearAll">🗑</button>
          <button class="hist-x" type="button" title="折叠" @click="isCollapsed = true">✕</button>
        </div>
      </div>
      <div class="hist-list">
        <div v-if="!items.length" class="hist-empty">暂无历史生成记录</div>
        <div v-for="it in items" :key="it.id" class="hist-item">
          <div class="hist-item-top">
            <span class="hist-kind" :class="it.kind">{{ { text: '文', image: '图', video: '视' }[it.kind] || '?' }}</span>
            <span class="hist-time">{{ draftAge(it.ts) }}</span>
          </div>
          <div class="hist-title">{{ it.title }}</div>
          <div v-if="it.kind === 'text'" class="hist-prev-text">{{ it.preview }}</div>
          <img v-else-if="it.kind === 'image' && it.preview" :src="it.preview" class="hist-prev" alt="历史图" loading="lazy" />
          <div v-else-if="it.kind === 'video'" class="hist-prev video">
            <img v-if="it.preview" :src="it.preview" alt="历史视频" loading="lazy" />
            <div v-else class="hist-video-ph">🎬 {{ it.previewText || '视频' }}</div>
            <span class="hist-play">▶</span>
          </div>
          <div v-else class="hist-prev-text">{{ it.preview || it.previewText || '' }}</div>
          <button class="hist-use" type="button" @click="onUse(it)">↩ 覆盖使用</button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.hist-root { position: fixed; left: 0; top: 88px; z-index: 2600; }
/* 嵌入模式：作为父容器（如弹窗）左侧一栏，而非 fixed 锚定视口最左 */
.hist-root.embedded { position: static; z-index: auto; align-self: stretch; display: flex; }
.hist-root.embedded .hist-tab {
  align-self: flex-start; margin: 0;
  border-radius: 0 10px 10px 0; border-left: none;
}
.hist-root.embedded .hist-box {
  width: 248px; height: 100%; max-height: 100%;
  border-radius: 0; border-top: none; border-bottom: none; border-left: none;
  border-right: 1px solid var(--line); box-shadow: none;
}
/* 浮层模式：页面级用法，固定在内容区左侧（避开 232px 侧栏），不锚定视口最左、不压住导航 */
.hist-root.floating { position: fixed; left: 232px; top: 88px; z-index: 2600; }
.hist-root.floating .hist-box { max-height: calc(100vh - 104px); }
.hist-tab {
  writing-mode: vertical-rl; text-orientation: mixed;
  background: var(--card, #fff); color: var(--txt, #222);
  border: 1px solid var(--line, #e3e3e8); border-left: none;
  border-radius: 0 10px 10px 0; padding: 14px 6px; font-size: 13px; font-weight: 700;
  cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,.12);
}
.hist-tab:hover { color: var(--pur, #8a5cff); border-color: var(--pur, #8a5cff); }
.hist-box {
  width: 248px; max-height: calc(100vh - 120px); display: flex; flex-direction: column;
  background: var(--card, #fff); border: 1px solid var(--line, #e3e3e8); border-left: none;
  border-radius: 0 12px 12px 0; box-shadow: 0 8px 28px rgba(0,0,0,.18); overflow: hidden;
}
.hist-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--line, #e3e3e8);
  background: var(--card-2, #f6f6fa);
}
.hist-title { font-size: 13px; font-weight: 800; }
.hist-acts { display: flex; gap: 4px; }
.hist-x {
  border: none; background: transparent; cursor: pointer; font-size: 13px;
  width: 24px; height: 24px; border-radius: 6px; color: var(--sub, #888);
}
.hist-x:hover { background: var(--card, #fff); color: var(--txt, #222); }
.hist-list { overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
.hist-empty { color: var(--muted, #999); font-size: 12px; text-align: center; padding: 24px 8px; }
.hist-item {
  border: 1px solid var(--line, #e3e3e8); border-radius: 10px; padding: 8px;
  background: var(--card-2, #f6f6fa);
}
.hist-item-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.hist-kind {
  width: 20px; height: 20px; border-radius: 6px; font-size: 11px; font-weight: 800;
  display: inline-flex; align-items: center; justify-content: center; color: #fff;
}
.hist-kind.text { background: #4a90e2; }
.hist-kind.image { background: #e2777a; }
.hist-kind.video { background: #8a5cff; }
.hist-time { font-size: 11px; color: var(--muted, #999); }
.hist-title { font-size: 12px; font-weight: 700; margin-bottom: 4px; word-break: break-all; }
.hist-prev-text {
  font-size: 11px; color: var(--sub, #666); max-height: 48px; overflow: hidden;
  background: var(--card, #fff); border-radius: 6px; padding: 6px; margin-bottom: 6px;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
}
.hist-prev { width: 100%; border-radius: 8px; overflow: hidden; margin-bottom: 6px; display: block; }
.hist-prev.img, .hist-prev img { width: 100%; max-height: 130px; object-fit: cover; display: block; }
.hist-prev.video { position: relative; }
.hist-prev.video img { width: 100%; max-height: 130px; object-fit: cover; display: block; }
.hist-video-ph {
  width: 100%; height: 96px; display: flex; align-items: center; justify-content: center;
  background: #2a2440; color: #cfc6ff; font-size: 13px; border-radius: 8px;
}
.hist-play {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 34px; height: 34px; border-radius: 50%; background: rgba(0,0,0,.55); color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 13px; pointer-events: none;
}
.hist-use {
  width: 100%; border: 1px solid var(--pur, #8a5cff); color: var(--pur, #8a5cff);
  background: transparent; border-radius: 8px; padding: 6px; font-size: 12px; font-weight: 700;
  cursor: pointer; transition: .15s;
}
.hist-use:hover { background: var(--pur, #8a5cff); color: #fff; }
</style>
