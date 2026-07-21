<script setup lang="ts">
// 📡 Radar: tracked narratives grouped by evaluation category — the harm
// categories (false info, illegal content, fundamental rights, civic
// discourse, vulnerable groups) plus a Pro-democracy section with narratives
// creators can SUPPORT. Topics expand to show the analyzed posts with their
// extraction channels (speech / on-screen text / caption) and the auditable
// per-category evaluation.
import type { AnalyzedPost, EvalCategory, Narrative } from '~/types/api'

const { api } = useApi()
const { guard, lang, fmt } = useAppState()
const rq = useResponseQueue()

const radar = ref<Narrative[]>([])
const analyzed = ref<AnalyzedPost[]>([])
const open = ref<Record<string, boolean>>({})

// The evaluation categories a post's auditable verdict flags as applying.
function appliesCats(p: AnalyzedPost): EvalCategory[] {
  const ev = p.narrative_eval
  if (!ev || ev.quarantined) return []
  return (Object.entries(ev.categories) as [EvalCategory, { applies: boolean }][])
    .filter(([, v]) => v.applies).map(([k]) => k)
}

// A narrative-matched post already appears under its narrative — don't
// duplicate it in the section-level post list.
function narrativeMatched(p: AnalyzedPost): boolean {
  return !!p.classify && p.classify.narrative !== 'none'
}

// URL-analyzed posts filed under every category their evaluation flags —
// the same category grid the narratives use (one post may appear in several
// sections, e.g. hate speech present AND pro-democracy stance).
const analyzedByCat = computed(() => {
  const map: Partial<Record<EvalCategory, AnalyzedPost[]>> = {}
  for (const p of analyzed.value) {
    if (narrativeMatched(p)) continue
    for (const c of appliesCats(p)) (map[c] ||= []).push(p)
  }
  return map
})

// Nothing disappears: posts with no applying category (or pending/triaged
// analysis) get their own bucket at the end.
const unfiled = computed(() =>
  analyzed.value.filter(p => !narrativeMatched(p) && !appliesCats(p).length))

const bySection = computed(() =>
  EVAL_SECTIONS.map(s => ({
    ...s,
    narratives: radar.value.filter(n => n.category === s.key),
    posts: analyzedByCat.value[s.key] || [],
  })).filter(s => s.narratives.length > 0 || s.posts.length > 0),
)

async function load() {
  await guard(async () => {
    radar.value = await api<Narrative[]>('/radar')
    analyzed.value = await api<AnalyzedPost[]>('/analyzed')
  })
  rq.load()
}
onMounted(load)
</script>

<template>
  <div class="card">
    <h2>📡 Narrative Radar</h2>
    <p class="hint">
      Tracked narratives grouped by evaluation category. Every analyzed post carries an
      auditable evaluation (versioned prompt, stored with its hash). Click a narrative to
      expand its posts and see what was said in each channel — speech, on-screen text, caption.
      New narratives are added from the 🎨 Studio tab.
    </p>

    <div v-if="!radar.length" class="empty">No data yet — go to ⚙️ Data, ingest sources and run the pipeline.</div>

    <section v-for="s in bySection" :key="s.key" :style="s.key === 'pro_democracy' ? 'border-top:2px solid var(--line);margin-top:18px;padding-top:12px' : ''">
      <h3 style="margin:14px 0 2px">{{ s.icon }} {{ s.title }}</h3>
      <p class="meta" style="margin:0 0 8px">{{ s.hint }}</p>

      <div v-for="n in s.narratives" :key="n.code" class="narr">
        <div class="row" style="cursor:pointer" @click="open[n.code] = !open[n.code]">
          <span class="meta">{{ open[n.code] ? '▾' : '▸' }}</span>
          <h3 style="margin:0">{{ lang === 'de' ? n.label_de : n.label_en }}</h3>
          <span class="pill" :class="s.key === 'pro_democracy' ? 'pill-ok' : 'pill-growth'">
            {{ s.key === 'pro_democracy' ? '💚 supportable' : `▲ ${n.growth_pct}% views/24h` }}
          </span>
          <span class="meta" style="margin-left:auto">{{ n.post_count }} posts · {{ fmt(n.total_views) }} views</span>
        </div>

        <template v-if="open[n.code]">
          <p class="meta">{{ n.description_en }}</p>
          <div v-if="!n.items.length" class="empty">No posts matched yet.</div>

          <ContentCard
            v-for="it in n.items" :key="it.id"
            :post="it" density="compact" :queued="rq.queuedIds.value.has(it.id)"
            @queue="rq.add(it.id)" @unqueue="rq.remove(it.id)"
          />
        </template>
      </div>

      <!-- URL-analyzed posts whose auditable evaluation flags this category
           but that matched no tracked narrative — same grid, same cards. -->
      <template v-if="s.posts.length">
        <p class="meta" style="margin:8px 0 0">
          🎬 Analyzed posts flagged <b>{{ s.title.toLowerCase() }}</b> (no tracked narrative):
        </p>
        <ContentCard
          v-for="p in s.posts" :key="p.id"
          :post="p" density="compact" :queued="rq.queuedIds.value.has(p.id)"
          @queue="rq.add(p.id)" @unqueue="rq.remove(p.id)"
        />
      </template>
    </section>

    <section v-if="unfiled.length" style="border-top:1px solid var(--line);margin-top:18px;padding-top:10px">
      <h3 style="margin:0 0 2px">🗂 Other analyzed posts</h3>
      <p class="meta" style="margin:0 0 8px">
        URL-submitted posts where no evaluation category applies (or analysis is
        still pending) — kept visible so nothing disappears.
      </p>
      <ContentCard
        v-for="p in unfiled" :key="p.id"
        :post="p" density="compact" :queued="rq.queuedIds.value.has(p.id)"
        @queue="rq.add(p.id)" @unqueue="rq.remove(p.id)"
      />
    </section>
  </div>
</template>
