<script setup>
import { ref, reactive, computed } from 'vue'
import { useSysLabel } from '../composables/sysLabel.js'

const props = defineProps({
  wsId: Number,
  scenario: Object,   // null = 新建
  roles: Array,
})
const emit = defineEmits(['close', 'saved', 'deleted'])

const { sysLabel } = useSysLabel()

const isCreate = computed(() => !props.scenario)
const saving = ref(false)
const error = ref('')

// ---------------- 表单状态（深拷贝场景数据） ----------------
const src = props.scenario
const d = src ? JSON.parse(JSON.stringify(src.data)) : {}
const form = reactive({
  code: src?.code ?? '',
  title: src?.title ?? '',
  domain: src?.domain ?? d.domain ?? '',
  value: d.value ?? '',
  main_users: [...(d.main_users ?? [])],
  not_covered: [...(d.not_covered ?? [])],
  triggers: [...(d.triggers ?? [])],
  preconditions: [...(d.preconditions ?? [])],
  steps: (d.steps ?? []).map(s => ({ no: s.no ?? '', actor: s.actor ?? '系统', action: s.action ?? '', response: s.response ?? '', raw_actor: s.raw_actor ?? '' })),
  variants: (d.variants ?? []).map(v => ({ name: v.name ?? '', steps: (v.steps ?? []).map(s => ({ no: s.no ?? '', actor: s.actor ?? '系统', action: s.action ?? '', response: s.response ?? '', raw_actor: s.raw_actor ?? '' })) })),
  acceptance: [...(d.acceptance ?? [])],
  exceptions: (d.exceptions ?? []).map(e => ({ condition: e.condition ?? '', handling: e.handling ?? '' })),
  ui_material_text: (d.ui_material ?? []).join('\n'),
  notes: d.notes ?? '',
  pending: (src?.pending ?? []).map(p => ({ kind: p.kind ?? '待确认', text: p.text ?? '' })),
})

function toggle(list, name) {
  const i = list.indexOf(name)
  if (i >= 0) list.splice(i, 1); else list.push(name)
}
function addRow(list, item) { list.push(item) }
function delRow(list, i) { list.splice(i, 1) }
function move(list, i, dir) {
  const j = i + dir
  if (j < 0 || j >= list.length) return
  const [x] = list.splice(i, 1)
  list.splice(j, 0, x)
}
const newStep = () => ({ no: '', actor: '系统', action: '', response: '' })

// ---------------- 保存 / 删除 ----------------
async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

function buildPayload() {
  const uiMaterial = form.ui_material_text.split('\n').map(s => s.replace(/\s+$/, '')).filter((s, i, a) => s.trim() || (i > 0 && i < a.length - 1 && a[i - 1].trim() && a[i + 1].trim()))
  return {
    code: form.code.trim(),
    title: form.title.trim(),
    domain: form.domain.trim(),
    data: {
      code: props.scenario?.code ?? form.code.trim(),
      title: form.title.trim(),
      domain: form.domain.trim(),
      value: form.value,
      main_users: form.main_users,
      not_covered: form.not_covered,
      triggers: form.triggers.filter(t => t.trim()),
      preconditions: form.preconditions.filter(t => t.trim()),
      steps: form.steps.map(s => ({ no: s.no, actor: s.actor, action: s.action, response: s.response, raw_actor: s.raw_actor ?? '' })),
      variants: form.variants.filter(v => v.name.trim() || v.steps.length).map(v => ({ name: v.name, steps: v.steps.map(s => ({ no: s.no, actor: s.actor, action: s.action, response: s.response, raw_actor: s.raw_actor ?? '' })) })),
      acceptance: form.acceptance.filter(t => t.trim()),
      exceptions: form.exceptions.filter(e => e.condition.trim() || e.handling.trim()),
      ui_material: uiMaterial,
      notes: form.notes,
    },
    pending: form.pending.filter(p => p.text.trim()),
  }
}

async function save() {
  const payload = buildPayload()
  if (!payload.title) { error.value = '场景标题不能为空'; return }
  if (isCreate.value && !payload.code) { error.value = '场景编号不能为空（如 1.7）'; return }
  saving.value = true
  error.value = ''
  try {
    if (isCreate.value) {
      await api(`/api/workspaces/${props.wsId}/scenarios`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    } else {
      await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    }
    emit('saved', payload.code)
  } catch (e) { error.value = e.message }
  saving.value = false
}

async function remove() {
  const note = props.scenario?.origin !== 'manual'
    ? '\n注意：该场景来自文档导入，下次「↻ 重新导入」会把它带回来；如需彻底移除请同时从源文档删除。'
    : ''
  if (!confirm(`确定删除场景 ${props.scenario.code}「${props.scenario.title}」？${note}`)) return
  error.value = ''
  try {
    await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}`, { method: 'DELETE' })
    emit('deleted')
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <div class="editor">
    <div class="bar">
      <b>{{ isCreate ? '新建场景' : `编辑场景 ${scenario.code}` }}</b>
      <span v-if="!isCreate" class="tag" :class="scenario.origin === 'manual' ? 'manual' : 'imported'">
        {{ scenario.origin === 'manual' ? '手工创建' : (scenario.edited ? '已编辑（重导不覆盖）' : '导入（保存后重导不再覆盖）') }}
      </span>
      <span class="spacer"></span>
      <button v-if="!isCreate" class="btn danger" @click="remove">删除场景</button>
      <button class="btn" @click="emit('close')">取消</button>
      <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存场景' }}</button>
    </div>
    <div v-if="error" class="error">{{ error }}</div>

    <div class="grid2">
      <div class="row"><label>编号 *</label><input v-model="form.code" :disabled="!isCreate" placeholder="如 1.7（保存后不可改）" /></div>
      <div class="row"><label>标题 *</label><input v-model="form.title" placeholder="场景标题" /></div>
      <div class="row"><label>分组</label><input v-model="form.domain" placeholder="如 2.1 项目与仓库初始化" /></div>
      <div class="row"><label>业务价值</label><input v-model="form.value" placeholder="一句话说明这个场景解决什么问题" /></div>
    </div>

    <section>
      <h3>主要用户</h3>
      <div class="chips">
        <span v-for="r in roles" :key="r.id" class="chip" :class="{ on: form.main_users.includes(r.name) }" @click="toggle(form.main_users, r.name)">{{ r.name }}</span>
        <span v-if="!roles?.length" class="dim">先到「👥 角色定义」添加角色</span>
      </div>
    </section>
    <section>
      <h3>不覆盖</h3>
      <div class="chips">
        <span v-for="r in roles" :key="r.id" class="chip" :class="{ on: form.not_covered.includes(r.name) }" @click="toggle(form.not_covered, r.name)">{{ r.name }}</span>
      </div>
    </section>

    <section>
      <h3>触发条件 <button class="add" @click="addRow(form.triggers, '')">＋</button></h3>
      <div v-for="(t, i) in form.triggers" :key="i" class="line-row">
        <input v-model="form.triggers[i]" placeholder="触发条件" />
        <button class="mini" @click="delRow(form.triggers, i)">✕</button>
      </div>
    </section>

    <section>
      <h3>前置条件 <button class="add" @click="addRow(form.preconditions, '')">＋</button></h3>
      <div v-for="(t, i) in form.preconditions" :key="i" class="line-row">
        <input v-model="form.preconditions[i]" placeholder="前置条件" />
        <button class="mini" @click="delRow(form.preconditions, i)">✕</button>
      </div>
    </section>

    <section>
      <h3>主流程 <button class="add" @click="addRow(form.steps, newStep())">＋ 添加步骤</button></h3>
      <table class="steps">
        <thead><tr><th class="c-no">步骤</th><th class="c-actor">角色</th><th>用户操作</th><th>系统响应</th><th class="c-ops"></th></tr></thead>
        <tbody>
          <tr v-for="(s, i) in form.steps" :key="i" :class="{ system: s.actor === '系统' }">
            <td><input v-model="s.no" class="in-no" /></td>
            <td>
              <select v-model="s.actor">
                <option value="系统">{{ sysLabel }}</option>
                <option v-for="r in roles" :key="r.id" :value="r.name">{{ r.name }}</option>
              </select>
            </td>
            <td><input v-model="s.action" :placeholder="s.actor === '系统' ? `（${sysLabel}步骤可留空）` : ''" /></td>
            <td><input v-model="s.response" /></td>
            <td class="ops">
              <button class="mini" title="上移" @click="move(form.steps, i, -1)">↑</button>
              <button class="mini" title="下移" @click="move(form.steps, i, 1)">↓</button>
              <button class="mini" title="删除" @click="delRow(form.steps, i)">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section>
      <h3>分支 / 变体 <button class="add" @click="addRow(form.variants, { name: '', steps: [] })">＋ 添加变体</button></h3>
      <div v-for="(v, vi) in form.variants" :key="vi" class="variant">
        <div class="v-head">
          <input v-model="v.name" class="v-name" placeholder="变体名称，如：分支 A：复用上一版平台" />
          <button class="mini" title="上移" @click="move(form.variants, vi, -1)">↑</button>
          <button class="mini" title="下移" @click="move(form.variants, vi, 1)">↓</button>
          <button class="mini" title="删除变体" @click="delRow(form.variants, vi)">✕</button>
        </div>
        <table class="steps">
          <thead><tr><th class="c-no">步骤</th><th class="c-actor">角色</th><th>用户操作</th><th>系统响应</th><th class="c-ops"></th></tr></thead>
          <tbody>
            <tr v-for="(s, i) in v.steps" :key="i" :class="{ system: s.actor === '系统' }">
              <td><input v-model="s.no" class="in-no" /></td>
              <td>
                <select v-model="s.actor">
                  <option value="系统">{{ sysLabel }}</option>
                  <option v-for="r in roles" :key="r.id" :value="r.name">{{ r.name }}</option>
                </select>
              </td>
              <td><input v-model="s.action" /></td>
              <td><input v-model="s.response" /></td>
              <td class="ops">
                <button class="mini" @click="move(v.steps, i, -1)">↑</button>
                <button class="mini" @click="move(v.steps, i, 1)">↓</button>
                <button class="mini" @click="delRow(v.steps, i)">✕</button>
              </td>
            </tr>
          </tbody>
        </table>
        <button class="add small" @click="addRow(v.steps, newStep())">＋ 添加步骤</button>
      </div>
    </section>

    <section>
      <h3>验收标准 <button class="add" @click="addRow(form.acceptance, '')">＋</button></h3>
      <div v-for="(t, i) in form.acceptance" :key="i" class="line-row">
        <input v-model="form.acceptance[i]" placeholder="验收标准" />
        <button class="mini" @click="delRow(form.acceptance, i)">✕</button>
      </div>
    </section>

    <section>
      <h3>异常分支 <button class="add" @click="addRow(form.exceptions, { condition: '', handling: '' })">＋</button></h3>
      <table class="steps">
        <thead><tr><th>异常情况</th><th>系统行为</th><th class="c-ops"></th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in form.exceptions" :key="i">
            <td><input v-model="e.condition" /></td>
            <td><input v-model="e.handling" /></td>
            <td class="ops"><button class="mini" @click="delRow(form.exceptions, i)">✕</button></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section>
      <h3>关键界面 / 交互（界面素材，每行一条，缩进表层级）</h3>
      <textarea v-model="form.ui_material_text" class="ui-text" rows="5" spellcheck="false" placeholder="- **界面名称**：&#10;  - 界面元素说明"></textarea>
    </section>

    <section>
      <h3>待确认 / 待办 <button class="add" @click="addRow(form.pending, { kind: '待确认', text: '' })">＋</button></h3>
      <div v-for="(p, i) in form.pending" :key="i" class="line-row">
        <select v-model="p.kind" class="kind">
          <option>待确认</option><option>待办</option><option>已确认</option>
        </select>
        <input v-model="p.text" placeholder="事项内容" />
        <button class="mini" @click="delRow(form.pending, i)">✕</button>
      </div>
    </section>

    <section v-if="form.notes">
      <h3>其余原文（流程图 / 说明，只读）</h3>
      <pre class="notes">{{ form.notes }}</pre>
    </section>
  </div>
</template>

<style scoped>
.editor { max-width: 1150px; }
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; position: sticky; top: -16px; background: var(--bg); padding: 10px 0; z-index: 10; }
.spacer { flex: 1; }
.tag { font-size: 11px; border-radius: 4px; padding: 2px 8px; }
.tag.manual { background: #ecfdf5; color: #047857; }
.tag.imported { background: var(--accent-soft); color: var(--accent); }
.error { color: #b91c1c; font-size: 13px; margin-bottom: 10px; }
.btn { padding: 6px 14px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.danger { color: #dc2626; border-color: #fecaca; }
.btn:disabled { opacity: .5; }

.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px; margin-bottom: 6px; }
.row { display: grid; grid-template-columns: 80px 1fr; gap: 10px; align-items: center; }
.row label { font-size: 13px; color: var(--text-dim); }
.row input { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13.5px; }

section { margin-top: 16px; }
h3 { font-size: 14px; margin: 0 0 8px; display: flex; align-items: center; gap: 8px; }
.add { border: 1px dashed var(--accent); background: transparent; color: var(--accent); border-radius: 6px; padding: 2px 10px; font-size: 12px; cursor: pointer; }
.add.small { margin-top: 6px; }

.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 3px 12px; border: 1px solid var(--border); border-radius: 999px; font-size: 13px; cursor: pointer; background: var(--panel); }
.chip.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.dim { color: var(--text-dim); font-size: 12.5px; }

.line-row { display: flex; gap: 6px; margin-bottom: 6px; }
.line-row input { flex: 1; padding: 5px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13.5px; }
.line-row select.kind { width: 90px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel); }

table.steps { width: 100%; border-collapse: collapse; }
.steps th, .steps td { border: 1px solid var(--border); padding: 4px 6px; }
.steps th { background: var(--panel); font-size: 12.5px; text-align: left; }
.c-no { width: 60px; } .c-actor { width: 190px; } .c-ops { width: 86px; }
.steps input { width: 100%; padding: 4px 8px; border: 1px solid transparent; border-radius: 4px; font-size: 13px; box-sizing: border-box; background: transparent; }
.steps input:focus, .steps select:focus { border-color: var(--accent); background: #fff; outline: none; }
.steps input.in-no { text-align: center; }
.steps select { width: 100%; padding: 4px 6px; border: 1px solid transparent; border-radius: 4px; font-size: 13px; background: transparent; }
tr.system td { background: var(--system-soft); }
.ops { white-space: nowrap; text-align: center; }
.mini { border: 1px solid var(--border); background: var(--panel); border-radius: 4px; font-size: 11px; padding: 2px 6px; cursor: pointer; }

.variant { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 10px; background: #fafafa; }
.v-head { display: flex; gap: 6px; margin-bottom: 8px; align-items: center; }
.v-name { flex: 1; padding: 5px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13.5px; font-weight: 600; }

.ui-text { width: 100%; font-family: Consolas, monospace; font-size: 13px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; box-sizing: border-box; }
.notes { white-space: pre-wrap; background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; font-size: 12.5px; max-height: 260px; overflow: auto; }
</style>
