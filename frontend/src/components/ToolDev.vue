<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'

const props = defineProps({ wsId: Number, reloadRev: { type: Number, default: 0 } })
const emit = defineEmits(['open-scenario'])

// 场景状态点颜色（与 App.vue 一致）
const STATUS_CLS = { '草稿': 'st-draft', '已确认': 'st-confirmed', '已开发': 'st-developed', '已验证': 'st-verified' }
function statusClass(s) { return STATUS_CLS[s] || 'st-draft' }
const statusFilter = ref('all')   // all | confirmed | undeveloped

// ---------------- 目标概览 ----------------
const target = ref(null)        // develop/target 返回
const loadErr = ref('')
const checked = ref(new Set())  // 勾选的场景号

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function loadTarget() {
  loadErr.value = ''
  try {
    target.value = await api(`/api/workspaces/${props.wsId}/develop/target`)
    // 默认勾选「已确认且有原型」的场景（只有已确认才能开发）
    if (!checked.value.size) {
      checked.value = new Set(target.value.scenarios.filter(s => s.has_ui_spec && s.status === '已确认').map(s => s.code))
    }
  } catch (e) { loadErr.value = e.message }
}
watch(() => props.wsId, loadTarget, { immediate: true })

const groups = computed(() => {
  if (!target.value) return []
  const m = new Map()
  for (const s of target.value.scenarios) {
    // 状态筛选：confirmed=仅已确认；undeveloped=未开发（草稿/已确认）
    if (statusFilter.value === 'confirmed' && s.status !== '已确认') continue
    if (statusFilter.value === 'undeveloped' && (s.status === '已开发' || s.status === '已验证')) continue
    const k = s.domain || '未分组'
    if (!m.has(k)) m.set(k, [])
    m.get(k).push(s)
  }
  return [...m.entries()].map(([name, items]) => ({ name, items }))
})
const checkedList = computed(() => [...checked.value])
const checkedWithSpec = computed(() =>
  target.value ? checkedList.value.filter(c => {
    const s = target.value.scenarios.find(x => x.code === c)
    return s && s.has_ui_spec && s.status === '已确认'
  }) : [])

function toggle(code) {
  const sc = target.value?.scenarios.find(s => s.code === code)
  if (sc && sc.status !== '已确认') return  // 只有已确认的场景可选
  const s = new Set(checked.value)
  if (s.has(code)) s.delete(code); else s.add(code)
  checked.value = s
}
function toggleGroup(items) {
  const selectable = items.filter(s => s.status === '已确认')
  if (!selectable.length) return
  const all = selectable.every(s => checked.value.has(s.code))
  const s = new Set(checked.value)
  for (const it of selectable) { if (all) s.delete(it.code); else s.add(it.code) }
  checked.value = s
}

// ---------------- 全局约定（只读摘要，编辑在左侧「📜 全局约定」页） ----------------
const globalNotes = ref('')

async function loadGlobalNotes() {
  try {
    const r = await api(`/api/workspaces/${props.wsId}/global-notes`)
    globalNotes.value = r.text || ''
  } catch { /* 静默 */ }
}
watch(() => props.wsId, loadGlobalNotes, { immediate: true })
watch(() => props.reloadRev, loadGlobalNotes)   // AI 应用 update_global_notes 后刷新只读摘要

// 摘要：非空行取前 6 行预览
const gnPreview = computed(() =>
  globalNotes.value.split('\n').map(l => l.trim()).filter(Boolean).slice(0, 6))

// ---------------- 开发任务状态 ----------------
const task = ref({ status: 'idle', log_tail: [], files: [], codes: [] })
const starting = ref(false)
const startErr = ref('')
const reply = ref('')
const devVersion = ref('')       // 本次开发归属的工具版本
const marked = ref(false)        // 本轮 done 后是否已标记"已开发"
const logEl = ref(null)
let pollTimer = null

const busy = computed(() => task.value.status === 'running' || task.value.status === 'waiting_input')
const canStart = computed(() => checkedWithSpec.value.length > 0 && !busy.value && target.value?.ai_ready)

async function poll() {
  const prevStatus = task.value.status
  try {
    task.value = await api(`/api/workspaces/${props.wsId}/develop/status`)
    if (busy.value) scrollLog()
    // 任务刚转入 done：把本次场景标为「已开发」并记录版本（幂等，只标一次）
    if (task.value.status === 'done' && prevStatus !== 'done' && !marked.value && task.value.codes?.length) {
      marked.value = true
      await api(`/api/workspaces/${props.wsId}/develop/mark-developed`, { method: 'POST' })
      loadTarget()  // 刷新状态点
    }
  } catch { /* 服务未就绪时静默 */ }
}

function scrollLog() {
  nextTick(() => { if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight })
}

async function start() {
  starting.value = true
  startErr.value = ''
  marked.value = false   // 新一轮开发，允许完成后重新标记
  try {
    await api(`/api/workspaces/${props.wsId}/develop`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ codes: checkedWithSpec.value, version: devVersion.value.trim() }),
    })
    await poll()
    scrollLog()
  } catch (e) { startErr.value = e.message }
  starting.value = false
}

async function sendReply() {
  const text = reply.value.trim()
  if (!text) return
  try {
    await api(`/api/workspaces/${props.wsId}/develop/input`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    reply.value = ''
    await poll()
  } catch (e) { startErr.value = e.message }
}

async function stop() {
  if (!confirm('确定终止当前开发进程？已写入的文件会保留。')) return
  await api(`/api/workspaces/${props.wsId}/develop/stop`, { method: 'POST' })
  await poll()
}

// error（网络/进程中断）后续开发：重启 claude 进程从中断处继续，已完成文件保留
const resuming = ref(false)
async function resume() {
  resuming.value = true
  startErr.value = ''
  try {
    await api(`/api/workspaces/${props.wsId}/develop/resume`, { method: 'POST' })
    await poll()
    scrollLog()
  } catch (e) { startErr.value = e.message }
  resuming.value = false
}

// ---------------- 反馈修正（done/error 后把问题发给开发 agent） ----------------
const fbText = ref('')
const fbIncludeFindings = ref(true)
const fbSending = ref(false)
const canFeedback = computed(() => ['done', 'error'].includes(task.value.status) && task.value.codes?.length && !busy.value)

async function sendFeedback() {
  fbSending.value = true
  startErr.value = ''
  try {
    const r = await api(`/api/workspaces/${props.wsId}/develop/feedback`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: fbText.value, include_findings: fbIncludeFindings.value }),
    })
    fbText.value = ''
    marked.value = false   // 反馈修正完成后允许重新标记已开发
    if (r.findings_fed) startErr.value = ''
    await poll()
    scrollLog()
  } catch (e) { startErr.value = e.message }
  fbSending.value = false
}

onMounted(() => { poll(); pollTimer = setInterval(poll, 2000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })

const STATUS = {
  idle: { label: '未开始', cls: '' },
  running: { label: '开发中…', cls: 'running' },
  waiting_input: { label: '待你确认', cls: 'waiting' },
  done: { label: '本轮完成', cls: 'done' },
  error: { label: '出错', cls: 'error' },
}
const statusInfo = computed(() => STATUS[task.value.status] || STATUS.idle)
</script>

<template>
  <div class="dev">
    <div v-if="loadErr" class="placeholder error">{{ loadErr }}</div>
    <div v-else-if="!target" class="placeholder">加载中…</div>

    <template v-else>
      <!-- 头部：工具信息 + 状态 -->
      <div class="dev-head">
        <div>
          <div class="dev-title">🛠 开发真正的工具：{{ target.tool_name }}</div>
          <div class="dev-sub">
            产物目录 <code>{{ target.work_dir }}</code>
            <span v-if="target.exists" class="dim">（已有 {{ target.files.length }} 个文件，将在其基础上增量开发）</span>
            <span v-else class="dim">（全新开发）</span>
          </div>
        </div>
        <span class="spacer"></span>
        <span class="status-badge" :class="statusInfo.cls">{{ statusInfo.label }}</span>
      </div>

      <div v-if="!target.ai_ready" class="warn-bar">AI 未就绪：后台开发依赖本机 claude CLI，请先在「✦ AI」设置里确认 provider 可用。</div>

      <!-- 全局约定只读摘要：编辑在左侧「📜 全局约定」页；开发时会全量注入 prompt -->
      <details class="gn">
        <summary>
          📜 全局约定
          <span class="dim">{{ globalNotes ? `（已配置，开发时将作为最高优先级约束注入；到左侧「📜 全局约定」页编辑）` : '（未配置——建议先到左侧「📜 全局约定」页维护跨场景规则）' }}</span>
        </summary>
        <div v-if="globalNotes" class="gn-preview">
          <div v-for="(l, i) in gnPreview" :key="i" class="gn-line">{{ l }}</div>
          <div class="dim">…（完整内容见左侧「📜 全局约定」页）</div>
        </div>
      </details>

      <div class="dev-body">
        <!-- 左：场景勾选 -->
        <aside class="picker">
          <div class="picker-title">
            选择要实现的场景 <span class="dim">（须已确认且有原型）</span>
            <select v-model="statusFilter" class="status-filter" title="按状态筛选">
              <option value="all">全部</option>
              <option value="confirmed">仅已确认</option>
              <option value="undeveloped">未开发</option>
            </select>
          </div>
          <div v-for="g in groups" :key="g.name" class="pgroup">
            <div class="pgroup-name" @click="toggleGroup(g.items)">
              <input type="checkbox" :checked="g.items.every(s => checked.has(s.code))" @click.stop="toggleGroup(g.items)" />
              {{ g.name }}
            </div>
            <label v-for="s in g.items" :key="s.code" class="pitem"
                   :class="{ nospec: !s.has_ui_spec, locked: s.status !== '已确认' }"
                   :title="s.status !== '已确认' ? `当前状态「${s.status}」，请先在场景详情页确认为「已确认」` : ''">
              <input type="checkbox" :checked="checked.has(s.code)" :disabled="busy || s.status !== '已确认'" @change="toggle(s.code)" />
              <span class="pcode">{{ s.code }}</span>
              <span class="status-dot" :class="statusClass(s.status)" :title="`状态：${s.status}${s.version ? ' · ' + s.version : ''}`"></span>
              <span class="ptitle">{{ s.title }}</span>
              <span v-if="s.has_ui_spec" class="ptag has" title="已有工具原型">原型</span>
              <span v-else class="ptag" title="还没有工具原型，开发时只会参考场景文档">无原型</span>
            </label>
          </div>
          <div class="picker-foot">
            <div class="dim">已选 {{ checkedList.length }} 个（{{ checkedWithSpec.length }} 个有原型）</div>
            <input v-model="devVersion" class="version-input" placeholder="本次版本（如 v0.2.0，可选）" title="开发成功后标到场景上，便于按版本迭代" :disabled="busy" />
            <button class="btn primary" :disabled="!canStart || starting" @click="start">
              {{ starting ? '启动中…' : (target.exists ? '🚀 增量开发' : '🚀 开始开发') }}
            </button>
            <div v-if="checkedList.length && !checkedWithSpec.length" class="err-line">所选场景都没有工具原型，无法开发</div>
            <div v-if="startErr" class="err-line">{{ startErr }}</div>
          </div>
        </aside>

        <!-- 右：日志 + 文件 + 待确认 -->
        <section class="panel">
          <div class="panel-bar">
            <b>开发日志</b>
            <span class="dim">{{ task.codes.length ? '场景：' + task.codes.join('、') : '' }}</span>
            <span class="spacer"></span>
            <button v-if="busy" class="btn sm danger" @click="stop">■ 终止</button>
          </div>

          <div ref="logEl" class="log">
            <div v-if="!task.log_tail.length" class="dim log-empty">
              {{ task.status === 'idle' ? '勾选场景后点「开始开发」，claude 会在产物目录里自主写代码。' : '暂无日志' }}
            </div>
            <pre v-for="(l, i) in task.log_tail" :key="i" class="log-line">{{ l }}</pre>
          </div>

          <!-- 待确认 -->
          <div v-if="task.status === 'waiting_input'" class="needs-input">
            <div class="ni-q">❓ {{ task.question }}</div>
            <div class="ni-ops">
              <input v-model="reply" placeholder="输入你的决定，回车发送，开发将继续" @keydown.enter="sendReply" />
              <button class="btn primary sm" @click="sendReply">回复并继续</button>
            </div>
          </div>
          <div v-else-if="task.status === 'error'" class="err-bar">
            ❌ {{ task.error }}
            <div class="err-ops">
              <button class="btn primary sm" :disabled="resuming" @click="resume">{{ resuming ? '续跑中…' : '↻ 继续（从中断处接着干）' }}</button>
              <span class="dim">进程因网络/中断退出；已写入工具目录的文件保留，续跑时它会检查现状接着做，不从头重写。</span>
            </div>
          </div>
          <div v-else-if="task.status === 'done'" class="done-bar">
            ✅ 本轮开发完成。可继续勾选场景做增量开发，或到 <code>{{ task.work_dir }}</code> 查看产物。
          </div>

          <!-- 反馈修正：done/error 后人话描述问题，发给开发 agent 修 -->
          <div v-if="canFeedback" class="feedback">
            <div class="fb-title">💬 反馈修正 <span class="dim">测试/试用发现与预期不一致？人话描述，开发 agent 会修正并更新对照清单</span></div>
            <textarea v-model="fbText" rows="3" placeholder="例：「进入工作空间」恢复上下文后没有重校验凭据；概览页仓库列表应是卡片不是表格…"></textarea>
            <div class="fb-ops">
              <label class="fb-chk"><input type="checkbox" v-model="fbIncludeFindings" /> 附带所有「待反馈」的实测记录（场景页记录的）</label>
              <span class="spacer"></span>
              <button class="btn primary sm" :disabled="fbSending || (!fbText.trim() && !fbIncludeFindings)" @click="sendFeedback">{{ fbSending ? '发送中…' : '发送给开发 agent 修正' }}</button>
            </div>
          </div>

          <!-- 产物文件 -->
          <div v-if="task.files.length" class="files">
            <div class="files-title">产物文件（{{ task.files.length }}）</div>
            <div class="files-list">
              <div v-for="f in task.files" :key="f" class="file-item">📄 {{ f }}</div>
            </div>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dev { max-width: 1300px; }
.placeholder { color: var(--text-dim); padding: 40px; text-align: center; }
.error { color: #b91c1c; }
.spacer { flex: 1; }

.dev-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.dev-title { font-size: 17px; font-weight: 700; }
.dev-sub { font-size: 12.5px; color: var(--text-dim); margin-top: 2px; }
.dev-sub code { background: var(--panel); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.status-badge { font-size: 12px; padding: 3px 12px; border-radius: 999px; background: var(--panel); color: var(--text-dim); font-weight: 600; }
.status-badge.running { background: #dbeafe; color: #1d4ed8; }
.status-badge.waiting { background: #fef3c7; color: #b45309; }
.status-badge.done { background: #d1fae5; color: #047857; }
.status-badge.error { background: #fee2e2; color: #b91c1c; }

.warn-bar { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; border-radius: 8px; padding: 8px 14px; font-size: 13px; margin-bottom: 12px; }

/* 全局约定（只读摘要） */
.gn { border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 10px 14px; margin-bottom: 12px; }
.gn summary { font-size: 13px; font-weight: 600; cursor: pointer; user-select: none; }
.gn summary .dim { font-weight: 400; font-size: 12px; margin-left: 6px; }
.gn-preview { margin-top: 8px; font-size: 12.5px; color: var(--text); line-height: 1.7; }
.gn-line { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.dev-body { display: flex; gap: 14px; align-items: flex-start; }

/* 场景勾选 */
.picker { width: 340px; flex-shrink: 0; border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 12px 14px; max-height: calc(100vh - 180px); overflow-y: auto; }
.picker-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.picker-title .dim { font-weight: 400; font-size: 12px; }
.pgroup { margin-bottom: 6px; }
.pgroup-name { font-size: 12px; font-weight: 600; color: var(--text-dim); padding: 4px 0; cursor: pointer; display: flex; align-items: center; gap: 6px; }
.pitem { display: flex; align-items: center; gap: 6px; padding: 3px 0 3px 18px; font-size: 13px; cursor: pointer; border-radius: 4px; }
.pitem:hover { background: var(--bg); }
.pitem.nospec { opacity: .65; }
.pitem.locked { opacity: .55; cursor: not-allowed; }
.pcode { font-weight: 600; min-width: 34px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.status-dot.st-draft { background: #cbd5e1; }
.status-dot.st-confirmed { background: #3b82f6; }
.status-dot.st-developed { background: #a855f7; }
.status-dot.st-verified { background: #10b981; }
.status-filter { margin-left: auto; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; padding: 2px 6px; }
.version-input { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 13px; box-sizing: border-box; }
.ptitle { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ptag { font-size: 11px; padding: 0 6px; border-radius: 4px; background: var(--panel); color: var(--text-dim); }
.ptag.has { background: #ede9fe; color: #7c3aed; }
.picker-foot { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--border); display: flex; flex-direction: column; gap: 8px; }
.picker-foot .dim { font-size: 12px; }
.err-line { font-size: 12px; color: #b91c1c; }

/* 面板 */
.panel { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.panel-bar { display: flex; align-items: center; gap: 10px; }
.panel-bar .dim { font-size: 12px; }
.log { border: 1px solid var(--border); border-radius: 10px; background: #0f172a; color: #e2e8f0; padding: 12px 14px; height: calc(100vh - 330px); min-height: 280px; overflow-y: auto; font-family: Consolas, monospace; font-size: 12.5px; }
.log-empty { color: #64748b; padding: 20px; text-align: center; font-family: inherit; }
.log-line { margin: 0 0 4px; white-space: pre-wrap; word-break: break-word; }

.needs-input { border: 1px solid #fde68a; background: #fffbeb; border-radius: 10px; padding: 12px 14px; }
.ni-q { font-size: 14px; font-weight: 600; color: #92400e; margin-bottom: 8px; }
.ni-ops { display: flex; gap: 8px; }
.ni-ops input { flex: 1; border: 1px solid var(--border); border-radius: 6px; padding: 7px 10px; font-size: 13.5px; }
.err-bar { border: 1px solid #fecaca; background: #fef2f2; color: #b91c1c; border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.err-ops { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.err-ops .dim { font-size: 12px; color: #7f1d1d; }
.done-bar { border: 1px solid #a7f3d0; background: #ecfdf5; color: #047857; border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.done-bar code { background: #fff; padding: 1px 6px; border-radius: 4px; }

/* 反馈修正 */
.feedback { border: 1px solid #bfdbfe; background: #eff6ff; border-radius: 10px; padding: 10px 14px; }
.fb-title { font-size: 13px; font-weight: 600; color: #1e40af; margin-bottom: 8px; }
.fb-title .dim { font-weight: 400; font-size: 12px; margin-left: 6px; }
.feedback textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 13px; font-family: inherit; line-height: 1.6; resize: vertical; }
.fb-ops { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.fb-chk { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--text-dim); cursor: pointer; }

.files { border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 10px 14px; }
.files-title { font-size: 12px; font-weight: 600; color: var(--text-dim); margin-bottom: 6px; }
.files-list { display: flex; flex-wrap: wrap; gap: 4px 14px; max-height: 120px; overflow-y: auto; }
.file-item { font-size: 12.5px; font-family: Consolas, monospace; color: var(--text); }

.btn { padding: 6px 14px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.sm { padding: 4px 10px; font-size: 12.5px; }
.btn.danger { color: #dc2626; }
.btn:disabled { opacity: .4; cursor: default; }
</style>
