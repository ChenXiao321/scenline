<script setup>
import { ref } from 'vue'

const emit = defineEmits(['close', 'created'])

const mode = ref('import')   // import | empty | template
const name = ref('')
const path = ref('')
const desc = ref('')
const busy = ref(false)
const error = ref('')

const MODES = [
  { key: 'import', label: '导入场景文档', hint: '指定一份符合编写约定的 Markdown 场景文档，解析出角色、场景树与待确认项' },
  { key: 'empty', label: '手动添加', hint: '创建空项目，从「👥 角色定义」开始自己搭建角色与场景' },
  { key: 'template', label: '从模板创建', hint: '生成一份含全部约定结构的最小示例文档并导入，在此基础上修改' },
]

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function submit() {
  if (!name.value.trim()) { error.value = '请填写项目名称'; return }
  if (mode.value === 'import' && !path.value.trim()) { error.value = '请填写文档路径'; return }
  busy.value = true
  error.value = ''
  try {
    let r
    if (mode.value === 'import') {
      r = await api('/api/workspaces/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.value.trim(), path: path.value.trim(), description: desc.value.trim() }) })
    } else if (mode.value === 'empty') {
      r = await api('/api/workspaces', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.value.trim(), description: desc.value.trim() }) })
    } else {
      r = await api('/api/workspaces/import-template', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.value.trim(), description: desc.value.trim() }) })
    }
    emit('created', r.workspace_id)
  } catch (e) { error.value = e.message }
  busy.value = false
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="panel">
      <div class="head"><b>新建项目</b><span class="dim">项目 = 工作域：一套独立的角色库、场景树与工具原型</span></div>

      <div class="modes">
        <button v-for="m in MODES" :key="m.key" class="mode" :class="{ active: mode === m.key }" @click="mode = m.key; error = ''">
          <div class="m-label">{{ m.label }}</div>
          <div class="m-hint">{{ m.hint }}</div>
        </button>
      </div>

      <div class="form">
        <div class="row">
          <label>项目名称<span class="req">*</span></label>
          <input v-model="name" placeholder="如：某某项目业务场景" />
        </div>
        <div v-if="mode === 'import'" class="row">
          <label>场景文档路径<span class="req">*</span></label>
          <input v-model="path" placeholder="D:\path\to\业务场景梳理.md" />
        </div>
        <div class="row">
          <label>项目描述</label>
          <input v-model="desc" placeholder="可选" />
        </div>
      </div>

      <div v-if="error" class="error">{{ error }}</div>

      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="busy" @click="submit">{{ busy ? '创建中…' : '创建项目' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, .35); display: flex; align-items: flex-start; justify-content: center; padding-top: 10vh; z-index: 60; }
.panel { width: 620px; max-width: 94vw; background: #fff; border-radius: 12px; padding: 18px 20px; box-shadow: 0 12px 40px rgba(0,0,0,.18); }
.head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 14px; }
.head .dim { color: var(--text-dim); font-size: 12.5px; }
.modes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px; }
.mode { text-align: left; border: 1.5px solid var(--border); border-radius: 8px; padding: 10px 12px; background: var(--panel); cursor: pointer; }
.mode.active { border-color: var(--accent); background: var(--accent-soft); }
.m-label { font-weight: 600; font-size: 13.5px; margin-bottom: 4px; }
.m-hint { font-size: 12px; color: var(--text-dim); line-height: 1.5; }
.form { display: flex; flex-direction: column; gap: 10px; }
.row { display: grid; grid-template-columns: 110px 1fr; gap: 10px; align-items: center; }
.row label { font-size: 13.5px; }
.req { color: #dc2626; }
.row input { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13.5px; }
.error { color: #b91c1c; font-size: 13px; margin-top: 10px; }
.foot { display: flex; gap: 8px; margin-top: 16px; }
.spacer { flex: 1; }
.btn { padding: 6px 14px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn:disabled { opacity: .5; }
</style>
