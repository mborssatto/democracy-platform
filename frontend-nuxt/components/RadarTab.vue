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

const radar = ref<Narrative[]>([])
const analyzed = ref<AnalyzedPost[]>([])
const open = ref<Record<string, boolean>>({})
const openPost = ref<Record<number, boolean>>({})
const showAdd = ref(false)
const addMsg = ref('')
const draft = ref({
  code: '', label_de: '', label_en: '', description_en: '',
  category: 'false_information' as EvalCategory,
})

// Section order + labels. Harm sections first, pro-democracy last (visually
// separated as the "supportable" section).
const SECTIONS: { key: EvalCategory; icon: string; title: string; hint: string }[] = [
  { key: 'false_information', icon: '❌', title: 'False & misleading narratives',
    hint: 'Match narratives already debunked by professional fact-checkers — the platform never issues its own verdicts.' },
  { key: 'illegal_content', icon: '⚖️', title: 'Illegal content',
    hint: 'Content illegal in Germany/the EU: incitement, banned symbols, credible threats.' },
  { key: 'fundamental_rights', icon: '🕊️', title: 'Against fundamental rights',
    hint: 'Attacks on human dignity, equal treatment, press freedom and other fundamental rights.' },
  { key: 'civic_discourse', icon: '🗳️', title: 'Against civic discourse',
    hint: 'Delegitimizing elections without evidence, discouraging participation, intimidation.' },
  { key: 'vulnerable_groups', icon: '🛡️', title: 'Minors, minorities & public health',
    hint: 'Content threatening the protection of minors, minorities or public health.' },
  { key: 'pro_democracy', icon: '💚', title: 'Pro-democracy narratives',
    hint: 'Ideas creators can actively SUPPORT: nonpartisan civic information, participation, media literacy.' },
]

const bySection = computed(() =>
  SECTIONS.map(s => ({ ...s, narratives: radar.value.filter(n => n.category === s.key) }))
    .filter(s => s.narratives.length > 0),
)

function narrativeLabel(code: string): string {
  const n = radar.value.find(x => x.code === code)
  return n ? (lang.value === 'de' ? n.label_de : n.label_en) : code
}

async function load() {
  await guard(async () => {
    radar.value = await api<Narrative[]>('/radar')
    analyzed.value = await api<AnalyzedPost[]>('/analyzed')
  })
}
onMounted(load)

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
  <div class="card">
    <div class="row">
      <h2>📡 Narrative Radar</h2>
      <button class="btn btn-ghost" style="margin-left:auto" @click="showAdd = !showAdd">
        {{ showAdd ? 'Cancel' : '+ Add narrative' }}
      </button>
    </div>
    <p class="hint">
      Tracked narratives grouped by evaluation category. Every analyzed post carries an
      auditable evaluation (versioned prompt, stored with its hash). Click a narrative to
      expand its posts and see what was said in each channel — speech, on-screen text, caption.
    </p>

    <div v-if="showAdd" class="item" style="margin-bottom:14px">
      <h3 style="margin-top:0">Add a narrative to the watchlist</h3>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <input v-model="draft.code" placeholder="code (e.g. medien_luegen)" style="flex:1;min-width:180px;padding:8px;border:1.5px solid var(--line);border-radius:8px">
        <select v-model="draft.category" style="padding:8px;border:1.5px solid var(--line);border-radius:8px">
          <option v-for="s in SECTIONS" :key="s.key" :value="s.key">{{ s.icon }} {{ s.title }}</option>
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

    <div v-if="!radar.length" class="empty">No data yet — go to ⚙️ Data, ingest sources and run the pipeline.</div>

    <!-- URL-submitted posts: always visible with their full analysis, even
         when they match no tracked narrative or were triaged out. -->
    <section v-if="analyzed.length">
      <h3 style="margin:14px 0 2px">🎬 Analyzed media posts</h3>
      <p class="meta" style="margin:0 0 8px">
        Posts submitted by URL, with everything the system read in them — speech,
        on-screen text, caption — and the auditable evaluation. Shown regardless of
        whether they matched a tracked narrative.
      </p>
      <div v-for="p in analyzed" :key="p.id" class="narr" style="border-left-color:var(--teal)">
        <div class="row" style="cursor:pointer" @click="openPost[p.id] = !openPost[p.id]">
          <span class="meta">{{ openPost[p.id] ? '▾' : '▸' }}</span>
          <span class="pill pill-plat">{{ p.platform }}</span>
          <b style="font-size:13.5px">{{ p.extraction?.caption || p.platform_item_id }}</b>
          <span
            class="pill" :class="p.status === 'triaged_out' ? 'pill-warn' : 'pill-ok'"
            :title="p.triage?.reason"
          >{{ p.status === 'triaged_out' ? 'triaged out' : (p.status === 'enriched' ? 'analyzed' : 'analysis pending') }}</span>
          <span class="meta" style="margin-left:auto">{{ p.author_pseudo }} · {{ fmt(p.views) }} views</span>
        </div>
        <template v-if="openPost[p.id]">
          <div class="item">
            <div class="row">
              <a v-if="p.url" :href="p.url" target="_blank" rel="noopener noreferrer" class="meta">open original ↗</a>
              <span v-if="p.classify && p.classify.narrative !== 'none'" class="meta">
                narrative: <b>{{ narrativeLabel(p.classify.narrative) }}</b> ({{ Math.round(p.classify.confidence * 100) }}%)
              </span>
              <span v-else-if="p.classify" class="meta">no tracked narrative matched</span>
            </div>
            <div v-if="p.triage" class="gate" :class="p.triage.relevant ? 'ok' : ''" :style="p.triage.relevant ? '' : 'background:var(--amber-soft);color:var(--amber)'">
              {{ p.triage.relevant ? '✔' : 'ℹ' }} Triage: {{ p.triage.reason }}
            </div>
            <AnalysisDetail :extraction="p.extraction" :evaluation="p.narrative_eval" />
          </div>
        </template>
      </div>
    </section>

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

          <div v-for="it in n.items" :key="it.id" class="item">
            <div class="row">
              <span class="pill pill-plat">{{ it.platform }}</span>
              <span class="meta">{{ it.author_pseudo }} · {{ it.author_followers_band }} followers · {{ fmt(it.views) }} views</span>
              <a v-if="it.url" :href="it.url" target="_blank" rel="noopener noreferrer" class="meta">open original ↗</a>
            </div>

            <!-- channels + auditable evaluation (shared renderer) -->
            <p v-if="!it.extraction" class="txt">{{ lang === 'de' ? it.text : (it.text_en || it.text) }}</p>
            <AnalysisDetail :extraction="it.extraction" :evaluation="it.narrative_eval" />
          </div>
        </template>
      </div>
    </section>
  </div>
</template>
