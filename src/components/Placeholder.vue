<script setup>
import { ref, computed } from 'vue'
import { mockNotifications } from '../mock'
import { toast } from '../store'

const props = defineProps({
  icon: { type: String, default: '🚧' },
  title: { type: String, default: '功能开发中' },
  desc: { type: String, default: '' },
  variant: { type: String, default: 'soon' },
})

const tiers = [
  { name: '免费版', price: '¥0', color: '#8b93a7', feats: ['每日 3 次生成', '720p 导出', '基础模型', '本地存储'] },
  { name: '至尊 VIP', price: '¥39/月', color: '#e8c87a', hot: true, feats: ['无限次生成', '4K 导出', '全部 AI 模型', '云端同步', '优先队列'] },
  { name: '团队版', price: '¥199/月', color: '#8a5cff', feats: ['5 席位', '团队资产库', '协作评审', 'API 接入'] },
]
const assetTab = ref('角色')
const assetTabs = ['角色', '场景', '道具']
const notifs = ref(mockNotifications)
const settings = ref({ defaultModel: '创作旗舰 Pro', resolution: '1080p', concurrency: 2, autoSave: true })
</script>

<template>
  <div class="page">
    <!-- 会员中心：三档权益 -->
    <template v-if="variant === 'membership'">
      <div class="page-head"><h1>👑 会员中心</h1><span class="muted">当前：至尊 VIP（演示）</span></div>
      <div class="tier-grid">
        <div v-for="t in tiers" :key="t.name" class="tier-card" :class="{ hot: t.hot }" :style="{ '--tc': t.color }">
          <div v-if="t.hot" class="tier-flag">推荐</div>
          <div class="tier-name">{{ t.name }}</div>
          <div class="tier-price">{{ t.price }}</div>
          <ul class="tier-feats">
            <li v-for="f in t.feats" :key="f">✓ {{ f }}</li>
          </ul>
          <button class="btn" :class="{ 'btn-pink': t.hot }" @click="toast('升级（演示）')">立即开通</button>
        </div>
      </div>
    </template>

    <!-- 全局资产库：三 Tab -->
    <template v-else-if="variant === 'assets'">
      <div class="page-head"><h1>🌐 全局资产库</h1><span class="muted">跨项目共享素材</span></div>
      <div class="tabs">
        <div v-for="t in assetTabs" :key="t" class="tab" :class="{ active: assetTab === t }" @click="assetTab = t">{{ t }}</div>
      </div>
      <div class="asset-grid">
        <div v-for="i in 9" :key="i" class="asset-card">
          <div class="asset-ph"></div>
          <div class="info"><div class="name">{{ assetTab }}{{ i }}</div><div class="type">未上传 · 占位</div></div>
        </div>
      </div>
    </template>

    <!-- 消息中心：模拟通知 -->
    <template v-else-if="variant === 'messages'">
      <div class="page-head"><h1>💬 消息中心</h1><span class="muted">{{ notifs.length }} 条通知</span></div>
      <div class="msg-list">
        <div v-for="n in notifs" :key="n.id" class="msg-item">
          <span class="msg-dot" :class="'m-' + n.type"></span>
          <div class="msg-body"><div class="msg-title">{{ n.title }}</div><div class="msg-time">{{ n.time }}</div></div>
        </div>
      </div>
    </template>

    <!-- 设置：可交互占位 -->
    <template v-else-if="variant === 'settings'">
      <div class="page-head"><h1>⚙️ 设置</h1><span class="muted">演示数据，可随意填写</span></div>
      <div class="set-card">
        <div class="field"><label>默认创作模型</label>
          <el-select v-model="settings.defaultModel" style="width:240px">
            <el-option label="创作旗舰 Pro" value="创作旗舰 Pro" />
            <el-option label="创作旗舰 Max" value="创作旗舰 Max" />
          </el-select>
        </div>
        <div class="field"><label>默认分辨率</label>
          <el-select v-model="settings.resolution" style="width:240px">
            <el-option label="720p" value="720p" /><el-option label="1080p" value="1080p" /><el-option label="4K" value="4K" />
          </el-select>
        </div>
        <div class="field"><label>并发任务数</label><el-input-number v-model="settings.concurrency" :min="1" :max="8" /></div>
        <div class="field"><label>自动保存</label><el-switch v-model="settings.autoSave" /></div>
        <button class="btn" @click="toast('设置已保存（演示）')">保存设置</button>
      </div>
    </template>

    <!-- 通用占位 -->
    <template v-else>
      <div class="placeholder-page">
        <div class="big">{{ icon }}</div>
        <h2>{{ title }}</h2>
        <p class="muted">{{ desc }}</p>
        <span class="dev-tag">🚧 功能开发中</span>
        <button class="btn" style="margin-top:16px" @click="$router.push('/projects')">返回项目中心</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tier-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px; }
.tier-card { position: relative; background: var(--card); border: 1px solid var(--line); border-top: 3px solid var(--tc); border-radius: 16px; padding: 22px; }
.tier-card.hot { box-shadow: 0 10px 40px rgba(232,200,122,.15); }
.tier-flag { position: absolute; top: 14px; right: 14px; background: var(--tc); color: #111; font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 6px; }
.tier-name { font-size: 18px; font-weight: 800; color: var(--tc); }
.tier-price { font-size: 24px; font-weight: 800; margin: 8px 0 14px; }
.tier-feats { list-style: none; padding: 0; margin: 0 0 18px; }
.tier-feats li { font-size: 13px; color: var(--txt); padding: 6px 0; border-bottom: 1px solid var(--line); }
.asset-ph { aspect-ratio: 1; background: linear-gradient(135deg, #1c2230, #14161c); display: flex; align-items: center; justify-content: center; color: var(--sub); }
.msg-list { display: flex; flex-direction: column; gap: 8px; }
.msg-item { display: flex; align-items: center; gap: 12px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }
.msg-dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 10px; }
.msg-dot.m-success { background: var(--green); } .msg-dot.m-info { background: var(--blue); } .msg-dot.m-warn { background: var(--warn); }
.msg-title { font-size: 14px; } .msg-time { font-size: 12px; color: var(--sub); margin-top: 3px; }
.set-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px; max-width: 520px; }
.dev-tag { display: inline-block; margin-top: 14px; padding: 4px 12px; border-radius: 20px; background: var(--card-2); font-size: 12px; color: var(--sub); }
</style>
