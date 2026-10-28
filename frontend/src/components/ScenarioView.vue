<script setup>
import { ref, watch, nextTick, computed } from 'vue'
import MermaidBlock from './MermaidBlock.vue'
import { useSysLabel } from '../composables/sysLabel.js'

const props = defineProps({ scenario: Object, focus: Object })
const emit = defineEmits(['focus-step', 'send-to-ai'])

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
      <span v-if="scenario.data.main_users?.length"><b>主要用户：</b>{{ scenario.data.main_users.join('、') }}</span>
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

    <section v-if="scenario.data.steps?.length">
      <h3>主流程（{{ scenario.data.steps.length }} 步）</h3>
      <table class="steps">
        <thead><tr><th class="no">步骤</th><th class="actor">角色</th><th>用户操作</th><th>系统响应</th></tr></thead>
        <tbody>
          <tr v-for="s in scenario.data.steps" :key="s.no" class="step-row" :data-no="s.no" :class="{ system: isSystem(s), flash: flashNo === s.no }" :title="`左键单击弹出操作菜单，右键单击跳到流程原型步骤 ${s.no}`" @click="onRowClick($event, s)" @contextmenu.prevent.stop="emit('focus-step', s.no)">
            <td class="no">{{ s.no }}</td>
            <td class="actor">{{ displayActor(s.actor) }}</td>
            <td>{{ isSystem(s) ? '' : s.action }}</td>
            <td>{{ s.response }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-for="v in scenario.data.variants" :key="v.name">
      <h3>{{ v.name }}</h3>
      <table v-if="v.steps?.length" class="steps">
        <thead><tr><th class="no">步骤</th><th class="actor">角色</th><th>用户操作</th><th>系统响应</th></tr></thead>
        <tbody>
          <tr v-for="s in v.steps" :key="s.no" class="step-row" :data-no="s.no" :class="{ system: isSystem(s), flash: flashNo === s.no }" :title="`左键单击弹出操作菜单，右键单击跳到流程原型步骤 ${s.no}`" @click="onRowClick($event, s)" @contextmenu.prevent="emit('focus-step', s.no)">
            <td class="no">{{ s.no }}</td>
            <td class="actor">{{ displayActor(s.actor) }}</td>
            <td>{{ isSystem(s) ? '' : s.action }}</td>
            <td>{{ s.response }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="dim">（该小节为说明性内容，无步骤表）</p>
    </section>

    <section v-if="scenario.data.ui_material?.length">
      <h3>关键界面 / 交互（界面素材）</h3>
      <ul class="ui-material">
        <li v-for="(t, i) in scenario.data.ui_material" :key="i" :style="{ paddingLeft: indentOf(t) + 'px' }">{{ t.replace(/^\s*[-*+]\s*/, '') }}</li>
      </ul>
    </section>

    <section v-if="scenario.data.acceptance?.length">
      <h3>验收标准（{{ scenario.data.acceptance.length }}）</h3>
      <ul class="accept"><li v-for="(t, i) in scenario.data.acceptance" :key="i">{{ t.replace(/^\[ \]\s*/, '') }}</li></ul>
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

    <!-- 步骤行操作菜单 -->
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
.pending.open .kind { background: var(--warn-soft); color: var(--warn); }
.pending.done { color: var(--text-dim); }
.pending.done .kind { background: #e5e7eb; color: var(--text-dim); }
.notes { margin-top: 20px; }
.notes h3 { margin: 0 0 8px; font-size: 15px; }
.notes :deep(.plain) { max-height: none; }
.dim { color: var(--text-dim); font-size: 13px; }
</style>
