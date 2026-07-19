<script setup lang="ts">
// 💬 Respond: opportunity list → claim → draft editor with the immutable AI
// seed, debounced gate checks, edit-distance meter, copy + mark-posted.
// The platform never posts — humans post from their own accounts (hard rule #1).
import type { Draft, GateResult, Opportunity } from '~/types/api'

const { api } = useApi()
const { state, guard, fail, err, lang, fmt } = useAppState()

const opps = ref<Opportunity[]>([])
const activeDraft = ref<Draft | null>(null)
const draftText = ref('')
const gateResult = ref<Partial<GateResult>>({ checks: [] })
const showPostedForm = ref(false)
const postedUrl = ref('')
const postedMsg = ref('')
const copied = ref(false)

onMounted(loadOpps)

async function loadOpps() {
  await guard(async () => {
    opps.value = await api<Opportunity[]>('/opportunities')
  })
}

function gateLabel(n: string): string {
  const labels: Record<string, string> = {
    edit_gate: 'Genuine edit',
    neutrality: 'Party neutrality',
    copypasta: 'Not copy-paste',
    dwell_time: 'Review time',
    rate_cap: 'Daily cap',
  }
  return labels[n] || n
}

async function claimOpp(o: Opportunity) {
  try {
    err.value = ''
    await api(`/opportunities/${o.id}/claim`, { method: 'POST' })
  } catch (e) {
    fail(e) // e.g. already claimed by someone else
    loadOpps() // refresh so the stale card disappears
    return
  }
  await guard(async () => {
    const d = await api<Draft>(`/opportunities/${o.id}/draft`, { method: 'POST' })
    activeDraft.value = d
    draftText.value = d.ai_seed
    gateResult.value = { checks: [], edit_distance_pct: 0, post_ready: false }
    showPostedForm.value = false
    postedMsg.value = ''
    copied.value = false
  })
}

let debounceTimer: ReturnType<typeof setTimeout> | undefined
function debouncedCheck() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(saveDraft, 700)
}
onUnmounted(() => clearTimeout(debounceTimer))

async function saveDraft() {
  await guard(async () => {
    if (!activeDraft.value) return
    gateResult.value = await api<GateResult>(`/drafts/${activeDraft.value.draft_id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: draftText.value }),
    })
  })
}

async function copyText() {
  try {
    await navigator.clipboard.writeText(draftText.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    /* clipboard unavailable — ignore, same as the old page */
  }
}

async function markPosted() {
  if (!postedUrl.value) {
    fail(new Error('Paste the link to your posted reply first.'))
    return
  }
  try {
    if (!activeDraft.value) return
    err.value = ''
    await api(`/drafts/${activeDraft.value.draft_id}/mark-posted`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ posted_url: postedUrl.value }),
    })
    postedMsg.value = 'Recorded. The thread will be re-checked at +1h/+24h/+72h (full plan).'
  } catch (e) {
    postedMsg.value = ''
    fail(e)
  }
}

function backToList() {
  activeDraft.value = null
  loadOpps()
}
</script>

<template>
  <div>
    <div v-if="!activeDraft" class="card">
      <h2>💬 Response opportunities</h2>
      <p class="hint">Threads where a reply would matter, ranked by the transparent score. Claim one to start.</p>
      <div v-if="!opps.length" class="empty">No open opportunities — ingest data and run the pipeline first.</div>
      <div v-for="o in opps" :key="o.id" class="item">
        <div class="row">
          <span class="pill pill-plat">{{ o.platform }}</span>
          <span class="pill pill-score">score {{ o.score }}</span>
          <span v-if="o.fact_checker" class="pill pill-fc">✔ fact-check available</span>
          <span class="meta">{{ fmt(o.views) }} views · {{ o.author_followers_band }} followers</span>
        </div>
        <p class="txt">{{ lang === 'de' ? o.text : (o.text_en || o.text) }}</p>
        <details class="meta" style="margin-bottom:8px">
          <summary>Why this score?</summary>
          <pre style="font-size:11px;margin-top:4px">{{ o.score_breakdown }}</pre>
        </details>
        <div class="row">
          <button v-if="o.status === 'open'" class="btn btn-blue" @click="claimOpp(o)">Claim & draft</button>
          <span v-else class="meta">claimed</span>
          <a v-if="o.fc_url" :href="o.fc_url" target="_blank" class="meta">{{ o.fact_checker }}: {{ o.verdict }} ↗</a>
        </div>
      </div>
    </div>

    <!-- draft editor -->
    <div v-if="activeDraft" class="card">
      <h2>✍️ Make it yours</h2>
      <p class="hint">The AI seed below is <b>raw material, not a message</b>. The Post-ready state unlocks only
        after you genuinely rework it — that's the human-in-the-loop gate, enforced in software.</p>
      <div class="seed"><b>AI seed (immutable):</b><br>{{ activeDraft.ai_seed }}</div>
      <textarea v-model="draftText" @input="debouncedCheck" />
      <div class="meter"><div :style="{ width: Math.min(100, gateResult.edit_distance_pct || 0) + '%' }" /></div>
      <p class="meta">Edit distance: {{ gateResult.edit_distance_pct || 0 }}% (need ≥{{ state.config ? state.config.edit_gate_min_pct : 15 }}% + substantive rework)</p>
      <div v-for="c in gateResult.checks" :key="c.name" class="gate" :class="c.passed ? 'ok' : 'fail'">
        <span>{{ c.passed ? '✔' : '✖' }}</span><span><b>{{ gateLabel(c.name) }}:</b> {{ c.detail }}</span>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn btn-ghost" @click="saveDraft">Re-check gates</button>
        <button class="btn btn-teal" :disabled="!gateResult.post_ready" @click="copyText">
          {{ copied ? 'Copied ✔' : 'Copy final text' }}</button>
        <button class="btn btn-blue" :disabled="!gateResult.post_ready" @click="showPostedForm = true">
          I posted it myself</button>
        <button class="btn btn-ghost" @click="backToList">Back</button>
      </div>
      <p class="meta" style="margin-top:8px">The platform has no post button and stores no passwords —
        you post from your own account, then paste the link:</p>
      <div v-if="showPostedForm" class="row" style="margin-top:8px">
        <input
          v-model="postedUrl"
          placeholder="https://... link to your posted reply"
          style="flex:1;padding:9px;border:1.5px solid var(--line);border-radius:8px;font-size:13px"
        >
        <button class="btn btn-blue" @click="markPosted">Confirm</button>
      </div>
      <p v-if="postedMsg" class="gate ok" style="margin-top:8px">✔ {{ postedMsg }}</p>
    </div>
  </div>
</template>
