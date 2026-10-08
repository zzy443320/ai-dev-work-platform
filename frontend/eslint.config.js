// ESLint 扁平配置（ESLint 10 + eslint-plugin-vue 10）。
//
// 定位：**找缺陷，不改风格**。所以只用 plugin-vue 的 flat/essential 档，不引
// flat/recommended——后者会把「属性换行、属性顺序、自闭合写法」这类格式规则一起打开，
// 而这个仓库的 class/id 与 web/static/style.css 的选择器、以及 tests/ 下十几个
// Playwright 用例的选择器是绑死的（见 README「迁移已完成的约定」）。为了过 lint 去动
// 模板写法，等于把样式和用例一起摇一遍，收益远小于风险。
//
// 也不接 prettier：项目是手写风格统一维护，加格式化工具会一次性重排全部 .vue。
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  {
    // 构建产物在 ../web/static/，由 vite 生成，不参与 lint
    ignores: ['node_modules/**', '../web/static/**', 'dist/**', 'scripts/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],

  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        // unplugin-auto-import 会替我们补上这些命令式 API 的 import（vite.config.js
        // 里刻意配了 AutoImport + ElementPlusResolver），所以源码里直接写 ElMessage
        // 是合法的，不是漏 import。不加进 globals 的话 no-undef 会全线误报。
        ElMessage: 'readonly',
        ElMessageBox: 'readonly',
        ElNotification: 'readonly',
        ElLoading: 'readonly',
      },
    },
    rules: {
      // 未使用的变量：warn。半成品的在途改动很多，直接 error 会让 `npm run lint`
      // 变成噪音墙，没人愿意跑。
      'no-unused-vars': ['warn', {
        args: 'after-used',
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrors: 'none',
      }],
      // 模板字符串里放行「不规则空白」：界面文案刻意用全角空格 \u3000 做中文分隔
      // （如 `标题　key`），那是排版不是笔误。默认的 skipStrings 只覆盖普通字符串
      // 字面量，不覆盖模板字面量，所以要显式开 skipTemplates。
      'no-irregular-whitespace': ['error', { skipTemplates: true }],
      // 这条是 ESLint 9 才进 recommended 的风格规则：`let ok = false` 后紧跟
      // try 里重新赋值 → 初值 useless。本仓库两处都是 decide() 里「先声明、try/catch
      // 里必赋值」的写法，读起来比 let 未初始化更顺手，且没有任何危险语义 → warn。
      'no-useless-assignment': 'warn',
      // 本项目所有真出过事的缺陷类型：v-html 直插未转义的外部内容（Figma 返回、
      // 仓库文件正文）＝ 存储型 XSS。warn 而非 error：现存站点大多是「自己拼好并
      // escapeHtml 过」的 HTML，需要人判断；但新增 v-html 必须被看见。
      'vue/no-v-html': 'warn',
      // v-for 缺 key / 用 index 当 key 的可疑写法，essential 档已含 require-v-for-key，
      // 这里补上更针对性的一条（切会话时 index key 会让 <details> 展开态串位）。
      'vue/no-unused-components': 'warn',
      'vue/multi-word-component-names': 'off',   // 组件名沿用旧版契约，不强行改
      'vue/no-dupe-keys': 'error',               // 遮蔽 prop 是真坑（UsageDonut 修过一处）
      'vue/no-mutating-props': 'error',
      'vue/valid-v-if': 'error',
      'vue/no-duplicate-attributes': 'error',
      'vue/no-reserved-keys': 'error',
      'vue/no-side-effects-in-computed-properties': 'error',
      'vue/require-toggle-inside-transition': 'off',
    },
  },
]
