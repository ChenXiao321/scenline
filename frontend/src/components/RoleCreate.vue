<script setup>
import { ref, reactive } from 'vue'

const props = defineProps({ wsId: Number })
const emit = defineEmits(['close', 'created'])

const saving = ref(false)
const error = ref('')
const form = reactive({ name: '', abbr: '', core_work: '', permissions: '' })

async function save() {
  if (!form.name.trim()) { error.value = '角色名称不能为空'; return }
  saving.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/workspaces/${props.wsId}/roles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
    const r = await res.json()
    emit('created', r.id)
  } catch (e) { error.value = e.message }
  saving.value = false
}
</script>

<template>
  <div class="new-role">
    <div class="head">
      <b>新增角色</b>
      <span class="dim">加入当前工作域的角色库，供场景步骤归属与工具原型选用</span>
    </div>
    <div v-if="error" class="err">{{ error }}</div>
    <div class="edit-form">
      <div class="frow"><label>角色名称 *</label><input v-model="form.name" placeholder="如：软件集成工程师" /></div>
      <div class="frow"><label>英文简称</label><input v-model="form.abbr" placeholder="如 SW Integration Engineer" /></div>
      <div class="frow"><label>核心工作</label><textarea v-model="form.core_work" rows="2"></textarea></div>
      <div class="frow"><label>权限特点</label><textarea v-model="form.permissions" rows="2"></textarea></div>
      <div class="fops">
        <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '创建角色' }}</button>
        <button class="btn" @click="emit('close')">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.new-role { max-width: 760px; }
.head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.dim { color: var(--text-dim); font-size: 12.5px; }
.err { color: #b91c1c; font-size: 12.5px; margin-bottom: 8px; }
.edit-form { border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: var(--panel); display: flex; flex-direction: column; gap: 10px; }
.frow { display: grid; grid-template-columns: 90px 1fr; gap: 10px; align-items: start; }
.frow label { font-size: 13px; color: var(--text-dim); padding-top: 6px; }
.frow input, .frow textarea { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13.5px; font-family: inherit; resize: vertical; }
.fops { display: flex; gap: 8px; margin-top: 2px; }
.btn { padding: 5px 12px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
</style>
