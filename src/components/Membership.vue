<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchProfile } from '../api'
import { useUserProfile } from '../composables/useUserProfile'

// 知识库 13 章「个人纯本地版」+ §5.1 改造清单：
// 会员 三档(VIP/至尊VIP/至尊SVIP) → 单档(个人版·永久免费)；积分本地模拟默认值 1,999,995,162
const { nickname: userNickname, avatar: userAvatar } = useUserProfile()
const user = ref({
  name: userNickname.value,
  edition: '个人版',
  activated: true,
  valid: '永久',
  points: 1999995162,
})

onMounted(async () => {
  try {
    const data = await fetchProfile()
    user.value = {
      name: data.nickname || '小志',
      edition: data.membership_tier === 'personal' ? '个人版' : data.membership_tier,
      activated: true,
      valid: '永久',
      points: data.points_balance ?? 1999995162,
    }
  } catch (e) {
    console.warn('profile backend failed, use default', e.message)
  }
})

// 个人版功能权益清单（MB1.3）
const personalFeats = [
  '完全本地运行，数据不出本机',
  '全部 14 个功能页免费用',
  '全部 AI 模型接入（视频/指导板/文生图）',
  '资产库无限制（角色 / 场景 / 道具 / 音色）',
  '项目数量无限制',
  '操作日志 90 天本地保留',
  '自动备份到本地磁盘',
]

// 团队版预告（MB1.4，暂未开放）
const teamFeats = [
  '多人实时协作',
  '云端同步与跨设备',
  '团队管理后台',
  '详细审计日志与权限',
]

const showUpgrade = ref(true)
const closeUpgrade = () => { showUpgrade.value = false; ElMessage.success('已隐藏升级提示（演示）') }

const viewAgreement = (t) => ElMessage({ message: `${t}（演示）`, type: 'info' })
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1 class="p5-title">👑 会员中心</h1>
      <span class="p5-badge-star mem-star"><span>★</span></span>
      <span class="muted">万能短剧 · AI 创作平台 · 个人版</span>
    </div>

    <!-- 当前状态卡（MB1.1 / MB1.2） -->
    <div class="mem-status">
      <el-avatar :size="56" :src="userAvatar" style="background:var(--grad);font-weight:800" />
      <div class="mem-user">
        <div class="mem-name">
          {{ userNickname }}
          <el-tag type="success" effect="dark" style="margin-left:8px;border:0">个人版</el-tag>
        </div>
        <div class="muted">
          本地已激活 ✓ · 版本 v1.3.0 · 永久免费 ·
          当前积分：<b style="color:var(--txt)">{{ user.points.toLocaleString() }}</b>
        </div>
      </div>
      <el-button class="btn ghost" @click="ElMessage({message:'积分明细为演示功能',type:'info'})">积分明细</el-button>
    </div>

    <!-- 升级提示（可关闭，MB1.5） -->
    <div v-if="showUpgrade" class="upgrade-bar">
      <span>🎉 您当前使用的是 <b>个人版 · 永久免费</b>，全部功能已开放，无需充值。</span>
      <el-button size="small" @click="closeUpgrade">不再提示</el-button>
    </div>

    <!-- 权益对比（MB1.3 / MB1.4） -->
    <div class="plan-row">
      <!-- 个人版（当前） -->
      <div class="plan-card cur">
        <div class="plan-name">
          💎 个人版
          <el-tag size="small" effect="dark" type="success" style="border:0">当前使用</el-tag>
        </div>
        <div class="plan-sell">完全本地运行 · 永久免费</div>
        <ul class="plan-feats">
          <li v-for="(f, i) in personalFeats" :key="i" class="check-item ok"><span class="ck">✓</span>{{ f }}</li>
        </ul>
        <button class="btn" disabled style="width:100%;justify-content:center;margin-top:auto">✓ 当前版本</button>
      </div>

      <!-- 团队版（暂未开放） -->
      <div class="plan-card locked">
        <div class="plan-name">⚪ 团队版</div>
        <div class="plan-sell">多人协作 · 云端同步（暂未开放）</div>
        <ul class="plan-feats">
          <li v-for="(f, i) in teamFeats" :key="i" class="check-item"><span class="ck">·</span>{{ f }}</li>
        </ul>
        <button class="btn ghost" disabled style="width:100%;justify-content:center;margin-top:auto">敬请期待</button>
      </div>
    </div>

    <!-- 协议查看（MB1.6） -->
    <div class="card-block">
      <h4 class="blk-title">协议与说明</h4>
      <div class="agree-row">
        <el-button class="btn ghost sm" @click="viewAgreement('用户协议')">用户协议</el-button>
        <el-button class="btn ghost sm" @click="viewAgreement('隐私政策')">隐私政策</el-button>
        <span class="muted">本地优先：所有数据保存在本机，不上传云端。</span>
      </div>
    </div>

    <p class="note muted">
      说明：个人纯本地版不绑定充值 / 卡密 / 订单，所有功能无门槛开放；会员体系保留 UI 骨架，便于未来升级团队版时复用（依据知识库 13 章 §四 / §5.1）。
    </p>
  </div>
</template>

<style scoped>
.mem-status {
  display: flex; align-items: center; gap: 16px;
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  padding: 18px 20px; margin-bottom: 18px;
}
.mem-user { flex: 1; }
.mem-name { font-size: 18px; font-weight: 800; display: flex; align-items: center; }
.upgrade-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  background: rgba(61,220,132,.12); border: 1px solid rgba(61,220,132,.35);
  color: var(--txt); border-radius: 12px; padding: 12px 16px; margin-bottom: 20px; font-size: 13px;
}
.plan-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; }
.plan-card {
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  padding: 18px; display: flex; flex-direction: column; transition: .2s;
}
.plan-card.cur { border: 2px solid var(--pur); box-shadow: 0 0 0 3px rgba(138,92,255,.18); }
.plan-card.locked { opacity: .82; }
.plan-name { font-size: 17px; font-weight: 800; display: flex; align-items: center; gap: 8px; }
.plan-sell { font-size: 12px; color: var(--sub); min-height: 32px; line-height: 1.5; margin: 6px 0; }
.plan-feats { list-style: none; padding: 0; margin: 12px 0 16px; display: flex; flex-direction: column; gap: 8px; }
.card-block { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 18px 20px; margin-top: 22px; }
.blk-title { margin: 0 0 14px; font-size: 15px; }
.agree-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.note { margin-top: 18px; line-height: 1.7; font-size: 12px; }
.page-head { position: relative; }
.mem-star { position: absolute; top: -12px; right: 6px; }
</style>
