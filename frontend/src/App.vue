<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import ScenarioView from './components/ScenarioView.vue'
import PrototypePlayer from './components/PrototypePlayer.vue'
import ToolPrototype from './components/ToolPrototype.vue'
import WorkspaceCreate from './components/WorkspaceCreate.vue'
import ProjectHome from './components/ProjectHome.vue'
import ScenarioEditor from './components/ScenarioEditor.vue'
import RoleDetail from './components/RoleDetail.vue'
import RoleCreate from './components/RoleCreate.vue'
import AiPanel from './components/AiPanel.vue'
import ToolDev from './components/ToolDev.vue'
import GlobalNotes from './components/GlobalNotes.vue'
import { parseFlowSteps, stepToScreens } from './composables/stepLink.js'
import { setSysLabel, useSysLabel } from './composables/sysLabel.js'

const { sysLabel } = useSysLabel()

// 场景状态点颜色（四态：草稿/已确认/已开发/已验证）
const STATUSES = ['草稿', '已确认', '已开发', '已验证']
function statusClass(s) {
  return { '草稿': 'st-draft', '已确认': 'st-confirmed', '已开发': 'st-developed', '已验证': 'st-verified' }[s] || 'st-draft'
}

// 场景状态/版本编辑
const versionDraft = ref('')
async function setStatus(s) {
  if (!scenario.value || scenario.value.status === s) return
  // 约束：切到「已确认」必须有版本号（后端同样强制）
  if (s === '已确认') {
    if (!versionDraft.value.trim()) {
      error.value = '切到「已确认」前请先填写版本号'
      return
    }
    if (!confirm(`以版本 ${versionDraft.value.trim()} 确认该场景？确认后才能进入工具开发。`)) return
  }
  try {
    const body = { status: s }
    if (s === '已确认') body.version = versionDraft.value.trim()  // 一并写入，避免中间态无版本
    await api(`/api/workspaces/${ws.value.id}/scenarios/${encodeURIComponent(scenario.value.code)}/status`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    scenario.value.status = s
    if (body.version != null) scenario.value.version = body.version
    openWorkspace(ws.value.id)  // 场景树状态点同步
  } catch (e) { error.value = e.message }
}
async function saveVersion() {
  if (!scenario.value) return
  const v = versionDraft.value.trim()
  if (v === (scenario.value.version || '')) return
  try {
    await api(`/api/workspaces/${ws.value.id}/scenarios/${encodeURIComponent(scenario.value.code)}/status`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: '', version: v }),
    })
    scenario.value.version = v
    openWorkspace(ws.value.id)
  } catch (e) { error.value = e.message }
}

const workspaces = ref([])
const ws = ref(null)          // 当前工作域（含 roles/scenarios）
const scenario = ref(null)    // 当前场景详情
const mode = ref('browse')    // browse | play | tool
const focusTarget = ref(null) // 跨视图聚焦：{ mode:'play'|'browse'|'tool', step:number|null, screenIdx:number|null, seq:number }
const showCreate = ref(false) // 新建项目面板
const editMode = ref(null)    // null | 'edit' | 'create'（场景编辑）
const activeRole = ref(undefined) // undefined=未选；null=系统；对象=角色
const roleCreating = ref(false)   // 主区新增角色表单
const showAi = ref(false)         // AI 面板开关
const devView = ref(false)        // 主区是否显示「开发工具」视图
const gnView = ref(false)         // 主区是否显示「全局约定」视图
const showSysLabelEdit = ref(false) // 系统步骤显示名编辑
const sysLabelDraft = ref('')
const aiPanel = ref(null)         // AiPanel 实例
const mainEl = ref(null)          // 主区元素（截图目标）
const uiSpecRev = ref(0)          // ui_spec 变更信号：AI 应用后自增，驱动 ToolPrototype 重拉规格
const globalNotesRev = ref(0)     // 全局约定变更信号：AI 应用后自增，驱动 GlobalNotes/ToolDev 重拉

function onAiApplied(change) {
  // AI 修改落库后刷新数据，但**保留用户当前所在视图**（不重置 mode/focus），
  // 避免在「工具原型/流程原型」点应用时被拽回「场景详情」。
  if (change?.action === 'update_ui_spec') uiSpecRev.value += 1  // 通知工具原型重拉规格
  if (change?.action === 'update_global_notes') globalNotesRev.value += 1  // 通知全局约定页/开发面板重拉
  const code = scenario.value?.code
  openWorkspace(ws.value.id).then(async () => {
    if (!code) return
    try {
      scenario.value = await api(`/api/workspaces/${ws.value.id}/scenarios/${encodeURIComponent(code)}`)
      // 不触碰 mode / editMode / focusTarget，停留在原视图；ToolPrototype 等会自行重拉 ui-spec
    } catch (e) { error.value = e.message }
  })
}

// 面板切到可见时通知 AiPanel 兜底滚到底（历史加载时面板可能是 v-show 隐藏）
watch(showAi, v => { if (v) aiPanel.value?.onShown?.() })

function sendSelectionToAi() {
  const sel = window.getSelection()?.toString().trim()
  if (!sel) return
  showAi.value = true
  // 等面板渲染后注入
  setTimeout(() => aiPanel.value?.setSelection(sel), 50)
}

// 工具原型「生成需求文本」：打开 AI 面板并预填反写指令
function onGenRequirements(code) {
  showAi.value = true
  const prompt = `请按「原型先行工作流」把场景 ${code} 的当前工具原型（含我的界面说明 notes）反写为场景需求文本，给出 update_scenario 的 change 块（可一次覆盖 value/preconditions/steps/acceptance/exceptions 中受影响的字段），summary 里写清从原型反写覆盖了哪些字段。`
  setTimeout(() => aiPanel.value?.prefill(prompt), 50)
}

// 场景详情「重新生成工具原型」：打开 AI 面板并预填生成指令
function onGenPrototype(code) {
  showAi.value = true
  const prompt = `请基于场景 ${code} 的当前完整数据（value/preconditions/steps/acceptance/exceptions 等），重新生成工具原型 ui_spec。给出 update_ui_spec 的 change 块，summary 里写清生成依据了哪些场景字段。`
  setTimeout(() => aiPanel.value?.prefill(prompt), 50)
}

function onMainMouseUp() {
  const sel = window.getSelection()?.toString().trim()
  selText.value = sel || ''
}
const selText = ref('')

// 场景详情页：整行发送给 AI
function onScenarioSendToAi(text) {
  showAi.value = true
  setTimeout(() => aiPanel.value?.setSelection(text), 50)
}

function selectRole(r) {
  activeRole.value = r
  roleCreating.value = false
  devView.value = false
  gnView.value = false
  scenario.value = null
  editMode.value = null
}

function startCreateRole() {
  roleCreating.value = true
  activeRole.value = undefined
  scenario.value = null
  editMode.value = null
}

async function onRoleCreated(id) {
  await openWorkspace(ws.value.id)
  roleCreating.value = false
  const r = ws.value.roles.find(x => x.id === id)
  if (r) selectRole(r)
}

async function onRoleChanged(updated) {
  await openWorkspace(ws.value.id)
  if (updated) {
    // 改名/改内容后保持选中
    const r = ws.value.roles.find(x => x.id === updated.id)
    activeRole.value = r ?? undefined
  } else {
    activeRole.value = undefined  // 当前角色被删除
  }
}

function openScenarioFromRole(code) {
  activeRole.value = undefined
  openScenario(code)
}

// 场景详情页内的角色视角切换：只换视角，停留在场景页（不跳角色详情）
// 传 '' 表示取消视角；同名再点一次=取消
function switchScenarioRole(name) {
  if (!name) { scenarioRoleName.value = ''; return }
  scenarioRoleName.value = scenarioRoleName.value === name ? '' : name
}
// 场景内的视角选择独立于左侧角色选中态（activeRole 是"看角色资料"的入口）
const scenarioRoleName = ref('')
const scenarioActiveRole = computed(() =>
  scenarioRoleName.value ? (ws.value?.roles.find(r => r.name === scenarioRoleName.value) || { name: scenarioRoleName.value }) : undefined
)
const loading = ref(false)
const error = ref('')

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function loadWorkspaces() {
  workspaces.value = await api('/api/workspaces')
}

async function saveSysLabel() {
  try {
    const settings = await api(`/api/workspaces/${ws.value.id}/settings`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ system_label: sysLabelDraft.value.trim() }),
    })
    setSysLabel(settings)
    showSysLabelEdit.value = false
  } catch (e) { error.value = e.message }
}

async function openWorkspace(id) {
  loading.value = true
  error.value = ''
  try {
    ws.value = await api(`/api/workspaces/${id}`)
    setSysLabel(ws.value.settings)
    sysLabelDraft.value = ws.value.settings?.system_label || ''
    scenario.value = null
    devView.value = false
    gnView.value = false
  } catch (e) { error.value = e.message }
  loading.value = false
}

async function openScenario(code) {
  error.value = ''
  try {
    scenario.value = await api(`/api/workspaces/${ws.value.id}/scenarios/${encodeURIComponent(code)}`)
    versionDraft.value = scenario.value.version || ''
    scenarioRoleName.value = ''  // 换场景重置视角
    mode.value = 'browse'
    editMode.value = null
    devView.value = false
    gnView.value = false
    activeRole.value = undefined
    roleCreating.value = false
  } catch (e) { error.value = e.message }
}

async function onScenarioSaved(code) {
  editMode.value = null
  await openWorkspace(ws.value.id)
  await openScenario(code)
}

async function onScenarioDeleted() {
  editMode.value = null
  scenario.value = null
  await openWorkspace(ws.value.id)
}

async function reimport() {
  loading.value = true
  try {
    await api(`/api/workspaces/${ws.value.id}/reimport`, { method: 'POST' })
    await openWorkspace(ws.value.id)
  } catch (e) { error.value = e.message }
  loading.value = false
}

async function onCreated(id) {
  showCreate.value = false
  await loadWorkspaces()
  await openWorkspace(id)
}

function goHome() {
  ws.value = null
  scenario.value = null
  activeRole.value = undefined
  roleCreating.value = false
  devView.value = false
  gnView.value = false
  error.value = ''
}

// 打开「开发工具」视图（工作域级，退出场景/角色选中态）
function openDevView() {
  devView.value = true
  gnView.value = false
  scenario.value = null
  editMode.value = null
  activeRole.value = undefined
  roleCreating.value = false
}

// 打开「全局约定」视图（工作域级）
function openGnView() {
  gnView.value = true
  devView.value = false
  scenario.value = null
  editMode.value = null
  activeRole.value = undefined
  roleCreating.value = false
}

async function deleteWorkspaceById(w) {
  if (!confirm(`确定删除项目「${w.name}」？其角色、场景与界面规格将一并删除，不可恢复。`)) return
  try {
    await api(`/api/workspaces/${w.id}`, { method: 'DELETE' })
    if (ws.value?.id === w.id) { ws.value = null; scenario.value = null }
    await loadWorkspaces()
  } catch (e) { error.value = e.message }
}

function exportProject() {
  window.open(`/api/workspaces/${ws.value.id}/export/pdf`, '_blank')
}

function exportMarkdown() {
  window.open(`/api/workspaces/${ws.value.id}/export/md`, '_blank')
}

function exportScenario(code) {
  window.open(`/api/workspaces/${ws.value.id}/scenarios/${encodeURIComponent(code)}/export/pdf`, '_blank')
}

// ---------------- 跨视图步骤联动 ----------------
let focusSeq = 0
// 请求切到某视图并定位步骤：mode='play'/'browse' 时给 step 号；mode='tool' 时给 screenIdx
function requestFocus(targetMode, { step = null, screenIdx = null } = {}) {
  focusSeq += 1
  mode.value = targetMode
  focusTarget.value = { mode: targetMode, step, screenIdx, seq: focusSeq }
}

// 当前场景的 ui-spec（仅用于步骤反查，不改变工具原型自身加载）
const uiSpecForLink = ref(null)
watch([() => ws.value?.id, () => scenario.value?.code], async ([wid, code]) => {
  uiSpecForLink.value = null
  if (!wid || !code) return
  try {
    const res = await fetch(`/api/workspaces/${wid}/scenarios/${encodeURIComponent(code)}/ui-spec`)
    if (!res.ok) return
    uiSpecForLink.value = (await res.json()).spec || null
  } catch { /* 无规格则忽略 */ }
})
const step2screens = computed(() => stepToScreens(uiSpecForLink.value))
// 当前工具屏覆盖的步骤号（工具视图下用于头部反显）
const currentScreenSteps = computed(() => {
  const sc = focusTarget.value?.screenIdx != null
    ? uiSpecForLink.value?.screens?.[focusTarget.value.screenIdx]
    : null
  return parseFlowSteps(sc?.flowSteps)
})
// 某步骤号对应的工具屏索引列表（步骤号统一字符串，支持 "3a" 变体号）
function screensForStep(no) {
  return step2screens.value.get(String(no)) || []
}

async function deleteWorkspace() {
  await deleteWorkspaceById(ws.value)
}

const domains = computed(() => {
  if (!ws.value) return []
  const groups = new Map()
  for (const sc of ws.value.scenarios) {
    const key = sc.domain || '未分组'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(sc)
  }
  return [...groups.entries()].map(([name, items]) => ({ name, items }))
})

onMounted(loadWorkspaces)
</script>

<template>
  <header class="topbar">
    <span class="logo" :class="{ clickable: ws }" title="返回项目列表" @click="goHome">场景流程原型平台</span>
    <button class="btn ghost" @click="showCreate = true">＋ 新建项目</button>
    <template v-if="ws">
      <span class="crumb">/ {{ ws.name }}</span>
      <select v-if="workspaces.length > 1" :value="ws.id" @change="openWorkspace(Number($event.target.value))">
        <option v-for="w in workspaces" :key="w.id" :value="w.id">{{ w.name }}</option>
      </select>
      <button class="btn ghost" :disabled="loading || !ws.source_path" :title="ws.source_path ? '从源文档重新解析导入（角色只增不覆盖）' : '该项目未绑定场景文档（空项目）'" @click="reimport">↻ 重新导入</button>
      <button class="btn ghost" title="导出整个项目（角色定义 + 全部场景 + 过程拆解）为 PDF" @click="exportProject">⤓ 导出 PDF</button>
      <button class="btn ghost" title="从平台数据重新生成整份场景文档 Markdown（平台为唯一事实源，导出文档请勿手改）" @click="exportMarkdown">⤓ 导出 MD</button>
      <button class="btn ghost" :class="{ active: showAi }" title="AI 助手：选中内容/截图对话，提议修改经确认后落库" @click="showAi = !showAi">✦ AI</button>
      <button class="btn ghost danger" title="删除当前项目（含角色、场景、界面规格）" @click="deleteWorkspace">🗑 删除项目</button>
    </template>
    <span v-if="error" class="error">{{ error }}</span>
  </header>

  <WorkspaceCreate v-if="showCreate" @close="showCreate = false" @created="onCreated" />

  <div class="body">
    <!-- 全局首页：项目列表 -->
    <ProjectHome v-if="!ws" :workspaces="workspaces" class="home-wrap" @open="openWorkspace" @create="showCreate = true" @delete="deleteWorkspaceById" />

    <template v-else>
    <aside class="sidebar">
      <div class="side-section">
        <div class="side-title">
          工具
          <button class="link" title="自定义工具名称（流程中自动执行步骤的执行者）" @click="showSysLabelEdit = !showSysLabelEdit">⚙</button>
        </div>
        <div v-if="showSysLabelEdit" class="syslabel-edit">
          <input v-model="sysLabelDraft" placeholder="工具名称，如：扩展" @keydown.enter="saveSysLabel" />
          <div class="syslabel-ops">
            <button class="btn primary sm" @click="saveSysLabel">保存</button>
            <button class="link" @click="showSysLabelEdit = false">取消</button>
          </div>
          <div class="syslabel-hint">流程中由工具自动执行的步骤显示为该名称；留空恢复「系统」。</div>
        </div>
        <div class="role-chip system" :class="{ active: activeRole === null }" @click="selectRole(null)">{{ sysLabel }}</div>
        <button class="btn dev-entry" :class="{ active: gnView }" title="跨场景的架构规则/外部系统约定：写场景时沉淀到这里，对话 AI 与后台开发都会遵守" @click="openGnView">📜 全局约定</button>
        <button class="btn dev-entry" :class="{ active: devView }" title="确认原型与文档后，让 claude 在独立文件夹里开发真正可发布的工具" @click="openDevView">🛠 开发工具</button>
      </div>
      <div class="side-section">
        <div class="side-title">
          角色（{{ ws.roles.length }}）
          <button class="link" @click="startCreateRole">＋ 新增</button>
        </div>
        <div v-for="r in ws.roles" :key="r.id" class="role-chip" :class="{ active: activeRole?.id === r.id }" :title="r.core_work" @click="selectRole(r)">{{ r.name }}</div>
      </div>
      <div class="side-section grow">
        <div class="side-title">
          场景（{{ ws.scenarios.length }}）
          <button class="link" @click="editMode = 'create'; scenario = null; activeRole = undefined; roleCreating = false">＋ 新建场景</button>
        </div>
        <div v-for="d in domains" :key="d.name" class="domain">
          <div class="domain-name">{{ d.name }}</div>
          <div
            v-for="sc in d.items" :key="sc.code"
            class="scenario-item"
            :class="{ active: scenario?.code === sc.code }"
            @click="openScenario(sc.code)"
          >
            <span class="code">{{ sc.code }}</span>
            <span class="status-dot" :class="statusClass(sc.status)" :title="`状态：${sc.status}${sc.version ? ' · ' + sc.version : ''}`"></span>
            <span class="title">{{ sc.title }}</span>
            <span v-if="sc.origin === 'manual'" class="badge manual" title="手工创建">手</span>
            <span v-else-if="sc.edited" class="badge edited" title="已在平台编辑，重导不覆盖">编</span>
            <span v-if="sc.pending_count" class="badge warn" :title="`待确认/待办 ${sc.pending_count} 条`">{{ sc.pending_count }}</span>
          </div>
        </div>
      </div>
    </aside>

    <main class="main" ref="mainEl" @mouseup="onMainMouseUp">
      <button v-if="selText" class="send-ai-fab" title="把选中内容发给 AI" @click="sendSelectionToAi">✦ 发给 AI</button>
      <ToolDev v-if="devView" :ws-id="ws.id" :reload-rev="globalNotesRev" @open-scenario="openScenario" />
      <GlobalNotes v-else-if="gnView" :ws-id="ws.id" :reload-rev="globalNotesRev" />
      <ScenarioEditor v-else-if="editMode === 'create'" :ws-id="ws.id" :scenario="null" :roles="ws.roles" @close="editMode = null" @saved="onScenarioSaved" @deleted="onScenarioDeleted" />
      <div v-else-if="loading" class="placeholder">加载中…</div>
      <RoleCreate v-else-if="roleCreating" :ws-id="ws.id" @close="roleCreating = false" @created="onRoleCreated" />
      <RoleDetail v-else-if="activeRole !== undefined" :ws-id="ws.id" :role="activeRole" @open-scenario="openScenarioFromRole" @changed="onRoleChanged" />
      <template v-else-if="scenario">
        <ScenarioEditor v-if="editMode === 'edit'" :ws-id="ws.id" :scenario="scenario" :roles="ws.roles" @close="editMode = null" @saved="onScenarioSaved" @deleted="onScenarioDeleted" />
        <template v-else>
        <div class="scenario-head">
          <div class="head-left">
            <h2>场景 {{ scenario.code }}：{{ scenario.title }}</h2>
            <div class="status-bar">
              <div class="status-switch" title="场景生命周期：草稿 → 已确认 → 已开发 → 已验证。修改内容会自动回退到草稿。">
                <button
                  v-for="s in STATUSES" :key="s"
                  class="status-btn" :class="[statusClass(s), { active: scenario.status === s }]"
                  @click="setStatus(s)"
                >{{ s }}</button>
              </div>
              <span class="version-field" :title="scenario.status === '草稿' ? '该场景归属的工具发布版本（如 v0.1.0），失焦保存。切到「已确认」时必填' : '该场景归属的工具发布版本（如 v0.1.0），失焦保存'">
                <input v-model="versionDraft" :placeholder="scenario.status === '草稿' ? '版本（确认时必填）' : '版本'" @blur="saveVersion" @keydown.enter="$event.target.blur()" />
              </span>
            </div>
          </div>
          <div class="head-actions">
            <button class="btn" :class="{ primary: mode === 'browse' }" @click="mode = 'browse'">场景详情</button>
            <button class="btn" :class="{ primary: mode === 'play' }" @click="mode = 'play'">▶ 流程原型</button>
            <button class="btn" :class="{ primary: mode === 'tool' }" @click="mode = 'tool'">🛠 工具原型</button>
            <button class="btn" @click="editMode = 'edit'">✎ 编辑场景</button>
            <button class="btn" title="基于当前场景完整数据，让 AI 重新生成工具原型" @click="onGenPrototype(scenario.code)">⤒ 重新生成工具原型</button>
            <button class="btn" title="导出该场景（含过程拆解）为 PDF" @click="exportScenario(scenario.code)">⤓ 导出</button>
          </div>
        </div>
        <div v-if="mode === 'tool' && currentScreenSteps.length" class="focus-hint">
          当前界面屏对应流程步骤：{{ currentScreenSteps.join('、') }}
          <button class="link" @click="requestFocus('play', { step: currentScreenSteps[0] })">→ 在流程原型中查看</button>
        </div>
        <ScenarioView v-if="mode === 'browse'" :scenario="scenario" :focus="focusTarget" :active-role="scenarioActiveRole" :ws-id="ws.id" @focus-step="(no) => requestFocus('play', { step: no })" @send-to-ai="onScenarioSendToAi" @switch-role="switchScenarioRole" />
        <PrototypePlayer v-else-if="mode === 'play'" :ws-id="ws.id" :scenario="scenario" :roles="ws.roles" :focus="focusTarget" :reload-rev="uiSpecRev" @focus-tool="(idx) => requestFocus('tool', { screenIdx: idx })" @focus-step="(no) => requestFocus('browse', { step: no })" />
        <ToolPrototype v-else :ws-id="ws.id" :code="scenario.code" :roles="ws.roles" :focus="focusTarget" :reload-rev="uiSpecRev" @focus-step="(no) => requestFocus('play', { step: no })" @gen-requirements="onGenRequirements" />
        </template>
      </template>
      <div v-else class="placeholder">
        <p>从左侧选择场景或角色，或点「＋ 新建场景」。</p>
      </div>
    </main>
    <AiPanel v-show="showAi" ref="aiPanel" :key="ws.id" :ws-id="ws.id" :scenario-code="scenario?.code || ''" :view="mode" :capture-el="mainEl" @applied="onAiApplied" />
    </template>
  </div>
</template>

<style scoped>
.topbar { display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--border); }
.logo { font-weight: 700; }
.logo.clickable { cursor: pointer; color: var(--accent); }
.crumb { color: var(--text-dim); }
.home-wrap { flex: 1; overflow-y: auto; }
.ws-name { color: var(--text-dim); }
.error { color: #b91c1c; margin-left: auto; font-size: 13px; }
.body { flex: 1; display: flex; min-height: 0; }
.sidebar { width: 280px; flex-shrink: 0; background: var(--panel); border-right: 1px solid var(--border); overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 16px; }
.side-title { font-size: 12px; color: var(--text-dim); margin-bottom: 6px; display: flex; align-items: center; justify-content: space-between; }
.syslabel-edit { margin-bottom: 8px; padding: 8px; border: 1px dashed var(--border); border-radius: 6px; }
.syslabel-edit input { width: 100%; border: 1px solid var(--border); border-radius: 4px; padding: 4px 6px; font-size: 12px; box-sizing: border-box; }
.syslabel-ops { display: flex; gap: 8px; margin-top: 6px; align-items: center; }
.syslabel-hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.4; }
.side-title .link { border: none; background: none; color: var(--accent); font-size: 12px; cursor: pointer; padding: 0; }
.role-chip { display: inline-block; padding: 2px 10px; margin: 0 6px 6px 0; border: 1px solid var(--border); border-radius: 999px; font-size: 13px; cursor: pointer; }
.role-chip:hover { border-color: var(--accent); }
.role-chip.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.role-chip.system { background: var(--system-soft); color: var(--system); border-color: transparent; }
.role-chip.system.active { background: var(--system); color: #fff; }
.dev-entry { margin-top: 8px; width: 100%; font-size: 12.5px; padding: 5px 0; border: 1px dashed var(--border); background: transparent; color: var(--accent); border-radius: 6px; cursor: pointer; }
.dev-entry:hover { border-color: var(--accent); background: var(--accent-soft); }
.dev-entry.active { background: var(--accent); border-color: var(--accent); color: #fff; border-style: solid; }
.grow { flex: 1; }
.domain { margin-bottom: 10px; }
.domain-name { font-size: 12px; font-weight: 600; color: var(--text-dim); margin: 6px 0 4px; }
.scenario-item { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 6px; cursor: pointer; font-size: 14px; }
.scenario-item:hover { background: var(--bg); }
.scenario-item.active { background: var(--accent-soft); color: var(--accent); }
.scenario-item .code { font-weight: 600; min-width: 34px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.status-dot.st-draft { background: #cbd5e1; }
.status-dot.st-confirmed { background: #3b82f6; }
.status-dot.st-developed { background: #a855f7; }
.status-dot.st-verified { background: #10b981; }
.scenario-item .title { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.badge.warn { background: var(--warn-soft); color: var(--warn); border-radius: 999px; font-size: 11px; padding: 0 7px; }
.badge.manual { background: #ecfdf5; color: #047857; border-radius: 4px; font-size: 11px; padding: 0 5px; }
.badge.edited { background: var(--accent-soft); color: var(--accent); border-radius: 4px; font-size: 11px; padding: 0 5px; }
.main { flex: 1; min-width: 0; overflow-y: auto; padding: 16px 20px; position: relative; }
.send-ai-fab { position: sticky; bottom: 24px; float: right; z-index: 30; border: none; background: var(--accent); color: #fff; border-radius: 999px; padding: 8px 16px; font-size: 13px; cursor: pointer; box-shadow: 0 4px 14px rgba(37,99,235,.35); }
.btn.ghost.active { background: var(--accent-soft); border-radius: 6px; }
.scenario-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; position: sticky; top: -16px; z-index: 20; background: var(--bg); padding: 10px 0; }
.scenario-head h2 { margin: 0; font-size: 18px; }
.head-left { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.status-bar { display: flex; align-items: center; gap: 10px; }
.status-switch { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.status-btn { border: none; background: var(--panel); padding: 3px 10px; font-size: 12px; cursor: pointer; color: var(--text-dim); border-right: 1px solid var(--border); }
.status-btn:last-child { border-right: none; }
.status-btn.active { font-weight: 600; }
.status-btn.st-draft.active { background: #e2e8f0; color: #475569; }
.status-btn.st-confirmed.active { background: #dbeafe; color: #1d4ed8; }
.status-btn.st-developed.active { background: #f3e8ff; color: #7c3aed; }
.status-btn.st-verified.active { background: #d1fae5; color: #047857; }
.version-field input { width: 90px; border: 1px solid var(--border); border-radius: 6px; padding: 3px 8px; font-size: 12px; }
.version-field input::placeholder { color: var(--text-dim); }
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.btn { padding: 6px 14px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.ghost { border: none; background: transparent; color: var(--accent); }
.btn.ghost.danger { color: #dc2626; }
.btn:disabled { opacity: .4; cursor: default; }
.placeholder { color: var(--text-dim); padding: 40px; text-align: center; }
.focus-hint { margin-bottom: 12px; font-size: 12.5px; color: var(--text-dim); background: var(--accent-soft); border-radius: 6px; padding: 6px 12px; display: flex; align-items: center; gap: 8px; }
.focus-hint .link { border: none; background: none; color: var(--accent); cursor: pointer; font-size: 12.5px; padding: 0; }
</style>
