<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import mermaid from 'mermaid'
import { marked } from 'marked'

const props = defineProps({ text: String, only: String })  // only: 'mermaid' | 'text' | 缺省=全部

mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' })

// 把 notes 拆成 文本段 / mermaid 段
// 围栏同时兼容 ```mermaid 和 ~~~mermaid（AI 有时用 ~~~）
const FENCE_RE = /(?:```|~~~)mermaid\s*\n([\s\S]*?)(?:```|~~~)/g

// 紧邻 mermaid 块上方的引导标签行（如「**流程图**（Mermaid）：」）不算正文，
// 拆分文本段时剥掉，避免它单独残留在「备注」区
function stripLabel(text) {
  return text.replace(/(?:^|\n)[ \t]*(?:[*_＊#>-]*[ \t]*)*流程图(?:[*_＊]*[ \t]*)*[（(][^）)]*[）)][ \t]*[:：]?[ \t]*(?=\n?$)/, '')
}

const allSegments = computed(() => {
  if (!props.text) return []
  const parts = []
  const re = new RegExp(FENCE_RE.source, 'g')
  let last = 0, m, i = 0
  while ((m = re.exec(props.text)) !== null) {
    const before = stripLabel(props.text.slice(last, m.index)).trim()
    if (before) parts.push({ type: 'text', content: before, key: `t${i}` })
    parts.push({ type: 'mermaid', content: m[1].trim(), key: `m${i}` })
    last = m.index + m[0].length
    i++
  }
  const rest = stripLabel(props.text.slice(last)).trim()
  if (rest) parts.push({ type: 'text', content: rest, key: `t${i}` })
  return parts
})
// 按 only 过滤；'mermaid' 只留图、'text' 只留文字、缺省全部
const segments = computed(() =>
  props.only ? allSegments.value.filter(s => s.type === props.only) : allSegments.value
)

const rendered = ref({})  // key -> svg

async function renderAll() {
  rendered.value = {}
  for (const seg of segments.value) {
    if (seg.type !== 'mermaid') continue
    try {
      const { svg } = await mermaid.render(`mmd-${seg.key}-${Date.now()}`, seg.content)
      rendered.value = { ...rendered.value, [seg.key]: svg }
    } catch (e) {
      rendered.value = { ...rendered.value, [seg.key]: null }  // 失败回退源码
    }
  }
}

onMounted(renderAll)
watch(() => props.text, renderAll)

// 文本段按 markdown 渲染（**粗体**、- 列表等）。notes 为平台内部数据，非外部不可信输入。
function renderMd(content) {
  return marked.parse(content, { breaks: true })
}
</script>

<template>
  <div class="mermaid-notes">
    <template v-for="seg in segments" :key="seg.key">
      <div v-if="seg.type === 'mermaid'" class="mermaid-block">
        <div v-if="rendered[seg.key]" v-html="rendered[seg.key]" class="svg-wrap"></div>
        <pre v-else-if="rendered[seg.key] === null" class="fallback">{{ seg.content }}</pre>
        <div v-else class="loading">流程图渲染中…</div>
      </div>
      <div v-else class="plain md" v-html="renderMd(seg.content)"></div>
    </template>
  </div>
</template>

<style scoped>
.mermaid-block { margin: 10px 0; overflow-x: auto; }
.svg-wrap :deep(svg) { max-width: 100%; height: auto; }
.fallback, .plain { white-space: pre-wrap; background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; font-size: 12.5px; }
/* markdown 渲染段：交给 marked 处理换行/段落，不再用 pre 的 pre-wrap */
.plain.md { white-space: normal; line-height: 1.7; }
.plain.md :deep(p) { margin: 0 0 10px; }
.plain.md :deep(p:last-child) { margin-bottom: 0; }
.plain.md :deep(ul), .plain.md :deep(ol) { margin: 4px 0 10px; padding-left: 22px; }
.plain.md :deep(code) { background: var(--bg); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.loading { color: var(--text-dim); font-size: 12px; padding: 8px; }
</style>
