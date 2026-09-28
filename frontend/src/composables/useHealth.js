// 健康检查（顶栏 #health 副标题）。旧版是 app.js 里的全局 loadHealth()，
// 三个地方会调它：启动时、跑完任务后（AI 模式可能变化）、采纳/撤销后。
// Vue 里它天然是跨组件共享状态（顶栏在 App.vue，写它的在三个任务页签和
// 产出物面板），所以抽成模块级单例（同 useToast / useRunStatus 的做法）。
import { ref } from 'vue'
import { api } from '../api/client.js'

const health = ref(null)
const healthError = ref('')

/** 拉一次健康检查。失败时记下原因（顶栏会显示「连接失败」并盖过运行态） */
async function loadHealth() {
  try {
    health.value = await api.health()
    healthError.value = ''
  } catch (e) {
    healthError.value = e && e.message ? e.message : String(e)
  }
}

export function useHealth() {
  return { health, healthError, loadHealth }
}
