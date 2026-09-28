// 清掉上一轮的 Vue 构建产物，避免带 hash 的旧文件越堆越多。
//
// 只删本工程独占的产物：web/static/vue/、web/static/v2.html、web/static/.vite/。
// **绝不碰** web/static/style.css —— 那是手写的共享样式表，前后端都在用。
import { rm, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const staticDir = join(here, '..', '..', 'web', 'static')

await rm(join(staticDir, 'vue'), { recursive: true, force: true })
await mkdir(join(staticDir, 'vue'), { recursive: true })
await rm(join(staticDir, 'v2.html'), { force: true })
// vite 的 manifest 也一并清掉，避免残留旧 hash
await rm(join(staticDir, '.vite'), { recursive: true, force: true })

console.log('[clean] 已清空 web/static/vue/ 与 v2.html（style.css 未动）')
