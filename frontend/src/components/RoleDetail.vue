<script setup>
import { ref, watch, computed, reactive } from 'vue'
import { useSysLabel } from '../composables/sysLabel.js'

const props = defineProps({ wsId: Number, role: Object })  // role 为 null 表示「系统」；undefined 场景不会进来
const emit = defineEmits(['open-scenario', 'changed'])

const { sysLabel, isSys } = useSysLabel()

const loading = ref(true)
const scenarios = ref([])   // 场景详情列表
const error = ref('')
const editing = ref(false)
const saving = ref(false)
const form = reactive({ name: '', abbr: '', core_work: '', permissions: '' })

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function load() {
  scenarios.value = []
  error.value = ''
  const list = await api(`/api/workspaces/${props.wsId}`)
  const details = await Promise.all(
    list.scenarios.map(s => api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(s.code)}`).catch(() => null))
  )
  scenarios.value = details.filter(Boolean)
}
watch(() => [props.wsId, props.role?.name], () => { editing.value = false; loading.value = true; load().catch(e => error.value = e.message).finally(() => loading.value = false) }, { immediate: true })

defineExpose({ reload: load })

const roleName = computed(() => props.role?.name ?? sysLabel.value)

function startEdit() {
  Object.assign(form, { name: props.role.name, abbr: props.role.abbr, core_work: props.role.core_work, permissions: props.role.permissions })
  error.value = ''
  editing.value = true
}

async function save() {
  if (!form.name.trim()) { error.value = '角色名称不能为空'; return }
  saving.value = true
  error.value = ''
  try {
    await api(`/api/workspaces/${props.wsId}/roles/${props.role.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) })
    editing.value = false
    emit('changed', { ...form, id: props.role.id })
  } catch (e) { error.value = e.message }
  saving.value = false
}

async function remove() {
  if (!confirm(`确定删除角色「${props.role.name}」？（不影响已导入场景的文本，仅影响后续归属与选择）`)) return
  error.value = ''
  try {
    await api(`/api/workspaces/${props.wsId}/roles/${props.role.id}`, { method: 'DELETE' })
    emit('changed', null)  // null = 当前角色被删除
  } catch (e) { error.value = e.message }
}

function stepMatches(sc) {
  // 系统角色匹配：数据库存「系统」，但显示名可能是「GLink」等
  const isSystemRole = props.role === null
  const hit = (s) => isSystemRole ? isSys(s.actor) : (s.actor || '') === roleName.value
  const main = (sc.data.steps || []).filter(hit)
  const byVariant = (sc.data.variants || []).map(v => ({ name: v.name, steps: (v.steps || []).filter(hit) })).filter(v => v.steps.length)
  return { main, byVariant }
}

const linked = computed(() => {
  const out = []
  for (const sc of scenarios.value) {
    const asMainUser = (sc.data.main_users || []).includes(roleName.value)
    const { main, byVariant } = stepMatches(sc)
    const stepCount = main.length + byVariant.reduce((n, v) => n + v.steps.length, 0)
    if (!asMainUser && !stepCount) continue
    out.push({ code: sc.code, title: sc.title, domain: sc.domain, asMainUser, main, byVariant, stepCount })
  }
  return out.sort((a, b) => (b.asMainUser - a.asMainUser) || parseFloat(a.code) - parseFloat(b.code))
})
</script>

<template>
  <div class="role-detail">
    <div class="head">
      <span class="avatar">{{ role?.name ? role.name[0] : '系' }}</span>
      <div>
        <div class="name">{{ roleName }}</div>
        <div v-if="role?.abbr && !editing" class="abbr">{{ role.abbr }}</div>
      </div>
      <span class="spacer"></span>
      <template v-if="role && !editing">
        <button class="btn" @click="startEdit">✎ 编辑</button>
        <button class="btn danger" @click="remove">删除</button>
      </template>
    </div>

    <div v-if="error" class="err">{{ error }}</div>

    <!-- 编辑态 -->
    <div v-if="editing" class="edit-form">
      <div class="frow"><label>角色名称 *</label><input v-model="form.name" /></div>
      <div class="frow"><label>英文简称</label><input v-model="form.abbr" placeholder="如 SW Integration Engineer" /></div>
      <div class="frow"><label>核心工作</label><textarea v-model="form.core_work" rows="2"></textarea></div>
      <div class="frow"><label>权限特点</label><textarea v-model="form.permissions" rows="2"></textarea></div>
      <div class="fops">
        <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
        <button class="btn" @click="editing = false">取消</button>
      </div>
    </div>

    <template v-else-if="role">
      <section v-if="role.core_work">
        <h4>核心工作</h4>
        <p>{{ role.core_work }}</p>
      </section>
      <section v-if="role.permissions">
        <h4>权限特点</h4>
        <p>{{ role.permissions }}</p>
      </section>
    </template>
    <p v-else-if="!role" class="dim">「{{ sysLabel }}」表示流程中由工具自动执行的步骤（非人物角色，名称可在侧栏 ⚙ 自定义）。</p>

    <section v-if="!editing">
      <h4>涉及场景（{{ linked.length }}）</h4>
      <div v-if="loading" class="dim">分析中…</div>
      <div v-else-if="!linked.length" class="dim">未在任何场景的主要用户或流程步骤中出现。</div>
      <div v-for="l in linked" :key="l.code" class="sc-link" @click="emit('open-scenario', l.code)">
        <div class="sc-line">
          <span class="code">{{ l.code }}</span>
          <span class="title">{{ l.title }}</span>
          <span v-if="l.asMainUser" class="tag main">主要用户</span>
          <span v-if="l.stepCount" class="tag steps">{{ l.stepCount }} 步</span>
        </div>
        <div v-if="l.stepCount" class="sc-steps">
          主流程 {{ l.main.length }} 步<template v-for="v in l.byVariant" :key="v.name"> · {{ v.name }} {{ v.steps.length }} 步</template>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.role-detail { display: flex; flex-direction: column; gap: 4px; max-width: 760px; }
.head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; background: var(--accent-soft); color: var(--accent); display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 700; flex-shrink: 0; }
.name { font-size: 15px; font-weight: 700; }
.abbr { font-size: 12px; color: var(--text-dim); }
.spacer { flex: 1; }
.btn { padding: 5px 12px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.danger { color: #dc2626; border-color: #fecaca; }
section { margin-top: 10px; }
h4 { margin: 0 0 4px; font-size: 12.5px; color: var(--text-dim); }
p { margin: 0; font-size: 13px; line-height: 1.6; white-space: pre-wrap; }
.dim { color: var(--text-dim); font-size: 12.5px; }
.err { color: #b91c1c; font-size: 12.5px; }
.edit-form { margin-top: 8px; border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: var(--panel); display: flex; flex-direction: column; gap: 10px; }
.frow { display: grid; grid-template-columns: 90px 1fr; gap: 10px; align-items: start; }
.frow label { font-size: 13px; color: var(--text-dim); padding-top: 6px; }
.frow input, .frow textarea { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13.5px; font-family: inherit; resize: vertical; }
.fops { display: flex; gap: 8px; margin-top: 2px; }
.sc-link { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; margin-bottom: 6px; cursor: pointer; background: #fff; }
.sc-link:hover { border-color: var(--accent); }
.sc-line { display: flex; align-items: center; gap: 8px; }
.code { font-weight: 600; font-size: 12.5px; }
.title { flex: 1; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tag { font-size: 11px; border-radius: 4px; padding: 0 6px; flex-shrink: 0; }
.tag.main { background: var(--accent-soft); color: var(--accent); }
.tag.steps { background: #ecfdf5; color: #047857; }
.sc-steps { font-size: 11.5px; color: var(--text-dim); margin-top: 3px; }
</style>
