<script setup>
const props = defineProps({ workspaces: Array })
const emit = defineEmits(['open', 'create', 'delete'])

function fmtDate(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
</script>

<template>
  <div class="home">
    <div class="home-head">
      <h1>项目</h1>
      <span class="dim">项目 = 工作域：一套独立的角色库、场景树、流程原型与工具原型</span>
    </div>

    <div class="grid">
      <div v-for="w in workspaces" :key="w.id" class="card" @click="emit('open', w.id)">
        <div class="card-title">{{ w.name }}</div>
        <div class="card-desc">{{ w.description || '（无描述）' }}</div>
        <div class="card-stats">
          <span class="stat">👥 {{ w.role_count }} 角色</span>
          <span class="stat">🎬 {{ w.scenario_count }} 场景</span>
        </div>
        <div class="card-meta">
          <span v-if="w.source_path" class="src" :title="w.source_path">📄 {{ w.source_path.split(/[\\/]/).pop() }}</span>
          <span v-else class="src empty">空项目（未绑定文档）</span>
          <span class="date">{{ fmtDate(w.created_at) }}</span>
        </div>
        <button class="del" title="删除项目（含角色、场景、界面规格）" @click.stop="emit('delete', w)">🗑</button>
      </div>

      <div class="card add" @click="emit('create')">
        <div class="add-icon">＋</div>
        <div>新建项目</div>
        <div class="add-hint">导入文档 / 手动添加 / 从模板创建</div>
      </div>
    </div>

    <div v-if="!workspaces.length" class="empty-tip">还没有项目，点「＋ 新建项目」开始。</div>
  </div>
</template>

<style scoped>
.home { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }
.home-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 20px; }
.home-head h1 { margin: 0; font-size: 22px; }
.dim { color: var(--text-dim); font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.card { position: relative; border: 1px solid var(--border); border-radius: 12px; background: #fff; padding: 16px 18px; cursor: pointer; transition: box-shadow .15s, border-color .15s; }
.card:hover { border-color: var(--accent); box-shadow: 0 6px 20px rgba(37, 99, 235, .10); }
.card-title { font-size: 16px; font-weight: 700; margin-bottom: 6px; padding-right: 28px; }
.card-desc { font-size: 13px; color: var(--text-dim); min-height: 34px; margin-bottom: 10px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.card-stats { display: flex; gap: 14px; margin-bottom: 10px; }
.stat { font-size: 13px; }
.card-meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--text-dim); border-top: 1px dashed var(--border); padding-top: 8px; }
.src { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.src.empty { font-style: italic; }
.del { position: absolute; top: 10px; right: 10px; border: none; background: transparent; cursor: pointer; font-size: 14px; opacity: .35; padding: 4px; }
.del:hover { opacity: 1; }
.card.add { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; min-height: 150px; border-style: dashed; color: var(--accent); font-weight: 600; }
.add-icon { font-size: 28px; line-height: 1; }
.add-hint { font-size: 12px; color: var(--text-dim); font-weight: 400; }
.empty-tip { text-align: center; color: var(--text-dim); margin-top: 30px; }
</style>
