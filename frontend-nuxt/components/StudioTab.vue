<script setup lang="ts">
// 🎨 Studio: turn a spreading narrative into a creator brief, grounded
// exclusively on published fact-checks (hard rule #3). Also home of the
// add-narrative form — the watchlist is curated here, Radar only observes.
import type { Brief, EvalCategory, Narrative } from '~/types/api'

const { api } = useApi()
const { guard, lang } = useAppState()

const radar = ref<Narrative[]>([])
const brief = ref<Brief | null>(null)
const showAdd = ref(false)
const addMsg = ref('')
const draft = ref({
  code: '', label_de: '', label_en: '', description_en: '',
  category: 'false_information' as EvalCategory,
})

async function load() {
  await guard(async () => {
    radar.value = await api<Narrative[]>('/radar')
  })
}
onMounted(load)

async function makeBrief(code: string) {
  await guard(async () => {
    brief.value = await api<Brief>('/briefs/' + code, { method: 'POST' })
  })
}

async function addNarrative() {
  addMsg.value = ''
  const d = draft.value
  if (!d.code || !d.label_de || !d.label_en || !d.description_en) {
    addMsg.value = 'Please fill all fields (code: lowercase_with_underscores).'
    return
  }
  await guard(async () => {
    await api('/narratives', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(d),
    })
    addMsg.value = `Narrative '${d.code}' added.`
    draft.value = { code: '', label_de: '', label_en: '', description_en: '', category: 'false_information' }
    showAdd.value = false
    await load()
  })
}
</script>

<template>
  <div>
    <div class="card">
      <div class="row">
        <h2>🎨 Creator Studio — briefs</h2>
        <button class="btn btn-ghost" style="margin-left:auto" @click="showAdd = !showAdd">
          {{ showAdd ? 'Cancel' : '+ Add narrative' }}
        </button>
      </div>
      <p class="hint">Turn a spreading narrative into a ready-to-use brief for a content creator.</p>

      <div v-if="showAdd" class="item" style="margin-bottom:14px">
        <p class="meta" style="background:#fff7e6;border:1px solid #f0d9a8;border-radius:8px;padding:8px 10px;margin:0 0 10px">
          🚧 <b>Under construction</b> — adding a narrative already works, but the rest of the
          curation workflow (review, editing, deletion) isn't built yet.
        </p>
        <h3 style="margin-top:0">Add a narrative to the watchlist</h3>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <input v-model="draft.code" placeholder="code (e.g. medien_luegen)" style="flex:1;min-width:180px;padding:8px;border:1.5px solid var(--line);border-radius:8px">
          <select v-model="draft.category" style="padding:8px;border:1.5px solid var(--line);border-radius:8px">
            <option v-for="s in EVAL_SECTIONS" :key="s.key" :value="s.key">{{ s.icon }} {{ s.title }}</option>
          </select>
        </div>
        <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap">
          <input v-model="draft.label_de" placeholder="Label (Deutsch)" style="flex:1;min-width:180px;padding:8px;border:1.5px solid var(--line);border-radius:8px">
          <input v-model="draft.label_en" placeholder="Label (English)" style="flex:1;min-width:180px;padding:8px;border:1.5px solid var(--line);border-radius:8px">
        </div>
        <textarea v-model="draft.description_en" placeholder="Plain-language description: what does this narrative claim / support, and why does it matter?" style="min-height:60px;margin-top:8px" />
        <button class="btn btn-teal" style="margin-top:8px" @click="addNarrative">Add narrative</button>
        <span v-if="addMsg" class="meta"> {{ addMsg }}</span>
      </div>

      <div class="row">
        <button v-for="n in radar" :key="n.code" class="btn btn-ghost" @click="makeBrief(n.code)">
          {{ lang === 'de' ? n.label_de : n.label_en }}</button>
      </div>
      <div v-if="!radar.length" class="empty">Run the pipeline first to see active narratives.</div>
    </div>
    <div v-if="brief" class="card">
      <h3 style="margin-bottom:8px">Brief: {{ lang === 'de' ? brief.narrative.label_de : brief.narrative.label_en }}</h3>
      <p style="font-size:13.5px;margin-bottom:12px">{{ lang === 'de' ? brief.brief.summary_de : brief.brief.summary_en }}</p>
      <div class="grid2">
        <div>
          <h3>✔ Key facts (from published fact-checks only)</h3>
          <ul><li v-for="f in brief.brief.key_facts" :key="f">{{ f }}</li></ul>
          <h3 style="margin-top:10px">🎯 Angles that work</h3>
          <ul><li v-for="a in brief.brief.angles" :key="a">{{ a }}</li></ul>
        </div>
        <div>
          <h3>🚫 Avoid</h3>
          <ul><li v-for="a in brief.brief.avoid" :key="a">{{ a }}</li></ul>
          <h3 style="margin-top:10px">📎 Sources</h3>
          <ul><li v-for="c in brief.factchecks" :key="c.id"><a :href="c.url || '#'" target="_blank">{{ c.fact_checker }} — {{ c.verdict }}</a></li></ul>
        </div>
      </div>
    </div>
  </div>
</template>
