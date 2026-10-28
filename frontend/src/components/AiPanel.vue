<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import html2canvas from 'html2canvas'

const props = defineProps({
  wsId: Number,
  scenarioCode: String,
  view: String,          // browse | play | tool
  captureEl: Object,     // 截图目标元素（主区）
})
const emit = defineEmits(['applied'])

const configured = ref(false)
const showSettings = ref(false)
const cfg = ref({ provider: 'openai', api_key: '', base_url: '', model: '' })

const PROVIDER_PRESETS = {
  'claude-cli': { label: 'Claude Code CLI（本机 agent，模型由 CLI 配置）', url: '', model: '', keyHint: '无需 API key（用本机 claude CLI）' },
  openai: { label: 'OpenAI 兼容（Kimi / DeepSeek / 通义…）', url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k', keyHint: 'sk-...（Moonshot/Kimi 控制台获取）' },
  anthropic: { label: 'Claude（Anthropic API）', url: '', model: 'claude-sonnet-4-5', keyHint: 'sk-ant-...' },
}
const sessionId = ref('')   // claude-cli 多轮续接
const messages = ref([])   // {role, content} 历史（发给后端的全量）
const turns = ref([])      // 展示用 [{who:'user'|'ai', text, image?, changes?:[{...}], applied?:{idx:bool}}]
const input = ref('')
const selection = ref('')
const screenshot = ref('') // dataURL
const sending = ref(false)
const error = ref('')
const logEl = ref(null)

async function api(url, opts) {
  const res = await fetch(url, opts)
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

async function loadConfig() {
  const c = await api('/api/ai/config')
  configured.value = c.configured
  cfg.value.provider = c.provider
  cfg.value.model = c.model
  cfg.value.base_url = c.base_url
  if (!c.configured) showSettings.value = true
}

function onProviderChange() {
  const p = PROVIDER_PRESETS[cfg.value.provider]
  if (p) { cfg.value.base_url = p.url; cfg.value.model = p.model }
}

async function saveConfig() {
  await api('/api/ai/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg.value) })
  configured.value = true
  showSettings.value = false
  cfg.value.api_key = ''  // 不回显
}

function setSelection(text) {
  selection.value = text
}

// 供父组件预填一条待发送的指令（如「生成需求文本」），用户审阅后自己点发送
function prefill(text) {
  input.value = text
}

async function capture() {
  const el = props.captureEl || document.querySelector('.main')
  if (!el) return
  const canvas = await html2canvas(el, { scale: 1, useCORS: true })
  screenshot.value = canvas.toDataURL('image/png')
}

function clearContext() {
  selection.value = ''
  screenshot.value = ''
}

async function scrollBottom() {
  await nextTick()
  if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  sending.value = true
  error.value = ''
  const sel = selection.value
  const img = screenshot.value
  turns.value.push({ who: 'user', text, selection: sel, image: img })
  input.value = ''
  clearContext()
  await scrollBottom()
  try {
    const history = messages.value.concat([{ role: 'user', content: text }])
    const res = await api('/api/ai/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ws_id: props.wsId, scenario_code: props.scenarioCode || '', view: props.view || '',
        selection: sel, messages: [{ role: 'user', content: text }],  // 后端只取最后一条问题；历史走 messages
        image: img, session_id: sessionId.value,
      }),
    })
    if (res.session_id) sessionId.value = res.session_id
    messages.value = history.concat([{ role: 'assistant', content: res.reply }])
    const { body, issues } = parseIssues(res.reply)
    turns.value.push({ who: 'ai', text: body, issues, changes: res.changes || [], applied: {} })
  } catch (e) {
    error.value = e.message
  }
  sending.value = false
  await scrollBottom()
}

async function apply(turn, idx) {
  // 状态机：undefined=待确认 pending=应用中 ok=已落库 err=失败（可重试） false=已忽略
  turn.applied[idx] = 'pending'
  try {
    await api('/api/ai/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ws_id: props.wsId, change: turn.changes[idx] }) })
    turn.applied[idx] = 'ok'
    emit('applied', turn.changes[idx])
  } catch (e) {
    turn.applied[idx] = 'err'
    turn.errors = { ...(turn.errors || {}), [idx]: e.message }
    error.value = e.message
  }
}

function ignore(turn, idx) {
  turn.applied[idx] = false
}

// 从回复文本中拆出 ```issue 块（AI 上报给开发助手的排查交接单）
const ISSUE_RE = /```issue\s*\n([\s\S]*?)```/g
function parseIssues(text) {
  const issues = []
  const body = text.replace(ISSUE_RE, (_, raw) => {
    try { issues.push(JSON.parse(raw)) } catch { issues.push({ title: '（格式异常）', phenomenon: raw.trim() }) }
    return ''
  }).trim()
  return { body, issues }
}

async function copyIssue(iss, ev) {
  const text = `【平台 AI 助手上报的问题】\n标题：${iss.title || ''}\n现象：${iss.phenomenon || ''}\n期望：${iss.expected || ''}\n上下文：${iss.context || ''}\n初步怀疑（未验证）：${iss.suspect || '无'}\n\n请排查。平台代码在 scenario-platform/，项目库 data/projects/ws-1.db。`
  await navigator.clipboard.writeText(text)
  const btn = ev.target
  btn.textContent = '✔ 已复制'
  setTimeout(() => { btn.textContent = '📋 复制给开发助手' }, 1500)
}

function preview(change) {
  return JSON.stringify(change.data_patch || change.spec || {}, null, 2)
}

async function clearChat() {
  if (!turns.value.length) return
  if (!confirm('清空当前对话记录？（AI 侧的会话上下文和已持久化的历史都会一并丢弃，从头开始）')) return
  try {
    await api(`/api/ai/history?ws_id=${props.wsId}`, { method: 'DELETE' })
  } catch (e) {
    // 清库失败也继续清前端，下次刷新再兜底
    console.warn('chat history clear failed:', e)
  }
  turns.value = []
  messages.value = []
  sessionId.value = ''
  error.value = ''
  clearContext()
}

// 面板用 v-show 常驻，对话记录不会因关闭面板而丢；仅在切换项目时隔离清空（各项目记录独立）。
// 手动清空走 clearChat()。
loadConfig()
loadHistory()

async function loadHistory() {
  try {
    const res = await api(`/api/ai/history?ws_id=${props.wsId}`)
    const msgs = res.messages || []
    if (!msgs.length) return
    // 重建展示 turns：user 行和 assistant 行一一对应；changes 落在 assistant 上
    for (const m of msgs) {
      if (m.role === 'user') {
        turns.value.push({ who: 'user', text: m.content, selection: m.selection || '', image: m.image || '' })
      } else {
        const { body, issues } = parseIssues(m.content)
        turns.value.push({ who: 'ai', text: body, issues, changes: m.changes || [], applied: {} })
      }
    }
    // 恢复 claude-cli 多轮续接（取最后一条非空 session_id）
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].session_id) { sessionId.value = msgs[i].session_id; break }
    }
    needInitialScroll.value = true
    doInitialScroll()
  } catch (e) {
    // 历史加载失败不阻塞使用
    console.warn('chat history load failed:', e)
  }
}

// 历史加载后要滚到底，但面板可能是 v-show 隐藏或布局未稳（scrollHeight=0），
// 等面板可见且有内容高度时再滚；showAi 变 true 时也会触发一次兜底。
const needInitialScroll = ref(false)

function doInitialScroll(attempt = 0) {
  if (!needInitialScroll.value) return
  const el = logEl.value
  // el 为 null（configured 未就绪、DOM 未渲染）或高度为 0（v-show 隐藏）：继续等，不计入尝试次数
  if (!el || el.clientHeight === 0) { setTimeout(() => doInitialScroll(attempt), 50); return }
  if (attempt > 60) { needInitialScroll.value = false; return }
  if (el.scrollHeight > el.clientHeight + 4) {
    el.scrollTop = el.scrollHeight
    needInitialScroll.value = false
    return
  }
  setTimeout(() => doInitialScroll(attempt + 1), 50)
}

// 图片加载撑开高度后，若还在等初始滚动则重试，否则已贴近底部就跟随
function onImgLoad() {
  if (needInitialScroll.value) doInitialScroll()
  else scrollBottom()
}

// 暴露给父组件：面板被切到可见时调用，兜底滚到底
function onShown() {
  if (!needInitialScroll.value && turns.value.length) needInitialScroll.value = true
  doInitialScroll()
}

defineExpose({ setSelection, capture, prefill, onShown })
</script>

<template>
  <div class="ai-panel">
    <div class="ai-head">
      <span class="ai-title">✦ AI 助手</span>
      <span class="ai-head-actions">
        <button class="link" title="清空对话记录（含 AI 侧上下文）" @click="clearChat">🗑 清空</button>
        <button class="link" title="设置" @click="showSettings = !showSettings">⚙</button>
      </span>
    </div>

    <div v-if="showSettings" class="settings">
      <label>服务商
        <select v-model="cfg.provider" @change="onProviderChange">
          <option v-for="(p, k) in PROVIDER_PRESETS" :key="k" :value="k">{{ p.label }}</option>
        </select>
      </label>
      <label v-if="cfg.provider !== 'claude-cli'">API Key<input v-model="cfg.api_key" type="password" :placeholder="PROVIDER_PRESETS[cfg.provider].keyHint" /></label>
      <label v-if="cfg.provider === 'openai'">Base URL<input v-model="cfg.base_url" placeholder="https://api.moonshot.cn/v1" /></label>
      <label v-if="cfg.provider !== 'claude-cli'">模型<input v-model="cfg.model" placeholder="moonshot-v1-8k" /></label>
      <div v-if="cfg.provider === 'claude-cli'" class="preset-hint">
        调本机 <code>claude</code> CLI 子进程，模型/密钥由 CLI 自身配置（如已通过 <code>ANTHROPIC_BASE_URL</code> 指向 Kimi K2）。无需在此填 key。<br>
        可选「模型」框留空则用 CLI 默认；填入则加 <code>--model</code> 强制指定。
      </div>
      <div v-if="cfg.provider === 'claude-cli'">
        <label>模型（可选，留空用 CLI 默认）<input v-model="cfg.model" placeholder="如 kimi-k2-0711-preview" /></label>
      </div>
      <div v-if="cfg.provider === 'openai'" class="preset-hint">Kimi：Base URL 用 <code>https://api.moonshot.cn/v1</code>，模型 <code>moonshot-v1-8k</code> / <code>kimi-k2-0711-preview</code>（视觉）</div>
      <button class="btn primary" @click="saveConfig">保存</button>
    </div>

    <div v-if="!configured && !showSettings" class="tip">未就绪：claude-cli 需本机装 claude；其他模式需配 API key。点 ⚙ 设置。</div>

    <template v-else>
      <div v-if="selection || screenshot" class="ctx">
        <div v-if="selection" class="ctx-item">
          <span class="ctx-label">选中：</span><span class="ctx-text">{{ selection.slice(0, 80) }}{{ selection.length > 80 ? '…' : '' }}</span>
        </div>
        <div v-if="screenshot" class="ctx-item">
          <img :src="screenshot" class="ctx-thumb" />
        </div>
        <button class="link" @click="clearContext">清除上下文</button>
      </div>

      <div ref="logEl" class="log">
        <div v-for="(t, i) in turns" :key="i" :class="['turn', t.who]">
          <img v-if="t.image" :src="t.image" class="turn-img" @load="onImgLoad" />
          <div v-if="t.selection" class="turn-sel">选中：{{ t.selection.slice(0, 100) }}{{ t.selection.length > 100 ? '…' : '' }}</div>
          <div class="turn-text">{{ t.text }}</div>
          <div v-for="(iss, ii) in t.issues" :key="'i'+ii" class="issue-card">
            <div class="issue-head">⚠️ 上报：{{ iss.title }}</div>
            <div class="issue-row" v-if="iss.phenomenon"><b>现象</b>：{{ iss.phenomenon }}</div>
            <div class="issue-row" v-if="iss.expected"><b>期望</b>：{{ iss.expected }}</div>
            <div class="issue-row" v-if="iss.context"><b>上下文</b>：{{ iss.context }}</div>
            <div class="issue-row" v-if="iss.suspect"><b>初步怀疑</b>：{{ iss.suspect }}</div>
            <button class="link" @click="copyIssue(iss, $event)">📋 复制给开发助手</button>
          </div>
          <div v-for="(c, ci) in t.changes" :key="ci" class="change-card" :class="{ invalid: c._invalid }">
            <div class="change-head">
              <span class="change-summary">{{ c._invalid ? '⚠️' : '📝' }} {{ c.summary || `${c.action} ${c.code}` }}</span>
            </div>
            <details v-if="c._invalid && c.raw" class="change-detail"><summary>查看原始内容</summary><pre>{{ c.raw }}</pre></details>
            <details v-else-if="!c._invalid" class="change-detail"><summary>查看内容</summary><pre>{{ preview(c) }}</pre></details>
            <div v-if="!c._invalid" class="change-actions">
              <template v-if="t.applied[ci] === undefined">
                <button class="btn primary sm" @click="apply(t, ci)">✔ 应用</button>
                <button class="btn sm" @click="ignore(t, ci)">✘ 忽略</button>
              </template>
              <span v-else-if="t.applied[ci] === 'pending'" class="st-pending">⏳ 正在落库…</span>
              <span v-else-if="t.applied[ci] === 'ok'" class="applied">✔ 已应用到数据库</span>
              <template v-else-if="t.applied[ci] === 'err'">
                <span class="st-err" :title="t.errors?.[ci]">✘ 落库失败：{{ t.errors?.[ci] }}</span>
                <button class="btn sm" @click="apply(t, ci)">↻ 重试</button>
                <button class="link" @click="ignore(t, ci)">忽略</button>
              </template>
              <span v-else class="ignored">已忽略</span>
            </div>
          </div>
        </div>
        <div v-if="sending" class="turn ai"><div class="turn-text dim">思考中…</div></div>
      </div>

      <div class="composer">
        <button class="btn sm" title="截取当前视图发给 AI" @click="capture">📷</button>
        <textarea v-model="input" rows="2" placeholder="问点什么，或让我改场景/拆解/规格…" @keydown.enter.exact.prevent="send" />
        <button class="btn primary sm" :disabled="sending || !input.trim()" @click="send">发送</button>
      </div>
      <div v-if="error" class="error">{{ error }}</div>
    </template>
  </div>
</template>

<style scoped>
.ai-panel { width: 320px; flex-shrink: 0; border-left: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; min-height: 0; }
.ai-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.ai-head-actions { display: flex; gap: 10px; align-items: center; }
.ai-title { font-weight: 700; }
.settings { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; border-bottom: 1px solid var(--border); }
.settings label { font-size: 12px; color: var(--text-dim); display: flex; flex-direction: column; gap: 3px; }
.settings input, .settings select { border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; font-size: 13px; }
.preset-hint { font-size: 11px; color: var(--text-dim); line-height: 1.5; }
.preset-hint code { background: var(--bg); padding: 0 4px; border-radius: 3px; }
.tip { padding: 16px; color: var(--text-dim); font-size: 13px; }
.ctx { padding: 8px 12px; border-bottom: 1px dashed var(--border); font-size: 12px; }
.ctx-item { display: flex; gap: 6px; align-items: flex-start; margin-bottom: 4px; }
.ctx-label { color: var(--accent); flex-shrink: 0; }
.ctx-text { color: var(--text-dim); word-break: break-all; }
.ctx-thumb { max-width: 100%; max-height: 90px; border: 1px solid var(--border); border-radius: 6px; }
.log { flex: 1; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.turn { max-width: 100%; }
.turn.user .turn-text { background: var(--accent-soft); }
.turn.ai .turn-text { background: var(--bg); }
.turn-text { padding: 8px 10px; border-radius: 8px; font-size: 13px; white-space: pre-wrap; word-break: break-word; }
.turn-text.dim { color: var(--text-dim); }
.turn-img { max-width: 100%; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 4px; }
.turn-sel { font-size: 11px; color: var(--text-dim); margin-bottom: 3px; }
.change-card { border: 1px solid var(--accent); border-radius: 8px; margin-top: 6px; padding: 8px; background: #fff; }
.change-card.invalid { border-color: #d97706; background: #fffbeb; }
.change-card.invalid .change-summary { color: #92400e; }
.change-summary { font-size: 12.5px; font-weight: 600; }
.change-detail pre { max-height: 180px; overflow: auto; font-size: 11px; background: var(--bg); padding: 6px; border-radius: 6px; }
.change-actions { display: flex; gap: 8px; margin-top: 6px; align-items: center; }
.applied { color: #047857; font-size: 12px; }
.issue-card { border: 1px solid #d97706; border-radius: 8px; margin-top: 6px; padding: 8px; background: #fffbeb; font-size: 12px; }
.issue-head { font-weight: 600; color: #92400e; margin-bottom: 4px; }
.issue-row { color: #451a03; line-height: 1.5; margin-bottom: 2px; word-break: break-word; }
.issue-card .link { margin-top: 4px; }
.st-pending { color: var(--accent); font-size: 12px; animation: pulse 1s ease-in-out infinite; }
.st-err { color: #b91c1c; font-size: 12px; }
@keyframes pulse { 50% { opacity: .45; } }
.ignored { color: var(--text-dim); font-size: 12px; }
.composer { display: flex; gap: 6px; padding: 10px 12px; border-top: 1px solid var(--border); align-items: flex-end; }
.composer textarea { flex: 1; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; resize: none; font-family: inherit; }
.btn { padding: 6px 14px; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.sm { padding: 4px 10px; font-size: 13px; }
.link { border: none; background: none; color: var(--accent); cursor: pointer; font-size: 13px; }
.error { color: #b91c1c; font-size: 12px; padding: 4px 12px 8px; }
</style>
