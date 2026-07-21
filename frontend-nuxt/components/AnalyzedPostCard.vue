<script setup lang="ts">
// One URL-submitted post as a card: link, summary, category/topic pills,
// score breakdown bars, metrics, and the expandable channel transcript.
// Summary + topic render "—" until the backend produces them (pipeline TODO).
import type { AnalyzedPost, EvalCategory, Opportunity } from '~/types/api'

const props = defineProps<{ post: AnalyzedPost; opportunity: Opportunity | null }>()
const emit = defineEmits<{ draft: [post: AnalyzedPost] }>()

const open = ref(false)

const CAT_LABEL: Record<EvalCategory, string> = {
  false_information: 'False information',
  illegal_content: 'Illegal content',
  hate_speech: 'Hate speech',
  fundamental_rights: 'Fundamental rights',
  civic_discourse: 'Civic discourse',
  vulnerable_groups: 'Vulnerable groups',
  pro_democracy: 'Pro-democracy',
}

// "https://www.tiktok.com/@dw/video/123" → "@dw/video/123"
const linkLabel = computed(() =>
  (props.post.url || '').replace(/^https?:\/\/(www\.)?[^/]+\//, '') || props.post.url || '')

const categories = computed<{ cat: EvalCategory; rationale: string }[]>(() => {
  const ev = props.post.narrative_eval
  if (!ev || ev.quarantined) return []
  return (Object.entries(ev.categories) as [EvalCategory, { applies: boolean; rationale: string }][])
    .filter(([, v]) => v.applies)
    .map(([cat, v]) => ({ cat, rationale: v.rationale }))
})

// Score component maxima + hover explanations mirror app/pipeline.py _score()
// so bars show how full each factor is, not just its raw points.
const SCORE_MAX: [key: string, label: string, max: number, tip: string][] = [
  ['velocity', 'velocity', 40,
    'How fast the post is spreading: view growth since the last check. Faster growth earns more points (max 40).'],
  ['reach', 'reach', 30,
    "The author's audience size, by follower band: <1k = 0, 1k–10k = 10, 10k–100k = 20, 100k+ = 30 points."],
  ['actionability (fact-check exists)', 'fact-check', 20,
    'Whether a published fact-check matches a claim in this post. A match makes it actionable (20 points); without one it gets 5. We never write our own verdicts — we only cite published fact-checks.'],
  ['classification confidence', 'confidence', 10,
    'How certain the classifier is that this post matches a monitored narrative (confidence × 10, max 10).'],
]
const scoreBlocked = computed(() =>
  typeof props.post.score?.breakdown?.blocked === 'string' ? String(props.post.score!.breakdown.blocked) : null)
const scoreParts = computed(() => {
  const bd = props.post.score?.breakdown
  if (!bd || scoreBlocked.value) return []
  return SCORE_MAX
    .filter(([key]) => typeof bd[key] === 'number')
    .map(([key, label, max, tip]) => ({ label, max, tip, pts: Number(bd[key]) }))
})
const scoreBand = computed(() => {
  const s = props.post.score?.score
  if (s == null) return null
  return s >= 70 ? 'high' : s >= 40 ? 'medium' : 'low'
})

function fmt(s: number): string {
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

// Transcript rows: speech + video overlays interleaved by start time, then
// slides in order, then the caption — same ordering idea as merged_text.
const transcript = computed(() => {
  const ex = props.post.extraction
  if (!ex) return []
  const timed: { at: number; label: string; text: string }[] = [
    ...ex.speech.map(s => ({ at: s.start_s, label: `Speech ${fmt(s.start_s)}–${fmt(s.end_s)}`, text: s.text })),
    ...ex.on_screen_text
      .filter(t => t.source.type === 'video')
      .map(t => ({
        at: (t.source as { first_seen_s: number }).first_seen_s,
        label: `On-screen ${fmt((t.source as { first_seen_s: number }).first_seen_s)}–${fmt((t.source as { last_seen_s: number }).last_seen_s)}`,
        text: t.text,
      })),
  ].sort((a, b) => a.at - b.at)
  const slides = ex.on_screen_text
    .filter(t => t.source.type === 'slide')
    .map(t => ({ label: `Slide ${(t.source as { index: number }).index}`, text: t.text }))
  const rows = [...timed.map(({ label, text }) => ({ label, text })), ...slides]
  if (ex.caption) rows.push({ label: 'Caption', text: ex.caption })
  return rows
})

// Draftable unless someone already claimed it (no opportunity yet is fine —
// the backend creates one on demand; the amplification floor is enforced
// there and surfaces as a clear error banner).
const claimable = computed(() => !props.opportunity || props.opportunity.status === 'open')
const claimHint = computed(() =>
  claimable.value ? '' : `Already claimed by ${props.opportunity!.claimed_by}`)

const posted = computed(() => (props.post.posted_at || '').slice(0, 10))
const analyzed = computed(() => (props.post.ingested_at || '').slice(0, 10))
</script>

<template>
  <div class="item" style="margin-top:10px">
    <div class="row" style="justify-content:space-between;align-items:flex-start;flex-wrap:nowrap;gap:12px">
      <div style="min-width:0">
        <a v-if="post.url" :href="post.url" target="_blank" rel="noopener" style="font-size:13.5px;font-weight:600">{{ linkLabel }} ↗</a>
        <div class="meta" style="margin-top:2px">
          {{ post.platform }} · {{ post.author_pseudo }}
          <template v-if="posted"> · posted {{ posted }}</template> · analyzed {{ analyzed }}
        </div>
      </div>
      <button
        class="btn btn-blue"
        style="flex-shrink:0"
        :disabled="!claimable"
        :title="claimHint"
        @click="claimable && emit('draft', post)"
      >Draft response</button>
    </div>

    <p v-if="post.summarize?.summary" class="txt">{{ post.summarize.summary }}</p>
    <p v-else class="txt" style="color:var(--muted)">
      Summary: — (run the pipeline to generate it)
    </p>

    <div class="row" style="gap:6px;flex-wrap:wrap;align-items:center">
      <span
        v-for="c in categories" :key="c.cat"
        class="pill" :class="c.cat === 'pro_democracy' ? 'pill-ok' : 'pill-warn'"
        :title="c.rationale"
      >{{ CAT_LABEL[c.cat] }}</span>
      <span v-if="post.narrative_eval && !post.narrative_eval.quarantined && !categories.length" class="pill pill-ok">no category applies</span>
      <span v-if="post.narrative_eval?.quarantined" class="pill pill-warn">⚠ evaluation quarantined</span>
      <span class="pill pill-plat">Topic: {{ post.summarize?.topic || '—' }}</span>
      <span class="meta">👥 {{ post.author_followers_band || '?' }} followers</span>
      <span class="meta">👁 {{ post.views.toLocaleString() }} views</span>
    </div>

    <div v-if="post.score" class="score-strip">
      <span class="score-num" :class="'score-' + scoreBand">
        {{ post.score.score }}
        <em>{{ scoreBand }} priority</em>
      </span>
      <template v-if="scoreBlocked">
        <span class="meta">{{ scoreBlocked }}</span>
      </template>
      <span v-for="p in scoreParts" :key="p.label" class="score-comp" :title="p.tip">
        <span class="meta">{{ p.label }}</span>
        <span class="score-bar"><i :style="{ width: Math.round(100 * p.pts / p.max) + '%' }" /></span>
        <span class="meta">{{ p.pts }}/{{ p.max }}</span>
      </span>
    </div>
    <p v-else-if="post.status === 'triaged_out'" class="meta" style="margin-top:8px" :title="post.triage?.reason">
      Not scored — triaged out as not relevant to the monitored discourse.
    </p>

    <div v-if="post.extraction" style="border-top:1px solid var(--line);margin-top:10px;padding-top:8px">
      <button class="chev" @click="open = !open">{{ open ? '▾' : '▸' }} Transcript</button>
      <div v-if="open" style="margin-top:6px">
        <div v-for="(r, i) in transcript" :key="i" class="tr-row">
          <span class="tr-label meta">{{ r.label }}</span>
          <span class="tr-text">{{ r.text }}</span>
        </div>
        <p v-if="!post.extraction.has_speech" class="meta" style="margin-top:4px">
          🎙 No speech detected (music/mute — nothing transcribed by design).
        </p>
      </div>
    </div>
  </div>
</template>
