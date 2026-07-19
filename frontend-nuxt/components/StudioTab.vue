<script setup lang="ts">
// 🎨 Studio: turn a spreading narrative into a creator brief, grounded
// exclusively on published fact-checks (hard rule #3).
import type { Brief, Narrative } from '~/types/api'

const { api } = useApi()
const { guard, lang } = useAppState()

const radar = ref<Narrative[]>([])
const brief = ref<Brief | null>(null)

onMounted(() => {
  guard(async () => {
    radar.value = await api<Narrative[]>('/radar')
  })
})

async function makeBrief(code: string) {
  await guard(async () => {
    brief.value = await api<Brief>('/briefs/' + code, { method: 'POST' })
  })
}
</script>

<template>
  <div>
    <div class="card">
      <h2>🎨 Creator Studio — briefs</h2>
      <p class="hint">Turn a spreading narrative into a ready-to-use brief for a content creator.</p>
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
