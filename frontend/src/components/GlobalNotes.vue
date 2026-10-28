<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'

const props = defineProps({ wsId: Number, reloadRev: { type: Number, default: 0 } })

const text = ref('')
const saved = ref('')        // 已保存版本（比对脏状态）
const loading = ref(true)
const saving = ref(false)
const error = ref('')
let timer = null             // 防抖自动保存

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api(`/api/workspaces/${props.wsId}/global-notes`)
    text.value = r.text || ''
    saved.value = text.value
  } catch (e) { error.value = e.message }
  loading.value = false
}
watch(() => props.wsId, load, { immediate: true })

// AI 应用 update_global_notes 后 reloadRev 自增：若当前无未保存编辑则自动重载，有则提示不覆盖
const externalNote = ref('')
watch(() => props.reloadRev, async () => {
  if (text.value !== saved.value) {
    externalNote.value = '全局约定已被 AI 更新；你有未保存的手动编辑，未自动刷新。请先保存或放弃当前编辑，再重新打开本页查看。'
    return
  }
  externalNote.value = ''
  await load()
})

async function save() {
  if (text.value === saved.value || saving.value) return
  saving.value = true
  try {
    await api(`/api/workspaces/${props.wsId}/global-notes`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text.value }),
    })
    saved.value = text.value
  } catch (e) { error.value = e.message }
  saving.value = false
}

// 输入防抖 1.2s 自动保存；失焦立即保存
function onInput() {
  clearTimeout(timer)
  timer = setTimeout(save, 1200)
}
onUnmounted(() => clearTimeout(timer))

// Ctrl+S 立即保存
function onKey(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save() }
}
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="gn-page">
    <div class="gn-head">
      <h2>📜 全局约定</h2>
      <span class="dim">跨场景的架构规则 / 外部系统约定 / 命名规范——写场景时沉淀到这里，对话 AI 与后台开发都会遵守</span>
      <span class="spacer"></span>
      <span v-if="saving" class="state saving">保存中…</span>
      <span v-else-if="text !== saved" class="state dirty">● 未保存（自动保存中）</span>
      <span v-else-if="saved" class="state ok">✓ 已保存</span>
    </div>

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="externalNote" class="ext-note">{{ externalNote }}</div>
    <div v-else-if="loading" class="dim pad">加载中…</div>
    <template v-else>
      <textarea
        v-model="text"
        class="gn-editor"
        placeholder="例：&#10;1. GitLab 分组定位规则：g4_projects/<产品线>_Projects/…&#10;2. 鉴权与降级策略：……&#10;3. 命名规范：……&#10;&#10;支持 markdown。输入停止 1.2s 或 Ctrl+S 自动保存。"
        @input="onInput"
        @blur="save"
      ></textarea>
      <div class="hint dim">
        这里的内容会：① 作为【项目全局约定】注入每轮 AI 对话上下文；② 开发真实工具时作为最高优先级约束放进开发 prompt。
        场景特有的内容不要写在这里（写进对应场景的 notes）。
      </div>
    </template>
  </div>
</template>

<style scoped>
.gn-page { max-width: 900px; display: flex; flex-direction: column; height: 100%; }
.gn-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.gn-head h2 { margin: 0; font-size: 18px; }
.gn-head .dim { font-size: 12.5px; }
.spacer { flex: 1; }
.state { font-size: 12.5px; }
.state.dirty { color: #b45309; }
.state.ok { color: #047857; }
.state.saving { color: var(--text-dim); }
.gn-editor { flex: 1; min-height: 55vh; border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; font-size: 13.5px; font-family: inherit; line-height: 1.7; resize: vertical; background: #fff; }
.hint { margin-top: 8px; font-size: 12px; line-height: 1.6; }
.err { color: #b91c1c; padding: 20px; }
.ext-note { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; border-radius: 8px; padding: 8px 14px; font-size: 12.5px; margin-bottom: 10px; }
.pad { padding: 20px; }
</style>
