import { ref, computed } from 'vue'

// 项目级「系统步骤显示名」：库里 actor 固定存「系统」，仅在界面显示层替换为项目自定义词（如「GLink」）。
// 由 App.vue 在打开项目时调用 setFromWorkspace() 注入；各组件用 useSysLabel() 取显示值。
const label = ref('系统')

export function setSysLabel(settings) {
  // settings 可能是 JSON 字符串（后端 sqlite 存储格式），先解析
  let obj = settings
  if (typeof settings === 'string') {
    try { obj = JSON.parse(settings) } catch { obj = {} }
  }
  const v = (obj && typeof obj === 'object' ? obj.system_label : '') || ''
  label.value = String(v).trim() || '系统'
}

export function useSysLabel() {
  const sysLabel = computed(() => label.value)
  // 匹配逻辑：数据库存的是「系统」，匹配时也要认「系统」
  const isSys = (actor) => actor === '系统' || actor === label.value
  // 显示替换：把 actor 名映射为显示词
  const displayActor = (actor) => (actor === '系统' ? label.value : actor)
  return { sysLabel, isSys, displayActor }
}
