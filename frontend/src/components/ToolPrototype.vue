<script setup>
import { ref, computed, watch } from 'vue'
import { parseFlowSteps } from '../composables/stepLink.js'

const props = defineProps({ wsId: Number, code: String, roles: Array, focus: Object, reloadRev: Number })
const emit = defineEmits(['focus-step', 'gen-requirements'])

// ---------------- 规格加载 ----------------
const spec = ref(null)       // 界面规格对象（null = 尚未编写）
const loaded = ref(false)
const error = ref('')
const editing = ref(false)
const draft = ref('')
const draftError = ref('')

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function loadSpec() {
  loaded.value = false
  error.value = ''
  try {
    const r = await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.code)}/ui-spec`)
    spec.value = r.spec
    resetRun()
    initValues()
    applyFocus()   // spec 就绪后补消费一次 pending focus（组件挂载时 spec 未加载，immediate watch 会被跳过）
  } catch (e) { error.value = e.message }
  loaded.value = true
}
watch(() => [props.wsId, props.code], loadSpec, { immediate: true })
// AI 应用 ui_spec 变更后，App 递增 reloadRev 通知重拉（停留在当前屏，按新规格渲染）
watch(() => props.reloadRev, (v, old) => { if (v && v !== old) loadSpec() })

function openEditor() {
  draft.value = spec.value ? JSON.stringify(spec.value, null, 2) : JSON.stringify(DEFAULT_SPEC, null, 2)
  draftError.value = ''
  editing.value = true
}
async function saveSpec() {
  let obj
  try { obj = JSON.parse(draft.value) } catch (e) { draftError.value = 'JSON 解析失败：' + e.message; return }
  try {
    await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.code)}/ui-spec`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec: obj }),
    })
    spec.value = obj
    editing.value = false
    resetRun()
    initValues()
  } catch (e) { draftError.value = e.message }
}

// ---------------- 界面说明（spec.notes）与需求反写 ----------------
const showNotes = ref(false)
const savingNotes = ref(false)
const notesSaved = ref(false)

async function saveNotes() {
  savingNotes.value = true
  notesSaved.value = false
  try {
    await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.code)}/ui-spec`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec: spec.value }),
    })
    notesSaved.value = true
    setTimeout(() => { notesSaved.value = false }, 2000)
  } catch (e) { error.value = e.message }
  savingNotes.value = false
}

function genRequirements() {
  emit('gen-requirements', props.code)
}

// ---------------- 表单值与校验 ----------------
const values = ref({})
const errors = ref({})

function allFields() {
  return (spec.value?.screens || []).flatMap(s => s.fields || [])
}
function initValues() {
  const v = {}
  for (const f of allFields()) if (f.key) v[f.key] = f.default ?? (f.type === 'select' ? (f.options?.[0] ?? '') : '')
  values.value = v
  errors.value = {}
  screenIdx.value = 0
}

function resolveFields(tpl) {
  // 只解析字段值（供 computed 定义使用，避免自引用）
  return (tpl || '').replace(/\{(\w+)\}/g, (_, k) => values.value[k] ?? '…')
}
const computedValues = computed(() => {
  const out = {}
  for (const c of spec.value?.computed || []) out[c.key] = resolveFields(c.template)
  return out
})
function resolveTemplate(tpl) {
  return (tpl || '').replace(/\{(\w+)\}/g, (_, k) => values.value[k] ?? computedValues.value[k] ?? '…')
}
function fieldDisplay(f) {
  if (f.type === 'computed') return computedValues.value[f.computedKey] ?? '…'
  if (f.type === 'display') return f.template ? resolveTemplate(f.template) : (f.text ?? '')
  return values.value[f.key]
}

function validateScreen(idx) {
  const errs = {}
  for (const f of spec.value.screens[idx]?.fields || []) {
    if (!f.key || f.readonly || f.type === 'display' || f.type === 'computed') continue
    const v = (values.value[f.key] ?? '').toString()
    if (f.required && !v.trim()) errs[f.key] = '必填项'
    else if (v && f.validate?.regex && !(new RegExp(f.validate.regex)).test(v)) errs[f.key] = f.validate.message || '格式不正确'
  }
  errors.value = errs
  return Object.keys(errs).length === 0
}

// ---------------- 向导流程 ----------------
const screenIdx = ref(0)
const phase = ref('form')     // form | confirm | running | done
const screens = computed(() => spec.value?.screens || [])
const isLastScreen = computed(() => screenIdx.value >= screens.value.length - 1)

function next() {
  if (!validateScreen(screenIdx.value)) return
  if (isLastScreen.value) { phase.value = 'confirm'; return }
  screenIdx.value += 1
  errors.value = {}
}
function prev() {
  errors.value = {}
  if (phase.value === 'confirm') { phase.value = 'form'; return }
  if (screenIdx.value > 0) screenIdx.value -= 1
}

// ---------------- 模拟执行 ----------------
const runStages = ref([])     // [{text, done}]
const runTimer = ref(null)
const toast = ref('')

// ---------------- 跨视图联动 ----------------
// 当前屏解析出的流程步骤号
const currentFlowSteps = computed(() => parseFlowSteps(screens.value[screenIdx.value]?.flowSteps))
// 接收外部 focus：切到指定屏（immediate 兜底——组件因切视图重挂载时挂载当拍也消费一次）
function applyFocus() {
  if (props.focus?.mode !== 'tool') return
  const idx = props.focus.screenIdx
  if (idx == null || !spec.value) return
  resetRun()
  phase.value = 'form'
  screenIdx.value = Math.max(0, Math.min(idx, screens.value.length - 1))
  errors.value = {}
}
watch(() => props.focus?.seq, () => applyFocus(), { immediate: true })

function startRun() {
  phase.value = 'running'
  const stages = spec.value?.run?.stages || []
  runStages.value = stages.map(text => ({ text, done: false }))
  let i = 0
  runTimer.value = setInterval(() => {
    if (i < runStages.value.length) {
      runStages.value[i].done = true
      i += 1
    } else {
      clearInterval(runTimer.value)
      runTimer.value = null
      phase.value = 'done'
    }
  }, 650)
}
function resetRun() {
  if (runTimer.value) { clearInterval(runTimer.value); runTimer.value = null }
  phase.value = 'form'
  runStages.value = []
  toast.value = ''
}
function doAction(a) {
  if (a.reset) { resetRun(); return }
  if (a.toast) toast.value = resolveTemplate(a.toast)
}

// ---------------- 规格编辑辅助 ----------------
function confirmRows() {
  const c = spec.value?.confirm
  if (!c?.fields) return []
  const fieldByKey = Object.fromEntries(allFields().filter(f => f.key).map(f => [f.key, f]))
  const computedByKey = Object.fromEntries((spec.value.computed || []).map(x => [x.key, x]))
  return c.fields.map(k => {
    if (computedByKey[k]) return { label: computedByKey[k].label, value: computedValues.value[k] }
    const f = fieldByKey[k]
    return { label: f?.label || k, value: values.value[k] ?? '' }
  })
}

const DEFAULT_SPEC = {
  kind: 'wizard',
  title: '（在此编写界面规格）',
  desc: 'screens 为向导步骤；字段类型支持 select / text / display / computed；computed 用 {字段key} 占位实时拼接。',
  computed: [],
  screens: [
    { id: 's1', title: '步骤 1', flowSteps: '', fields: [{ key: 'demo', label: '示例字段', type: 'text', default: '', help: '' }] },
  ],
  confirm: { title: '确认', fields: [] },
  run: { button: '确认', stages: [], successTitle: '完成', successFields: [], actions: [{ label: '重新演示', reset: true }] },
}
</script>

<template>
  <div class="tool">
    <div v-if="!loaded" class="placeholder">加载中…</div>
    <div v-else-if="error" class="placeholder error">{{ error }}</div>

    <!-- 规格编辑器 -->
    <div v-else-if="editing" class="editor">
      <div class="editor-bar">
        <b>界面规格（JSON）— 保存后左侧界面立即按新规格渲染</b>
        <span class="spacer"></span>
        <button class="btn" @click="editing = false">取消</button>
        <button class="btn primary" @click="saveSpec">保存规格</button>
      </div>
      <div v-if="draftError" class="draft-error">{{ draftError }}</div>
      <textarea v-model="draft" class="spec-text" spellcheck="false"></textarea>
    </div>

    <!-- 无规格 -->
    <div v-else-if="!spec" class="placeholder">
      <p>该场景还没有界面规格。</p>
      <p class="dim">界面规格描述目标工具的界面结构（向导步骤 / 控件 / 校验 / 操作），由 Claude 起草、你来修改 —— 改规格，界面跟着变。</p>
      <button class="btn primary" @click="openEditor">编写界面规格</button>
    </div>

    <!-- 原型渲染 -->
    <div v-else class="prototype">
      <div class="proto-head">
        <div>
          <div class="proto-title">{{ spec.title }}</div>
          <div v-if="spec.desc" class="proto-desc">{{ spec.desc }}</div>
        </div>
        <span class="spacer"></span>
        <span class="proto-badge">工具原型</span>
        <button class="btn" @click="showNotes = !showNotes">📝 界面说明</button>
        <button class="btn" title="把当前原型规格+界面说明发给 AI，反写为场景需求文本（走确认流程后落库）" @click="genRequirements">⤓ 生成需求文本</button>
        <button class="btn" @click="openEditor">✎ 编辑规格</button>
      </div>

      <!-- 界面说明（随 spec 持久化在 spec.notes） -->
      <div v-if="showNotes" class="notes-panel">
        <div class="notes-head">
          <b>界面说明</b><span class="dim">点原型时随手记：字段含义 / 交互意图 / 异常约定 —— 会随规格一起保存，并作为「生成需求文本」的依据</span>
        </div>
        <textarea v-model="spec.notes" rows="4" placeholder="例：P8 项目代码必须 3 位大写字母；仓库名实时拼接展示；网络异常时停留在当前屏并提示重试…"></textarea>
        <div class="notes-ops">
          <button class="btn primary sm" :disabled="savingNotes" @click="saveNotes">{{ savingNotes ? '保存中…' : '保存说明' }}</button>
          <span v-if="notesSaved" class="saved-tip">✔ 已保存</span>
        </div>
      </div>

      <div class="proto-body">
        <!-- 主区：模拟工具窗口 -->
        <div class="window">
          <div class="win-titlebar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span><span class="win-title">{{ spec.title }}</span></div>

          <!-- 向导步骤条 -->
          <div v-if="phase === 'form' || phase === 'confirm'" class="stepsbar">
            <template v-for="(s, i) in screens" :key="s.id">
              <div class="stepdot" :class="{ active: phase === 'form' && i === screenIdx, done: phase === 'confirm' || i < screenIdx }">
                <span class="n">{{ i + 1 }}</span>{{ s.title }}
              </div>
              <span v-if="i < screens.length - 1" class="step-sep">›</span>
            </template>
            <span class="step-sep">›</span>
            <div class="stepdot" :class="{ active: phase === 'confirm', done: false }"><span class="n">{{ screens.length + 1 }}</span>{{ spec.confirm?.title || '确认' }}</div>
          </div>

          <!-- 表单屏 -->
          <div v-if="phase === 'form'" class="win-content">
            <div class="screen-title-row">
              <h3>{{ screens[screenIdx].title }}</h3>
              <span v-if="currentFlowSteps.length" class="flowlink">
                对应流程步骤
                <button
                  v-for="no in currentFlowSteps" :key="no"
                  class="step-chip" :title="`跳到流程原型并展开步骤 ${no}`"
                  @click="emit('focus-step', no)"
                >{{ no }}</button>
              </span>
            </div>
            <p v-if="screens[screenIdx].desc" class="screen-desc">{{ screens[screenIdx].desc }}</p>
            <div class="form">
              <div v-for="f in screens[screenIdx].fields" :key="f.key || f.label" class="form-row" :class="{ invalid: f.key && errors[f.key] }">
                <label>{{ f.label }}<span v-if="f.required" class="req">*</span></label>
                <div class="control">
                  <select v-if="f.type === 'select'" v-model="values[f.key]" :disabled="f.readonly">
                    <option v-for="o in f.options" :key="o" :value="o">{{ o }}</option>
                  </select>
                  <select v-else-if="f.type === 'role-picker'" v-model="values[f.key]" :disabled="f.readonly">
                    <option value="" disabled>请选择角色</option>
                    <option v-for="r in roles || []" :key="r.id" :value="r.name">{{ r.name }}</option>
                  </select>
                  <input v-else-if="f.type === 'text'" v-model="values[f.key]" :placeholder="f.placeholder || ''" :readonly="f.readonly" />
                  <div v-else-if="f.type === 'display' || f.type === 'computed'" class="display-value">{{ fieldDisplay(f) }}</div>
                  <div v-if="f.key && errors[f.key]" class="field-error">{{ errors[f.key] }}</div>
                  <div v-else-if="f.help" class="help">{{ f.help }}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- 确认屏 -->
          <div v-else-if="phase === 'confirm'" class="win-content">
            <h3>{{ spec.confirm?.title || '确认' }}</h3>
            <p v-if="spec.confirm?.note" class="screen-desc">{{ spec.confirm.note }}</p>
            <table class="summary">
              <tr v-for="(r, i) in confirmRows()" :key="i"><th>{{ r.label }}</th><td>{{ r.value }}</td></tr>
            </table>
          </div>

          <!-- 执行中 -->
          <div v-else-if="phase === 'running'" class="win-content">
            <h3>正在执行…</h3>
            <ul class="stages">
              <li v-for="(st, i) in runStages" :key="i" :class="{ done: st.done, active: !st.done && (i === 0 || runStages[i-1].done) }">
                <span class="st-icon">{{ st.done ? '✅' : (i === 0 || runStages[i-1].done ? '⏳' : '·') }}</span>{{ st.text }}
              </li>
            </ul>
          </div>

          <!-- 完成 -->
          <div v-else class="win-content">
            <h3 class="ok">✅ {{ spec.run?.successTitle || '完成' }}</h3>
            <table class="summary">
              <tr v-for="(sf, i) in spec.run?.successFields || []" :key="i"><th>{{ sf.label }}</th><td>{{ resolveTemplate(sf.template) }}</td></tr>
            </table>
            <div v-if="toast" class="toast">{{ toast }}</div>
          </div>

          <!-- 按钮条 -->
          <div class="win-actions">
            <template v-if="phase === 'form'">
              <button class="btn" :disabled="screenIdx === 0" @click="prev">‹ 上一步</button>
              <span class="spacer"></span>
              <button class="btn primary" @click="next">{{ isLastScreen ? '下一步：确认 ›' : '下一步 ›' }}</button>
            </template>
            <template v-else-if="phase === 'confirm'">
              <button class="btn" @click="prev">‹ 返回修改</button>
              <span class="spacer"></span>
              <button class="btn primary" @click="startRun">{{ spec.run?.button || '确认' }}</button>
            </template>
            <template v-else-if="phase === 'done'">
              <span class="spacer"></span>
              <button v-for="(a, i) in spec.run?.actions || []" :key="i" class="btn" :class="{ primary: a.primary }" @click="doAction(a)">{{ a.label }}</button>
            </template>
          </div>
        </div>

        <!-- 侧栏：实时预览 -->
        <aside v-if="(spec.computed || []).some(c => c.preview)" class="preview">
          <div class="pv-title">实时预览</div>
          <div v-for="c in spec.computed.filter(c => c.preview)" :key="c.key" class="pv-item">
            <div class="pv-label">{{ c.label }}</div>
            <div class="pv-value">{{ computedValues[c.key] }}</div>
          </div>
          <div v-if="screens[screenIdx]?.flowSteps && phase === 'form'" class="pv-flow">
            对应主流程：{{ screens[screenIdx].flowSteps }}
            <div class="pv-flow-chips">
              <button
                v-for="no in currentFlowSteps" :key="no"
                class="step-chip" :title="`跳到流程原型并展开步骤 ${no}`"
                @click="emit('focus-step', no)"
              >步骤 {{ no }}</button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool { max-width: 1200px; }
.placeholder { color: var(--text-dim); padding: 30px; text-align: center; }
.placeholder .dim { font-size: 13px; }
.error { color: #b91c1c; }
.spacer { flex: 1; }

/* 编辑器 */
.editor { display: flex; flex-direction: column; gap: 8px; height: calc(100vh - 140px); }
.editor-bar { display: flex; align-items: center; gap: 8px; }
.draft-error { color: #b91c1c; font-size: 13px; }
.spec-text { flex: 1; font-family: Consolas, monospace; font-size: 13px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; resize: none; background: var(--panel); }

/* 原型外框 */
.proto-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.notes-panel { border: 1px dashed var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; background: var(--bg); }
.notes-head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 6px; }
.notes-head .dim { font-size: 12px; color: var(--text-dim); }
.notes-panel textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; font-family: inherit; resize: vertical; box-sizing: border-box; }
.notes-ops { display: flex; gap: 10px; align-items: center; margin-top: 6px; }
.saved-tip { color: #047857; font-size: 12px; }
.proto-title { font-size: 16px; font-weight: 700; }
.proto-desc { color: var(--text-dim); font-size: 13px; }
.proto-badge { font-size: 11px; background: #ede9fe; color: #7c3aed; border-radius: 4px; padding: 2px 8px; font-weight: 600; }
.proto-body { display: flex; gap: 14px; align-items: flex-start; }

/* 模拟工具窗口 */
.window { flex: 1; min-width: 0; border: 1px solid var(--border); border-radius: 10px; background: #fff; overflow: hidden; box-shadow: 0 4px 18px rgba(0,0,0,.06); }
.win-titlebar { display: flex; align-items: center; gap: 6px; padding: 8px 12px; background: var(--panel); border-bottom: 1px solid var(--border); }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.dot.r { background: #f87171; } .dot.y { background: #fbbf24; } .dot.g { background: #34d399; }
.win-title { margin-left: 8px; font-size: 12.5px; color: var(--text-dim); }

.stepsbar { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 10px 16px; border-bottom: 1px solid var(--border); background: #fafafa; }
.stepdot { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text-dim); }
.stepdot .n { display: inline-flex; width: 20px; height: 20px; border-radius: 50%; border: 1.5px solid var(--border); align-items: center; justify-content: center; font-size: 11px; }
.stepdot.active { color: var(--accent); font-weight: 600; }
.stepdot.active .n { background: var(--accent); border-color: var(--accent); color: #fff; }
.stepdot.done { color: #059669; }
.stepdot.done .n { background: #059669; border-color: #059669; color: #fff; }
.step-sep { color: var(--border); }

.win-content { padding: 18px 22px; min-height: 260px; }
.win-content h3 { margin: 0 0 6px; font-size: 16px; }
.win-content h3.ok { color: #047857; }
.screen-desc { color: var(--text-dim); font-size: 13px; margin: 0 0 12px; }

.form { display: flex; flex-direction: column; gap: 12px; max-width: 560px; }
.form-row { display: grid; grid-template-columns: 170px 1fr; gap: 10px; align-items: start; }
.form-row label { font-size: 13.5px; padding-top: 6px; }
.req { color: #dc2626; margin-left: 2px; }
.control select, .control input { width: 100%; padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13.5px; background: #fff; }
.form-row.invalid select, .form-row.invalid input { border-color: #dc2626; background: #fef2f2; }
.control input[readonly] { background: #f3f4f6; color: var(--text-dim); }
.display-value { padding: 6px 10px; background: var(--accent-soft); border-radius: 6px; font-family: Consolas, monospace; font-size: 13.5px; color: var(--accent); font-weight: 600; }
.help { font-size: 12px; color: var(--text-dim); margin-top: 3px; }
.field-error { font-size: 12px; color: #dc2626; margin-top: 3px; }

.summary { border-collapse: collapse; min-width: 420px; }
.summary th, .summary td { border: 1px solid var(--border); padding: 7px 12px; font-size: 13.5px; text-align: left; }
.summary th { background: var(--panel); width: 180px; }

.stages { list-style: none; padding: 0; }
.stages li { padding: 7px 0; font-size: 13.5px; color: var(--text-dim); }
.stages li.done { color: #047857; }
.stages li.active { color: var(--text); }
.st-icon { display: inline-block; width: 26px; }
.toast { margin-top: 12px; padding: 8px 12px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; font-size: 13px; }

.win-actions { display: flex; gap: 8px; padding: 12px 22px; border-top: 1px solid var(--border); background: #fafafa; }
.btn { padding: 6px 14px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn:disabled { opacity: .4; }

/* 预览侧栏 */
.preview { width: 260px; flex-shrink: 0; border: 1px solid var(--border); border-radius: 10px; background: var(--panel); padding: 12px; }
.pv-title { font-size: 12px; color: var(--text-dim); margin-bottom: 8px; font-weight: 600; }
.pv-item { margin-bottom: 10px; }
.pv-label { font-size: 12px; color: var(--text-dim); }
.pv-value { font-family: Consolas, monospace; font-size: 14px; font-weight: 700; color: var(--accent); word-break: break-all; }
.pv-flow { margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--border); font-size: 12px; color: var(--text-dim); }
.pv-flow-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.screen-title-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flowlink { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-dim); }
.step-chip { border: 1px solid var(--accent); background: var(--accent-soft); color: var(--accent); border-radius: 999px; padding: 1px 10px; font-size: 12px; cursor: pointer; }
.step-chip:hover { background: var(--accent); color: #fff; }
</style>
