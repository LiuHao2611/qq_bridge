import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: {
    outDir: 'frontend/dist',
    emptyOutDir: false,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api/v1': 'http://127.0.0.1:7788',
      '/api': 'http://localhost:8099',
      '/file': 'http://localhost:8099',
      '/uploads': 'http://localhost:8099',
    },
  },
})
