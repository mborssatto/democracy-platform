<script setup lang="ts">
// 📡 Radar: tracked narratives grouped by evaluation category — the harm
// categories (false info, illegal content, fundamental rights, civic
// discourse, vulnerable groups) plus a Pro-democracy section with narratives
// creators can SUPPORT. Topics expand to show the analyzed posts with their
// extraction channels (speech / on-screen text / caption) and the auditable
// per-category evaluation.
import type { EvalCategory, Narrative, NarrativeEval } from '~/types/api'

const { api } = useApi()
const { guard, lang, fmt } = useAppState()

const radar = ref<Narrative[]>([])
const open = ref<Record<string, boolean>>({})
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

const CAT_SHORT: Record<EvalCategory, string> = {
  false_information: 'false info',
  illegal_content: 'illegal',
  fundamental_rights: 'fund. rights',
  civic_discourse: 'civic discourse',
  vulnerable_groups: 'vulnerable groups',
  pro_democracy: 'pro-democracy',
}

const bySection = computed(() =>
  SECTIONS.map(s => ({ ...s, narratives: radar.value.filter(n => n.category === s.key) }))
    .filter(s => s.narratives.length > 0),
)

function flags(ev: NarrativeEval | null): { cat: EvalCategory; rationale: string }[] {
  if (!ev || ev.quarantined) return []
  return (Object.entries(ev.categories) as [EvalCategory, { applies: boolean; rationale: string }][])
    .filter(([, v]) => v.applies)
    .map(([cat, v]) => ({ cat, rationale: v.rationale }))
}

async function load() {
  await guard(async () => {
    radar.value = await api<Narrative[]>('/radar')
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

            <!-- what the post says, channel by channel -->
            <div v-if="it.extraction" style="margin:6px 0">
              <div v-if="it.extraction.speech.length">
                <span class="meta"><b>🎙 Spoken</b> ({{ it.extraction.extractor }} extraction):</span>
                <p v-for="(seg, i) in it.extraction.speech" :key="i" class="txt" style="margin:2px 0">
                  <span class="meta">{{ seg.start_s.toFixed(1) }}–{{ seg.end_s.toFixed(1) }}s</span> {{ seg.text }}
                </p>
              </div>
              <div v-else-if="!it.extraction.has_speech" class="meta">🎙 No speech detected (music/mute — nothing transcribed by design).</div>
              <div v-if="it.extraction.on_screen_text.length" style="margin-top:4px">
                <span class="meta"><b>🖼 On-screen text</b>:</span>
                <p v-for="(t, i) in it.extraction.on_screen_text" :key="i" class="txt" style="margin:2px 0">
                  <span class="meta">
                    {{ t.source.type === 'slide' ? `slide ${t.source.index}` : `${t.source.first_seen_s.toFixed(1)}–${t.source.last_seen_s.toFixed(1)}s` }}
                  </span>
                  {{ t.text }}
                </p>
              </div>
              <p v-if="it.extraction.caption" class="txt" style="margin:4px 0">
                <span class="meta"><b>✏️ Caption</b>:</span> {{ it.extraction.caption }}
              </p>
            </div>
            <p v-else class="txt">{{ lang === 'de' ? it.text : (it.text_en || it.text) }}</p>

            <!-- auditable evaluation -->
            <div v-if="it.narrative_eval" class="row" style="gap:6px;flex-wrap:wrap;margin-top:4px">
              <span v-if="it.narrative_eval.quarantined" class="pill pill-warn">⚠ evaluation quarantined (invalid model output)</span>
              <span
                v-for="f in flags(it.narrative_eval)" :key="f.cat"
                class="pill" :class="f.cat === 'pro_democracy' ? 'pill-ok' : 'pill-warn'"
                :title="f.rationale"
              >{{ f.cat === 'pro_democracy' ? '💚' : '⚠' }} {{ CAT_SHORT[f.cat] }}</span>
              <span v-if="!it.narrative_eval.quarantined && !flags(it.narrative_eval).length" class="pill pill-ok">no category applies</span>
              <span class="meta" :title="'prompt sha256: ' + it.narrative_eval.prompt_sha256">
                evaluated with {{ it.narrative_eval.prompt_version }}
              </span>
            </div>
          </div>
        </template>
      </div>
    </section>
  </div>
</template>
