<script setup lang="ts">
// 📡 Radar: false narratives ranked by growth, with their top posts.
import type { Narrative } from '~/types/api'

const { api } = useApi()
const { guard, lang, fmt } = useAppState()

const radar = ref<Narrative[]>([])

onMounted(() => {
  guard(async () => {
    radar.value = await api<Narrative[]>('/radar')
  })
})
</script>

<template>
  <div class="card">
    <h2>📡 Narrative Radar</h2>
    <p class="hint">Which false narratives are gaining traction right now, ranked by growth. Click a narrative in the Studio tab to turn it into a creator brief.</p>
    <div v-if="!radar.length" class="empty">No data yet — go to ⚙️ Data, ingest sources and run the pipeline.</div>
    <div v-for="n in radar" :key="n.code" class="narr">
      <div class="row">
        <h3>{{ lang === 'de' ? n.label_de : n.label_en }}</h3>
        <span class="pill pill-growth">▲ {{ n.growth_pct }}% views/24h</span>
      </div>
      <div class="stats">
        <span>{{ n.post_count }} posts tracked</span>
        <span>{{ fmt(n.total_views) }} total views</span>
      </div>
      <p class="meta">{{ n.description_en }}</p>
      <div v-for="it in n.items.slice(0, 3)" :key="it.id" class="item">
        <div class="row">
          <span class="pill pill-plat">{{ it.platform }}</span>
          <span class="meta">{{ it.author_pseudo }} · {{ it.author_followers_band }} followers · {{ fmt(it.views) }} views</span>
          <a v-if="it.url" :href="it.url" target="_blank" rel="noopener noreferrer" class="meta">open original ↗</a>
        </div>
        <p class="txt">{{ lang === 'de' ? it.text : (it.text_en || it.text) }}</p>
      </div>
    </div>
  </div>
</template>
