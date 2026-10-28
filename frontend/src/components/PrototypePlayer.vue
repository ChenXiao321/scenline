<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { stepToScreens } from '../composables/stepLink.js'
import { useSysLabel } from '../composables/sysLabel.js'

const props = defineProps({ wsId: Number, scenario: Object, roles: Array, focus: Object, reloadRev: Number })
const emit = defineEmits(['focus-tool', 'focus-step'])

const { sysLabel, displayActor } = useSysLabel()

// ---------------- 过程拆解规格（process_spec 表） ----------------
const detailSpec = ref(null)   // { kind:'process', stepDetails: { '1': {...} } }
const specLoaded = ref(false)
const editing = ref(false)
const draft = ref('')
const draftError = ref('')

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function loadSpec() {
  specLoaded.value = false
  try {
    const r = await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}/process-spec`)
    detailSpec.value = r.spec?.kind === 'process' ? r.spec : null
  } catch { detailSpec.value = null }
  specLoaded.value = true
}
watch(() => [props.wsId, props.scenario?.code], loadSpec, { immediate: true })

function openEditor() {
  draft.value = detailSpec.value
    ? JSON.stringify(detailSpec.value, null, 2)
    : JSON.stringify(defaultSpec(), null, 2)
  draftError.value = ''
  editing.value = true
}
async function saveSpec() {
  let obj
  try { obj = JSON.parse(draft.value) } catch (e) { draftError.value = 'JSON 解析失败：' + e.message; return }
  obj.kind = 'process'
  try {
    await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}/process-spec`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec: obj }),
    })
    detailSpec.value = obj
    editing.value = false
  } catch (e) { draftError.value = e.message }
}
function defaultSpec() {
  const stepDetails = {}
  for (const s of props.scenario.data.steps || []) {
    stepDetails[String(s.no)] = { do: '', calls: [], reads: [], writes: [], shows: [] }
  }
  return { kind: 'process', title: `${props.scenario.title} — 过程拆解`, desc: '', stepDetails }
}

// ---------------- 流程段：主流程 + 各变体（分支） ----------------
const flows = computed(() => {
  const list = []
  if (props.scenario.data.steps?.length) {
    list.push({ key: 'main', name: '主流程', steps: props.scenario.data.steps })
  }
  for (const v of props.scenario.data.variants || []) {
    if (v.steps?.length) list.push({ key: v.name, name: v.name, steps: v.steps })
  }
  return list
})
const activeFlowKey = ref(flows.value[0]?.key ?? '')
// 注意：不要用 flows 的 watch 在挂载时重置 selectedNo——会把跨视图 focus 定位清掉。
// 这里只在 flows 内容真的变化（如切场景）时重置展开态。
watch(() => props.scenario?.code, () => { activeFlowKey.value = flows.value[0]?.key ?? ''; selectedNo.value = null; expandAll.value = false })
const activeFlow = computed(() => flows.value.find(f => f.key === activeFlowKey.value))

// ---------------- 展开状态 ----------------
// 步骤号统一按字符串比较：变体号是 "3a"/"1b" 这类非数字，Number() 会得到 NaN 永远匹配不上
const selectedNo = ref(null)   // 当前单展开的步骤号（字符串）
const expandAll = ref(false)

function toggleStep(no) {
  expandAll.value = false
  const n = String(no)
  selectedNo.value = selectedNo.value === n ? null : n
}
function toggleAll() {
  expandAll.value = !expandAll.value
  if (expandAll.value) selectedNo.value = null
}
function isOpen(no) {
  return expandAll.value || (selectedNo.value != null && String(selectedNo.value) === String(no))
}

// ---------------- 跨视图联动 ----------------
const flashNo = ref(null)   // 刚定位到、短暂高亮的步骤号
async function applyFocus() {
  if (props.focus?.mode !== 'play') return
  const no = props.focus.step
  if (no == null) return
  expandAll.value = false
  selectedNo.value = String(no)
  flashNo.value = String(no)
  // 变体步骤（3a 等）在对应变体 tab 里，先切过去再展开
  const flow = flows.value.find(f => (f.steps || []).some(s => String(s.no) === String(no)))
  if (flow) activeFlowKey.value = flow.key
  await nextTick()
  await nextTick()   // 等卡片 v-if 渲染进 DOM
  document.querySelector(`.card[data-no="${no}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  setTimeout(() => { if (flashNo.value === String(no)) flashNo.value = null }, 2200)
}
// seq 变化时消费 focus；immediate 兜底——组件因切视图重挂载时，挂载当拍也消费一次 pending focus
watch(() => props.focus?.seq, () => applyFocus(), { immediate: true })
// 步骤号对应的工具屏索引列表（本组件自加载 ui-spec 建反查表，不经 App 传 prop）
const uiSpec = ref(null)
async function loadUiSpec() {
  uiSpec.value = null
  try {
    const r = await api(`/api/workspaces/${props.wsId}/scenarios/${encodeURIComponent(props.scenario.code)}/ui-spec`)
    uiSpec.value = r.spec || null
  } catch { uiSpec.value = null }
}
watch(() => [props.wsId, props.scenario?.code], loadUiSpec, { immediate: true })
// AI 应用 ui_spec 变更后，App 递增 reloadRev 通知重拉反查表
watch(() => props.reloadRev, (v, old) => { if (v && v !== old) loadUiSpec() })
const step2screens = computed(() => stepToScreens(uiSpec.value))
function screensOf(no) {
  // 反查表 key 是字符串步骤号（支持 "3a" 这类变体号）
  return step2screens.value.get(String(no)) || []
}
// 是否有任一步骤关联了工具屏（决定「打开工具原型」按钮显隐）
const hasAnyLink = computed(() => {
  for (const s of activeFlow.value?.steps || []) if (screensOf(s.no).length) return true
  return false
})
// 跳到工具原型：优先当前选中步骤对应的屏，否则第一屏
function openTool() {
  const no = selectedNo.value
  const idx = (no != null && screensOf(no)[0] != null) ? screensOf(no)[0] : 0
  emit('focus-tool', idx)
}

// ---------------- 拆解卡数据 ----------------
function stepSummary(s) {
  const text = s.actor === '系统' ? (s.response || s.action) : s.action
  return text.length > 42 ? text.slice(0, 42) + '…' : text
}

function detailFor(s) {
  const d = detailSpec.value?.stepDetails?.[String(s.no)]
  return {
    actors: d?.actors || [],
    fallbackText: s.actor === '系统' ? (s.response || s.action) : s.action,
    fallbackActor: s.actor,
    hasSpec: !!d && (d.actors || []).length > 0,
  }
}

// 动作标签：kind → 文本/配色
const KINDS = {
  act:   { label: '动作', color: '#334155' },
  call:  { label: '调用', color: '#0891b2' },
  read:  { label: '获取', color: '#059669' },
  write: { label: '存储', color: '#d97706' },
  show:  { label: '展示', color: '#7c3aed' },
}
function kindOf(k) { return KINDS[k] || KINDS.act }
</script>

<template>
  <div class="proc">
    <!-- 工具条 -->
    <div class="toolbar">
      <div class="flow-tabs">
        <button
          v-for="f in flows" :key="f.key"
          class="tab" :class="{ active: f.key === activeFlowKey }"
          @click="activeFlowKey = f.key; selectedNo = null; expandAll = false"
        >{{ f.name }}（{{ f.steps.length }} 步）</button>
      </div>
      <div class="controls">
        <button v-if="hasAnyLink" class="btn tool-open" title="跳到工具原型（定位到当前步骤对应屏，或第一屏）" @click="openTool">🛠 打开工具原型</button>
        <button class="btn" :class="{ primary: expandAll }" @click="toggleAll">{{ expandAll ? '收起全部' : '全展开' }}</button>
        <button class="btn" @click="openEditor">✎ 编辑拆解</button>
      </div>
    </div>

    <!-- 拆解规格编辑器 -->
    <div v-if="editing" class="editor">
      <div class="editor-bar">
        <b>过程拆解规格（JSON）— stepDetails 按步骤号键控；字段 do / calls / reads / writes / shows</b>
        <span class="spacer"></span>
        <button class="btn" @click="editing = false">取消</button>
        <button class="btn primary" @click="saveSpec">保存拆解</button>
      </div>
      <div v-if="draftError" class="draft-error">{{ draftError }}</div>
      <textarea v-model="draft" class="spec-text" spellcheck="false"></textarea>
    </div>

    <template v-else>
      <!-- 顶部：步骤条 -->
      <div class="stepstrip">
        <div
          v-for="(s, idx) in activeFlow?.steps || []" :key="s.no"
          class="stepnode"
          :class="{ open: isOpen(s.no), system: s.actor === '系统' }"
          :title="`左键单击展开/收起，右键单击返回场景详情步骤 ${s.no}`"
          @click="toggleStep(s.no)"
          @contextmenu.prevent.stop="emit('focus-step', s.no)"
        >
          <span class="s-no">{{ s.no }}</span>
          <span class="s-text" :title="s.actor === '系统' ? (s.response || s.action) : s.action">{{ stepSummary(s) }}</span>
          <span class="s-actor">{{ displayActor(s.actor) }}</span>
          <button
            v-if="screensOf(s.no).length"
            class="s-tool" :title="`跳到工具原型第 ${screensOf(s.no)[0] + 1} 屏`"
            @click.stop="emit('focus-tool', screensOf(s.no)[0])"
          >🛠</button>
          <span v-if="idx < (activeFlow?.steps.length - 1)" class="s-arrow">→</span>
        </div>
      </div>

      <!-- 拆解区 -->
      <div v-if="!expandAll && selectedNo === null" class="hint">
        点击上方任一步骤展开其拆解（谁做 / 调用谁 / 获取谁 / 存储谁 / 展示什么）；或点「全展开」铺开全部。
        <span v-if="!detailSpec">该场景还没有过程拆解规格，点「✎ 编辑拆解」生成草稿。</span>
      </div>

      <div v-else class="cards" :class="{ single: !expandAll }">
        <template v-for="s in activeFlow?.steps || []" :key="s.no">
          <div v-if="isOpen(s.no)" class="card" :data-no="s.no" :class="{ system: s.actor === '系统', flash: flashNo === s.no }" @contextmenu.prevent.stop="emit('focus-step', s.no)">
            <div class="card-head">
              <span class="c-no">{{ s.no }}</span>
              <span class="c-title">{{ detailFor(s).fallbackText }}</span>
              <span v-if="screensOf(s.no).length" class="c-link">
                <button
                  v-for="idx in screensOf(s.no)" :key="idx"
                  class="link-chip" :title="`跳到工具原型第 ${idx + 1} 屏`"
                  @click.stop="emit('focus-tool', idx)"
                >🛠 界面 {{ idx + 1 }}</button>
              </span>
              <span v-if="!detailFor(s).hasSpec" class="c-todo">待补充</span>
            </div>
            <!-- 按对象分块 -->
            <div v-if="detailFor(s).hasSpec" class="c-actors">
              <div v-for="(a, i) in detailFor(s).actors" :key="i" class="actor-block" :class="{ sys: a.kind === 'system' }">
                <div class="actor-name">{{ a.name }}<span v-if="a.kind === 'system'" class="sys-tag">{{ sysLabel }}</span></div>
                <ul class="actor-items">
                  <li v-for="(it, j) in a.items" :key="j">
                    <span class="k-tag" :style="{ color: kindOf(it.kind).color, borderColor: kindOf(it.kind).color }">{{ kindOf(it.kind).label }}</span>{{ it.text }}
                  </li>
                </ul>
              </div>
            </div>
            <!-- 无规格时回退：显示步骤原文 -->
            <div v-else class="c-fallback">
              <span class="c-actor" :class="{ sys: s.actor === '系统' }">{{ displayActor(s.actor) }}</span>：{{ detailFor(s).fallbackText }}
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.proc { display: flex; flex-direction: column; gap: 12px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
.flow-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { padding: 5px 12px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; font-size: 13px; }
.tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.controls { display: flex; gap: 8px; align-items: center; }
.btn { padding: 5px 12px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; }
.btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.spacer { flex: 1; }

.editor { display: flex; flex-direction: column; gap: 8px; height: calc(100vh - 160px); }
.editor-bar { display: flex; align-items: center; gap: 8px; }
.draft-error { color: #b91c1c; font-size: 13px; }
.spec-text { flex: 1; font-family: Consolas, monospace; font-size: 13px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; resize: none; background: var(--panel); }

/* 顶部步骤条 */
.stepstrip { display: flex; flex-wrap: wrap; gap: 0; align-items: stretch; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); padding: 10px; row-gap: 8px; }
.stepnode { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 6px; cursor: pointer; max-width: 260px; }
.stepnode:hover { background: var(--bg); }
.stepnode.open { background: var(--accent-soft); box-shadow: inset 0 0 0 1.5px var(--accent); }
.s-no { display: inline-flex; min-width: 22px; height: 22px; border-radius: 50%; background: var(--accent); color: #fff; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.stepnode.system .s-no { background: var(--system, #7c3aed); }
.s-text { font-size: 12.5px; line-height: 1.3; }
.s-actor { font-size: 11px; color: var(--text-dim); flex-shrink: 0; }
.s-arrow { color: var(--border); margin-left: 2px; }
.s-tool { border: none; background: #ede9fe; color: #7c3aed; border-radius: 4px; font-size: 11px; padding: 0 4px; cursor: pointer; flex-shrink: 0; line-height: 1.6; }
.s-tool:hover { background: #7c3aed; color: #fff; }
.tool-open { border-color: #7c3aed; color: #7c3aed; }
.tool-open:hover { background: #ede9fe; }

.hint { padding: 24px; text-align: center; color: var(--text-dim); font-size: 13px; border: 1px dashed var(--border); border-radius: 8px; }

/* 拆解卡 */
.cards { display: flex; flex-direction: column; gap: 10px; }
.card { border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 12px 16px; box-shadow: 0 2px 10px rgba(0,0,0,.05); }
.card.system { border-left: 3px solid var(--system, #7c3aed); }
.card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.c-no { display: inline-flex; min-width: 24px; height: 24px; border-radius: 50%; background: var(--accent); color: #fff; align-items: center; justify-content: center; font-size: 12.5px; font-weight: 700; flex-shrink: 0; }
.card.system .c-no { background: var(--system, #7c3aed); }
.c-title { font-size: 13.5px; font-weight: 600; line-height: 1.4; }
.c-todo { margin-left: auto; font-size: 11px; background: var(--warn-soft, #fef3c7); color: var(--warn, #b45309); border-radius: 4px; padding: 1px 8px; flex-shrink: 0; }
.c-link { display: inline-flex; gap: 6px; margin-left: auto; flex-shrink: 0; }
.c-link + .c-todo { margin-left: 0; }
.link-chip { border: 1px solid #7c3aed; background: #ede9fe; color: #7c3aed; border-radius: 999px; padding: 1px 10px; font-size: 11.5px; cursor: pointer; }
.link-chip:hover { background: #7c3aed; color: #fff; }
.card.flash { animation: flashRing 2.2s ease; }
@keyframes flashRing {
  0%, 60% { box-shadow: 0 0 0 3px var(--accent); border-color: var(--accent); }
  100% { box-shadow: 0 2px 10px rgba(0,0,0,.05); }
}

/* 对象分块 */
.c-actors { display: flex; flex-direction: column; gap: 8px; }
.actor-block { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; background: #fafafa; }
.actor-block.sys { background: #f5f3ff; border-color: #ddd6fe; }
.actor-name { font-size: 13px; font-weight: 700; margin-bottom: 4px; display: flex; align-items: center; gap: 6px; }
.actor-block.sys .actor-name { color: var(--system, #7c3aed); }
.sys-tag { font-size: 10px; font-weight: 600; background: var(--system, #7c3aed); color: #fff; border-radius: 3px; padding: 0 5px; }
.actor-items { margin: 0; padding: 0; list-style: none; }
.actor-items li { font-size: 12.5px; line-height: 1.55; padding: 2px 0; display: flex; align-items: baseline; gap: 6px; }
.k-tag { flex-shrink: 0; font-size: 10.5px; font-weight: 600; border: 1px solid; border-radius: 3px; padding: 0 4px; line-height: 1.5; }

.c-fallback { font-size: 13px; color: var(--text-dim); }
.c-actor { font-weight: 600; }
.c-actor.sys { color: var(--system, #7c3aed); }
</style>
