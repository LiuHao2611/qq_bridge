<script setup>
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import SideBar from './components/SideBar.vue'
import TopBar from './components/TopBar.vue'
import { toastState } from './store'

const route = useRoute()
const pid = computed(() => route.params.id || '')
// 仅缓存表单类页面（保留已填写内容与参数），其余页面每次进入重建并重新拉取数据。
// 用 keep-alive 的 include 精确控制，避免 v-if/v-else 拆分包裹导致的 deactivate 崩溃。
const cachedViews = ['Txt2Img', 'Img2Img', 'Creative']
</script>

<template>
  <div id="app-shell">
    <SideBar :pid="pid" />
    <div class="app-right">
      <TopBar :pid="pid" />
      <main class="main">
        <router-view v-slot="{ Component }">
          <keep-alive :include="cachedViews" :max="5">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </main>
    </div>
    <div class="toast-wrap">
      <transition-group name="fade">
        <div v-if="toastState.msg" :key="toastState.msg" class="toast">{{ toastState.msg }}</div>
      </transition-group>
    </div>
  </div>
</template>
