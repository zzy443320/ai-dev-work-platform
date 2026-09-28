import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 构建产物直接落到 web/static/，由后端挂载在 /static 下。
// 入口文件沿用 v2.html 这个名字（`GET /` 直接返回它）—— 改名的收益不如保持
// 后端与既有用例的选择器稳定，所以留着历史名。
// 迁移期的旧版原生界面（index.html + app.js）已删除，现在只有这一份实现。
export default defineConfig({
  plugins: [vue()],
  // 关键：资源 URL 前缀必须是 /static/ —— 后端只把 web/static 挂在 /static 下，
  // 默认的 '/' 会让产物引用 /vue/app.js，而那个路径后端并不提供（实测 404）。
  base: '/static/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // 产物落到后端已挂载的 /static
    outDir: fileURLToPath(new URL('../web/static', import.meta.url)),
    emptyOutDir: false,          // 绝不删 web/static 里的其他文件（style.css 等）
    manifest: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./v2.html', import.meta.url)),
      output: {
        entryFileNames: 'vue/app.[hash].js',
        chunkFileNames: 'vue/[name].[hash].js',
        assetFileNames: 'vue/[name].[hash][extname]',
      },
    },
  },
  server: {
    port: 5173,
    // dev 模式下把接口转发到 FastAPI，避免跨域与端口写死
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/static': 'http://127.0.0.1:8765',
      '/attachments': 'http://127.0.0.1:8765',
      '/screenshots': 'http://127.0.0.1:8765',
    },
  },
})
