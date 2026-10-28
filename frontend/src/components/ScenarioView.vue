<script setup>
import { ref, watch, nextTick, computed } from 'vue'
import MermaidBlock from './MermaidBlock.vue'
import { useSysLabel } from '../composables/sysLabel.js'

const props = defineProps({ scenario: Object, focus: Object, activeRole: Object, wsId: Number })
const emit = defineEmits(['focus-step', 'send-to-ai', 'switch-role'])

const { isSys, displayActor } = useSysLabel()
function isSystem(step) {
  return isSys(step.actor)
}

// notes 拆成「流程图」和「文字备注」两块展示——流程图是流程图，备注是备注
// 围栏兼容 ```mermaid 和 ~~~mermaid；mermaid 块上方的「**流程图**（Mermaid）：」式引导标签行不算正文
const notesText = computed(() => props.scenario?.data?.notes || '')
const MERMAID_RE = /(?:```|~~~)mermaid\s*\n([\s\S]*?)(?:```|~~~)/
const LABEL_LINE_RE = /(?:^|\n)[ \t]*(?:[*_＊#>-]*[ \t]*)*流程图(?:[*_＊]*[ \t]*)*[（(][^）)]*[）)][ \t]*[:：]?[ \t]*(?=\n?$)/
const stripLabels = (t) => t.replace(LABEL_LINE_RE, '')
const hasMermaid = computed(() => MERMAID_RE.test(notesText.value))
const hasPlainText = computed(() => !!stripLabels(notesText.value.replace(new RegExp(MERMAID_RE.source, 'g'), '')).trim())

// ---------------- 角色视角 ----------------
// 步骤表所有角色共享；步骤上的 roles 数组限定可见角色；role_views 是各角色完整的界面/验收/说明描述
const roleName = computed(() => props.activeRole?.name || '')
const roleView = computed(() => (roleName.value && props.scenario?.data?.role_views?.[roleName.value]) || null)

function stepVisible(s) {
  if (!roleName.value) return true          // 未选角色：全部显示
  return !s.roles?.length || s.roles.includes(roleName.value)
}
const visibleSteps = computed(() => (props.scenario?.data?.steps || []).filter(stepVisible))
function visibleVariantSteps(v) { return (v.steps || []).filter(stepVisible) }

// 界面素材 / 验收：选中角色且有独立视角时换成该角色的版本，否则用共享版
const uiMaterial = computed(() => roleView.value?.ui_material?.length ? roleView.value.ui_material : (props.scenario?.data?.ui_material || []))
const acceptance = computed(() => roleView.value?.acceptance?.length ? roleView.value.acceptance : (props.scenario?.data?.acceptance || []))

// 场景内可切换的角色：main_users + role_views 里出现过的角色（去重，保持顺序）
const switchableRoles = computed(() => {
  const d = props.scenario?.data || {}
  const names = [...(d.main_users || []), ...Object.keys(d.role_views || {})]
  return [...new Set(names)]
})
function clickRole(name) {
  emit('switch-role', roleName.value === name ? '' : name)  // 再点一次=取消视角
}

// ---------------- 变更历史 ----------------
const changes = ref([])
const ORIGIN_LABEL = { ai_apply: 'AI 应用', manual_edit: '手工编辑', develop_mark: '开发标记', ai_prototype: 'AI 原型' }
async function loadChanges() {
  changes.value = []
  if (!props.wsId || !props.scenario?.code) return
  try {
    const res = await fetch(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}/changes`)
    if (res.ok) changes.value = (await res.json()).changes || []
  } catch { /* 静默 */ }
}
watch(() => [props.wsId, props.scenario?.code], loadChanges, { immediate: true })

// 版本总结：场景确认到某版本时后台 AI 生成；未生成完会显示"生成中"并稍后重试拉一次
const verSummary = ref('')
const verSummaryPending = ref(false)
let vsTimer = null
async function loadVersionSummary() {
  clearTimeout(vsTimer)
  verSummary.value = ''
  verSummaryPending.value = false
  const v = props.scenario?.version
  if (!props.wsId || !v) return
  try {
    const res = await fetch(`/api/workspaces/${props.wsId}/version-summary?version=${encodeURIComponent(v)}`)
    if (!res.ok) return
    const s = ((await res.json()).summary || '').trim()
    if (s) verSummary.value = s
    else {
      // 还没生成完：5 秒后再拉一次
      verSummaryPending.value = true
      vsTimer = setTimeout(loadVersionSummary, 5000)
    }
  } catch { /* 静默 */ }
}
watch(() => [props.wsId, props.scenario?.code, props.scenario?.version], loadVersionSummary, { immediate: true })
function fmtTime(ts) {
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function originLabel(o) { return ORIGIN_LABEL[o] || o || '变更' }
function detailFields(detail) {
  try { return JSON.parse(detail).fields || [] } catch { return [] }
}

// ---------------- 实测记录（真实环境试用时记不一致点，待反馈给开发 agent） ----------------
const findings = ref([])
const findingDraft = ref('')
const findingSaving = ref(false)
async function loadFindings() {
  findings.value = []
  if (!props.wsId || !props.scenario?.code) return
  try {
    const res = await fetch(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}/findings`)
    if (res.ok) findings.value = (await res.json()).findings || []
  } catch { /* 静默 */ }
}
watch(() => [props.wsId, props.scenario?.code], loadFindings, { immediate: true })

async function addFinding() {
  const t = findingDraft.value.trim()
  if (!t || findingSaving.value) return
  findingSaving.value = true
  try {
    const res = await fetch(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}/findings`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
    findingDraft.value = ''
    await loadFindings()
  } catch { /* 静默 */ }
  findingSaving.value = false
}
async function delFinding(f) {
  try {
    await fetch(`/api/workspaces/${props.wsId}/findings/${f.id}`, { method: 'DELETE' })
    await loadFindings()
  } catch { /* 静默 */ }
}
const openFindingCount = computed(() => findings.value.filter(f => f.status === 'open').length)

// 跨视图联动：接收 focus 时滚动定位并短暂高亮该步骤行
const flashNo = ref(null)
async function applyFocus() {
  if (props.focus?.mode !== 'browse') return
  const no = props.focus.step
  if (no == null) return
  flashNo.value = no
  await nextTick()
  document.querySelector(`tr.step-row[data-no="${no}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  setTimeout(() => { if (flashNo.value === no) flashNo.value = null }, 2200)
}
// seq 变化时消费；immediate 兜底——组件因切视图重挂载时，挂载当拍也消费一次 pending focus
watch(() => props.focus?.seq, () => applyFocus(), { immediate: true })

function indentOf(line) {
  const m = line.match(/^(\s*)/)
  return Math.min(3, Math.floor((m ? m[1].length : 0) / 2)) * 14
}

// ---------------- 步骤行操作菜单 ----------------
const actionMenu = ref({ show: false, x: 0, y: 0, step: null, rowText: '' })
const annotateDialog = ref({ show: false, stepNo: '', text: '' })
const annotations = ref([])  // 批注列表 { stepNo, text, time }

function onRowClick(e, s) {
  console.log('[ScenarioView] onRowClick triggered', { button: e.button, step: s.no })
  // 只响应左键
  if (e.button !== 0) {
    console.log('[ScenarioView] not left button, skip')
    return
  }

  // 清除之前的文字选择，避免干扰
  const sel = window.getSelection()
  if (sel && sel.rangeCount > 0 && !sel.getRangeAt(0).collapsed) {
    console.log('[ScenarioView] has selection from drag, skip and clear')
    sel.removeAllRanges()
    return
  }

  const row = e.currentTarget
  const cells = row.querySelectorAll('td')
  const rowText = Array.from(cells).map(c => c.textContent.trim()).join(' | ')
  console.log('[ScenarioView] show menu at', e.clientX, e.clientY)

  // 先关闭已有菜单，再打开新的
  closeActionMenu()

  // 使用 setTimeout 确保 document click 事件先处理完
  setTimeout(() => {
    actionMenu.value = {
      show: true,
      x: e.clientX,
      y: e.clientY,
      step: s,
      rowText,
    }
  }, 0)
}

function closeActionMenu() {
  actionMenu.value.show = false
}

function onAnnotate() {
  annotateDialog.value = {
    show: true,
    stepNo: actionMenu.value.step.no,
    text: '',
  }
  closeActionMenu()
}

function onSendToAi() {
  emit('send-to-ai', actionMenu.value.rowText)
  closeActionMenu()
}

function saveAnnotation() {
  if (!annotateDialog.value.text.trim()) return
  annotations.value.push({
    stepNo: annotateDialog.value.stepNo,
    text: annotateDialog.value.text.trim(),
    time: new Date().toLocaleString('zh-CN'),
  })
  annotateDialog.value.show = false
}

// 点击其他地方关闭菜单
function onDocumentClick(e) {
  if (!e.target.closest('.action-menu') && !e.target.closest('.annotate-dialog')) {
    closeActionMenu()
  }
}
watch(() => actionMenu.value.show, (v) => {
  if (v) document.addEventListener('click', onDocumentClick)
  else document.removeEventListener('click', onDocumentClick)
})
</script>

<template>
  <div class="detail">
    <blockquote v-if="scenario.data.value" class="value">{{ scenario.data.value }}</blockquote>

    <div class="meta-row">
      <span v-if="switchableRoles.length" class="role-switch">
        <b>视角：</b>
        <span
          v-for="n in switchableRoles" :key="n"
          class="role-opt" :class="{ active: roleName === n }"
          :title="roleName === n ? '再点一次取消视角，恢复共享全貌' : `以「${n}」的视角查看本场景`"
          @click="clickRole(n)"
        >{{ n }}</span>
      </span>
      <span v-if="scenario.data.not_covered?.length"><b>不覆盖：</b>{{ scenario.data.not_covered.join('、') }}</span>
      <span v-if="scenario.data.domain"><b>分组：</b>{{ scenario.data.domain }}</span>
    </div>

    <section v-if="scenario.data.triggers?.length">
      <h3>触发条件</h3>
      <ol><li v-for="(t, i) in scenario.data.triggers" :key="i">{{ t }}</li></ol>
    </section>

    <section v-if="scenario.data.preconditions?.length">
      <h3>前置条件</h3>
      <ul><li v-for="(t, i) in scenario.data.preconditions" :key="i">{{ t }}</li></ul>
    </section>

    <!-- 角色视角横幅：选中角色时提示当前看到的是该角色视角 -->
    <div v-if="roleName" class="role-view-bar">
      👤 当前按角色「{{ roleName }}」查看：步骤表仅显示与该角色相关的步骤{{ roleView ? '，界面/验收为该角色专属描述' : '（该角色暂无专属描述，界面/验收显示共享版）' }}
    </div>

    <section v-if="visibleSteps.length">
      <h3>主流程（{{ visibleSteps.length }}{{ roleName ? ' / 共 ' + (scenario.data.steps?.length || 0) : '' }} 步）</h3>
      <table class="steps">
        <thead><tr><th class="no">步骤</th><th class="actor">角色</th><th>用户操作</th><th>系统响应</th></tr></thead>
        <tbody>
          <tr v-for="s in visibleSteps" :key="s.no" class="step-row" :data-no="s.no" :class="{ system: isSystem(s), flash: flashNo === s.no }" :title="`左键单击弹出操作菜单，右键单击跳到流程原型步骤 ${s.no}`" @click="onRowClick($event, s)" @contextmenu.prevent.stop="emit('focus-step', s.no)">
            <td class="no">{{ s.no }}</td>
            <td class="actor">{{ displayActor(s.actor) }}</td>
            <td>{{ isSystem(s) ? '' : s.action }}</td>
            <td>{{ s.response }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 角色专属说明 -->
    <section v-if="roleView?.notes" class="notes">
      <h3>{{ roleName }} · 补充说明</h3>
      <MermaidBlock :text="roleView.notes" />
    </section>

    <section v-for="v in scenario.data.variants" :key="v.name">
      <h3>{{ v.name }}</h3>
      <table v-if="visibleVariantSteps(v).length" class="steps">
        <thead><tr><th class="no">步骤</th><th class="actor">角色</th><th>用户操作</th><th>系统响应</th></tr></thead>
        <tbody>
          <tr v-for="s in visibleVariantSteps(v)" :key="s.no" class="step-row" :data-no="s.no" :class="{ system: isSystem(s), flash: flashNo === s.no }" :title="`左键单击弹出操作菜单，右键单击跳到流程原型步骤 ${s.no}`" @click="onRowClick($event, s)" @contextmenu.prevent="emit('focus-step', s.no)">
            <td class="no">{{ s.no }}</td>
            <td class="actor">{{ displayActor(s.actor) }}</td>
            <td>{{ isSystem(s) ? '' : s.action }}</td>
            <td>{{ s.response }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">（该小节为说明性内容，无步骤表{{ roleName ? '，或该角色视角下无可见步骤' : '' }}）</p>
    </section>

    <section v-if="uiMaterial.length">
      <h3>关键界面 / 交互（界面素材）{{ roleView?.ui_material?.length ? ` · ${roleName}` : '' }}</h3>
      <ul class="ui-material">
        <li v-for="(t, i) in uiMaterial" :key="i" :style="{ paddingLeft: indentOf(t) + 'px' }">{{ t.replace(/^\s*[-*+]\s*/, '') }}</li>
      </ul>
    </section>

    <section v-if="acceptance.length">
      <h3>验收标准（{{ acceptance.length }}）{{ roleView?.acceptance?.length ? ` · ${roleName}` : '' }}</h3>
      <ul class="accept"><li v-for="(t, i) in acceptance" :key="i">{{ t.replace(/^\[ \]\s*/, '') }}</li></ul>
    </section>

    <section v-if="scenario.data.exceptions?.length">
      <h3>异常分支（{{ scenario.data.exceptions.length }}）</h3>
      <table class="steps">
        <thead><tr><th>异常情况</th><th>系统行为</th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in scenario.data.exceptions" :key="i">
            <td>{{ e.condition }}</td><td>{{ e.handling }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="scenario.pending?.length">
      <h3>待确认 / 待办（{{ scenario.pending.length }}）</h3>
      <ul>
        <li v-for="(p, i) in scenario.pending" :key="i" :class="['pending', p.kind === '已确认' ? 'done' : 'open']">
          <span class="kind">{{ p.kind }}</span>{{ p.text }}
        </li>
      </ul>
    </section>

    <!-- 流程图：仅当 notes 含 mermaid 段时显示 -->
    <section v-if="hasMermaid" class="notes">
      <h3>流程图</h3>
      <MermaidBlock :text="notesText" only="mermaid" />
    </section>

    <!-- 备注 / 补充说明：仅当 notes 含文字段时显示 -->
    <section v-if="hasPlainText" class="notes">
      <h3>备注 / 补充说明</h3>
      <MermaidBlock :text="notesText" only="text" />
    </section>

    <!-- 版本总结（AI 归纳该版本主要交付了什么）+ 变更历史 -->
    <section v-if="scenario.version && (verSummary || verSummaryPending)" class="ver-summary">
      <h3>📦 版本 {{ scenario.version }} 总结</h3>
      <p v-if="verSummary" class="vs-text">{{ verSummary }}</p>
      <p v-else class="dim">AI 正在生成该版本总结…</p>
    </section>

    <details v-if="changes.length" class="changelog">
      <summary>🕘 变更历史（{{ changes.length }}）</summary>
      <div class="chg-list">
        <div v-for="c in changes" :key="c.id" class="chg-item">
          <div class="chg-meta">
            <span class="chg-time">{{ fmtTime(c.created_at) }}</span>
            <span class="chg-origin">{{ originLabel(c.origin) }}</span>
            <span v-if="c.version" class="chg-ver">{{ c.version }}</span>
          </div>
          <div class="chg-summary">{{ c.summary }}</div>
          <div v-if="detailFields(c.detail).length" class="chg-detail dim">字段：{{ detailFields(c.detail).join('、') }}</div>
        </div>
      </div>
    </details>

    <!-- 实测记录：真实环境试用时记不一致点，可在工具开发页一键汇总反馈给开发 agent -->
    <section class="findings">
      <h3>🧪 实测记录 <span v-if="openFindingCount" class="fd-count">{{ openFindingCount }} 条待反馈</span></h3>
      <div class="fd-input">
        <input v-model="findingDraft" placeholder="真实环境试用时与预期不一致的点，记下来…（回车保存）"
               :disabled="findingSaving" @keydown.enter="addFinding" />
        <button class="fd-btn" :disabled="!findingDraft.trim() || findingSaving" @click="addFinding">记录</button>
      </div>
      <div v-if="findings.length" class="fd-list">
        <div v-for="f in findings" :key="f.id" class="fd-item" :class="f.status">
          <span class="fd-badge" :class="f.status">{{ f.status === 'open' ? '待反馈' : '已反馈' }}</span>
          <span class="fd-text">{{ f.text }}</span>
          <span class="fd-time dim">{{ fmtTime(f.created_at) }}</span>
          <button class="fd-del" title="删除这条记录" @click="delFinding(f)">×</button>
        </div>
      </div>
      <div class="dim fd-hint">「待反馈」的记录会在工具开发页点「发送给开发 agent 修正」时一并附上，之后自动转为「已反馈」。</div>
    </section>
    <Teleport to="body">
      <div v-if="actionMenu.show" class="sv-action-menu" :style="{ left: actionMenu.x + 'px', top: actionMenu.y + 'px' }" @click.stop>
        <button class="sv-menu-item" @click="onAnnotate">✎ 批注</button>
        <button class="sv-menu-item" @click="onSendToAi">✦ 发送给 AI</button>
      </div>
    </Teleport>

    <!-- 批注对话框 -->
    <Teleport to="body">
      <div v-if="annotateDialog.show" class="sv-annotate-overlay" @click.self="annotateDialog.show = false">
        <div class="sv-annotate-dialog">
          <h4>批注 — 步骤 {{ annotateDialog.stepNo }}</h4>
          <textarea v-model="annotateDialog.text" placeholder="输入批注内容..." rows="4"></textarea>
          <div class="sv-annotate-ops">
            <button class="sv-btn" @click="annotateDialog.show = false">取消</button>
            <button class="sv-btn sv-btn-primary" @click="saveAnnotation">保存</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style>
/* 全局样式：Teleport 到 body 的元素不受 scoped 限制 */
.sv-action-menu { position: fixed; z-index: 10000; background: #fff; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.15); padding: 4px 0; min-width: 140px; }
.sv-menu-item { display: block; width: 100%; padding: 8px 16px; border: none; background: none; text-align: left; font-size: 13.5px; cursor: pointer; }
.sv-menu-item:hover { background: #e8f0fe; }

.sv-annotate-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.3); z-index: 10001; display: flex; align-items: center; justify-content: center; }
.sv-annotate-dialog { background: #fff; border-radius: 12px; padding: 20px; width: 400px; box-shadow: 0 8px 32px rgba(0,0,0,.2); }
.sv-annotate-dialog h4 { margin: 0 0 12px; font-size: 15px; }
.sv-annotate-dialog textarea { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 8px; font-size: 13.5px; font-family: inherit; resize: vertical; box-sizing: border-box; }
.sv-annotate-ops { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.sv-btn { padding: 6px 14px; border: 1px solid #ddd; background: #f5f5f5; border-radius: 6px; cursor: pointer; }
.sv-btn-primary { background: #1a73e8; border-color: #1a73e8; color: #fff; }
</style>

<style scoped>

<style scoped>
.detail { max-width: 1100px; }
.value { margin: 0 0 12px; padding: 10px 14px; background: var(--accent-soft); border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0; }
.meta-row { display: flex; gap: 20px; flex-wrap: wrap; color: var(--text-dim); font-size: 13px; margin-bottom: 8px; }
section { margin-top: 18px; }
h3 { font-size: 15px; margin: 0 0 8px; }
table.steps { width: 100%; border-collapse: collapse; background: var(--panel); font-size: 13.5px; }
.role-view-bar { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; border-radius: 8px; padding: 8px 14px; font-size: 13px; margin-bottom: 14px; }
/* 场景内角色视角切换 */
.role-switch { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.role-opt { padding: 1px 10px; border-radius: 999px; border: 1px solid var(--border); background: var(--panel); font-size: 12.5px; cursor: pointer; user-select: none; }
.role-opt:hover { border-color: var(--accent); color: var(--accent); }
.role-opt.active { background: var(--accent); border-color: var(--accent); color: #fff; }
table.steps th, table.steps td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; vertical-align: top; user-select: text; -webkit-user-select: text; white-space: pre-line; }
table.steps th { background: var(--bg); font-weight: 600; }
td.no, th.no { width: 48px; text-align: center; }
td.actor, th.actor { width: 150px; }
tr.system td { background: var(--system-soft); }
tr.system td.actor { color: var(--system); font-weight: 600; }
tr.step-row { cursor: text; transition: background .15s; }
tr.step-row:hover td { background: var(--accent-soft); }
tr.step-row.flash td { animation: rowFlash 2.2s ease; }
@keyframes rowFlash {
  0%, 60% { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
  100% { background: transparent; }
}
ul.accept li { margin-bottom: 4px; font-size: 13.5px; }
ul.ui-material { list-style: none; padding: 0; }
ul.ui-material li { font-size: 13.5px; margin-bottom: 3px; }
.pending .kind { display: inline-block; font-size: 11px; border-radius: 4px; padding: 0 6px; margin-right: 8px; }
/* 变更历史 */
.ver-summary { border: 1px solid #ddd6fe; background: #f5f3ff; border-radius: 10px; padding: 12px 16px; margin-top: 14px; }
.ver-summary h3 { margin: 0 0 6px; font-size: 13.5px; color: #6d28d9; }
.vs-text { margin: 0; font-size: 13px; line-height: 1.7; }
.changelog { border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 10px 14px; margin-top: 14px; }
.changelog summary { font-size: 13px; font-weight: 600; cursor: pointer; user-select: none; }
.chg-list { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; max-height: 320px; overflow-y: auto; }
.chg-item { border-left: 2px solid var(--border); padding-left: 10px; }
.chg-meta { display: flex; gap: 8px; align-items: baseline; font-size: 11.5px; color: var(--text-dim); }
.chg-origin { background: var(--panel); border-radius: 3px; padding: 0 5px; }
.chg-ver { background: #ede9fe; color: #7c3aed; border-radius: 3px; padding: 0 5px; }
.chg-summary { font-size: 13px; margin-top: 1px; }
.chg-detail { font-size: 11.5px; margin-top: 1px; }
/* 实测记录 */
.findings { border: 1px solid #bbf7d0; background: #f0fdf4; border-radius: 10px; padding: 12px 16px; }
.findings h3 { margin: 0 0 8px; font-size: 13.5px; color: #15803d; }
.fd-count { background: #22c55e; color: #fff; font-size: 11px; border-radius: 999px; padding: 1px 8px; margin-left: 6px; font-weight: 600; }
.fd-input { display: flex; gap: 8px; }
.fd-input input { flex: 1; border: 1px solid var(--border); border-radius: 6px; padding: 7px 10px; font-size: 13px; }
.fd-btn { padding: 6px 14px; border: 1px solid #16a34a; background: #16a34a; color: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.fd-btn:disabled { opacity: .4; cursor: default; }
.fd-list { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; max-height: 240px; overflow-y: auto; }
.fd-item { display: flex; align-items: baseline; gap: 8px; background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.fd-item.fed { opacity: .65; }
.fd-badge { flex-shrink: 0; font-size: 11px; border-radius: 3px; padding: 0 6px; }
.fd-badge.open { background: #fef3c7; color: #b45309; }
.fd-badge.fed { background: #e5e7eb; color: var(--text-dim); }
.fd-text { flex: 1; min-width: 0; white-space: pre-line; }
.fd-time { flex-shrink: 0; font-size: 11.5px; }
.fd-del { flex-shrink: 0; border: none; background: none; color: var(--text-dim); cursor: pointer; font-size: 14px; padding: 0 2px; }
.fd-del:hover { color: #b91c1c; }
.fd-hint { margin-top: 8px; font-size: 11.5px; }
.pending.open .kind { background: var(--warn-soft); color: var(--warn); }
.pending.done { color: var(--text-dim); }
.pending.done .kind { background: #e5e7eb; color: var(--text-dim); }
.notes { margin-top: 20px; }
.notes h3 { margin: 0 0 8px; font-size: 15px; }
.notes :deep(.plain) { max-height: none; }
.dim { color: var(--text-dim); font-size: 13px; }
</style>
