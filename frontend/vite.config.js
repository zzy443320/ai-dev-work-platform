import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'

// 构建产物直接落到 web/static/，由后端挂载在 /static 下。
// 入口文件沿用 v2.html 这个名字（`GET /` 直接返回它）—— 改名的收益不如保持
// 后端与既有用例的选择器稳定，所以留着历史名。
// 迁移期的旧版原生界面（index.html + app.js）已删除，现在只有这一份实现。
export default defineConfig({
  plugins: [
    vue(),

    // ---- Element Plus 按需引入 ----
    // 目的：只把**真正用到**的组件与其样式打进产物。全量 `app.use(ElementPlus)`
    // 会一次性带上 ~1MB 的 JS + CSS，首屏明显变慢。
    //
    // AutoImport 负责「命令式 API」：代码里直接写 ElMessage.success(...) /
    // ElMessageBox.confirm(...) 而**不写 import** —— 插件会补上 import 与它的
    // 样式。这是刻意的（否则那些 API 会没样式），所以看到没 import 的 ElMessage
    // 不要以为是漏了。
    AutoImport({
      resolvers: [ElementPlusResolver()],
      dts: false,        // 纯 JS 工程，不生成 auto-imports.d.ts
    }),

    // Components 负责「模板里的标签」：<el-button> / <el-dialog> ... 用到才注册。
    // dirs 置空是刻意的：本项目自己的组件一律显式 import，不走全局自动注册，
    // 免得同名组件被静默顶掉。
    Components({
      resolvers: [ElementPlusResolver()],
      dirs: [],
      dts: false,
    }),
  ],

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
        // Element Plus 与 Vue 各自切一个 chunk：它们的版本变化频率远低于业务代码，
        // 分开之后用户更新版本时只需要重新下载 app.*.js（入口从 ~570KB 降到 ~320KB）。
        // 注意 entry 仍然只有一个 app.*.js —— tests/check_vue_migration.py 断言这一点。
        manualChunks(id) {
          if (id.includes('node_modules/element-plus') ||
              id.includes('node_modules/@element-plus')) return 'element-plus'
          if (id.includes('node_modules/@vue/') || id.includes('node_modules/vue/')) return 'vue'
        },
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
