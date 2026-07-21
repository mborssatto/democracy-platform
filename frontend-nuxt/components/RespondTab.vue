<script setup lang="ts">
// 💬 Respond: the response queue — content the user chose to react to (added
// from Data/Radar), a rail of cards on the left, the draft editor on the
// right. No claiming: anyone can draft anything (owner decision 2026-07-21);
// the human-in-the-loop gates still apply to every draft. The platform never
// posts — humans post from their own accounts (hard rule #1).
import type { GateResult, ItemDraft, QueueEntry } from '~/types/api'

const { api } = useApi()
const { state, guard, fail, err } = useAppState()
const rq = useResponseQueue()

const selectedId = ref<number | null>(null)
const activeDraft = ref<ItemDraft | null>(null)
const draftText = ref('')
const activeTone = ref<string | null>(null)
const gateResult = ref<Partial<GateResult>>({ checks: [] })

// The seed carries three tone variants ("Warm: …\nFactual: …\nWry: …").
// The editor starts with one and the tone buttons swap between them; the
// backend gate measures the edit distance against the closest variant, so
// picking a tone counts as 0% edited — the rework still has to be yours.
const TONES = ['Warm', 'Factual', 'Wry']

const variants = computed<Record<string, string> | null>(() => {
  const seed = activeDraft.value?.ai_seed || ''
  const ms = [...seed.matchAll(/^[ \t]*(Warm|Factual|Wry)[ \t]*:[ \t]*/gim)]
  if (ms.length < 2) return null // legacy single-text seed — no buttons
  const out: Record<string, string> = {}
  ms.forEach((m, i) => {
    const start = (m.index ?? 0) + m[0].length
    const end = i + 1 < ms.length ? ms[i + 1].index : seed.length
    const tone = m[1][0].toUpperCase() + m[1].slice(1).toLowerCase()
    out[tone] = seed.slice(start, end).trim()
  })
  return out
})

function pickTone(tone: string) {
  const text = variants.value?.[tone]
  if (!text) return
  activeTone.value = tone
  draftText.value = text
  debouncedCheck()
}

// "Create script for video response" — planned feature, popover only for now.
const showVideoNote = ref(false)
let videoNoteTimer: ReturnType<typeof setTimeout> | undefined
function videoScriptSoon() {
  showVideoNote.value = !showVideoNote.value
  clearTimeout(videoNoteTimer)
  if (showVideoNote.value) videoNoteTimer = setTimeout(() => (showVideoNote.value = false), 6000)
}
onUnmounted(() => clearTimeout(videoNoteTimer))
const showPostedForm = ref(false)
const postedUrl = ref('')
const postedMsg = ref('')
const copied = ref(false)

onMounted(rq.load)

const selected = computed<QueueEntry | null>(() =>
  rq.entries.value.find(e => e.id === selectedId.value) || null)

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

// Her flow, step 6: clicking a card opens its draft when one exists;
// otherwise the pane shows the Draft response button (step 4).
function selectCard(entry: QueueEntry) {
  if (selectedId.value === entry.id) return
  selectedId.value = entry.id
  activeDraft.value = null
  showPostedForm.value = false
  postedMsg.value = ''
  copied.value = false
  if (entry.draft) openDraft(entry.id)
}

async function openDraft(itemId: number) {
  await guard(async () => {
    const d = await api<ItemDraft>(`/items/${itemId}/draft`, { method: 'POST' })
    activeDraft.value = d
    activeTone.value = null
    draftText.value = d.current_text
    gateResult.value = { checks: [], edit_distance_pct: 0, post_ready: false }
    if (d.current_text === d.ai_seed && variants.value) {
      pickTone('Warm') // untouched draft: start with ONE variant, not all three
    } else if (!d.created && d.current_text !== d.ai_seed) {
      // resume: restore gate state; highlight the tone the text came from, if any
      const match = Object.entries(variants.value || {}).find(([, t]) => t === d.current_text)
      activeTone.value = match ? match[0] : null
      await saveDraft()
    }
    rq.load() // refresh the rail's draft badges
  })
}

function removeFromQueue(entry: { id: number }) {
  rq.remove(entry.id)
  if (selectedId.value === entry.id) {
    selectedId.value = null
    activeDraft.value = null
  }
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
    /* clipboard unavailable — ignore */
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
    rq.load()
  } catch (e) {
    postedMsg.value = ''
    fail(e)
  }
}
</script>

<template>
  <div class="card">
    <h2>💬 Response queue</h2>
    <p class="hint">
      Content you chose to react to. Find posts on ⚙️ Data or 📡 Radar and click
      "Add to response queue"; here you draft your reply — the AI seed is raw
      material, and the gates only unlock posting after you genuinely rework it.
    </p>

    <div class="q-layout">
      <!-- left rail: the queue -->
      <div>
        <p class="meta" style="margin:0 0 8px"><b>Queue ({{ rq.entries.value.length }})</b></p>
        <LoadingSpinner v-if="rq.loading.value" />
        <template v-else>
          <ContentCard
            v-for="e in rq.entries.value" :key="e.id"
            :post="e" density="queue"
            :selected="e.id === selectedId"
            :draft-status="e.draft?.status || null"
            @select="selectCard(e)"
            @unqueue="removeFromQueue(e)"
          />
          <template v-if="!rq.entries.value.length">
            <div class="q-ghost" />
            <div class="q-ghost" />
            <div class="q-ghost" />
          </template>
          <div class="q-empty">Add posts from ⚙️ Data or 📡 Radar</div>
        </template>
      </div>

      <!-- right pane: empty state → card CTA → draft editor -->
      <div>
        <div v-if="rq.loading.value" class="q-empty" style="min-height:220px;display:flex;align-items:center;justify-content:center">
          <LoadingSpinner />
        </div>

        <div v-else-if="!selected" class="q-empty" style="min-height:220px;display:flex;align-items:center;justify-content:center">
          <span style="max-width:320px">
            {{ rq.entries.value.length
              ? 'Select a post from the queue to start or continue its draft.'
              : 'Nothing queued yet. Find content on the Data or Radar tab and click "Add to response queue".' }}
          </span>
        </div>

        <template v-else-if="!activeDraft">
          <div class="item" style="margin-top:0">
            <b style="font-size:13.5px">{{ (selected.url || '').replace(/^https?:\/\/(www\.)?[^/]+\//, '') || selected.platform_item_id }}</b>
            <p class="txt">{{ selected.summarize?.summary || selected.extraction?.caption || selected.text }}</p>
            <button class="btn btn-blue" @click="openDraft(selected.id)">Draft response</button>
          </div>
        </template>

        <template v-else>
          <h3 style="margin:0 0 4px">✍️ Make it yours</h3>
          <p class="hint">The AI seed below is <b>raw material, not a message</b>. The Post-ready state unlocks only
            after you genuinely rework it — that's the human-in-the-loop gate, enforced in software.</p>
          <details v-if="variants" class="seed">
            <summary style="cursor:pointer"><b>AI seed (immutable) — all three tones</b></summary>
            <div style="white-space:pre-wrap;margin-top:6px">{{ activeDraft.ai_seed }}</div>
          </details>
          <div v-else class="seed"><b>AI seed (immutable):</b><br>{{ activeDraft.ai_seed }}</div>
          <div class="row" style="margin:10px 0 6px;gap:6px;align-items:center;flex-wrap:wrap;position:relative">
            <template v-if="variants">
              <span class="meta">Tone:</span>
              <button
                v-for="t in TONES" :key="t" v-show="variants[t]"
                class="btn" :class="activeTone === t ? 'btn-blue' : 'btn-ghost'"
                @click="pickTone(t)"
              >{{ t }}</button>
            </template>
            <button
              class="btn btn-ghost"
              style="margin-left:auto;border-style:dashed;color:var(--muted, #667)"
              @click="videoScriptSoon"
            >🎥 Create script for video response</button>
            <div
              v-if="showVideoNote"
              style="position:absolute;top:100%;right:0;z-index:5;margin-top:4px;max-width:320px;
                     background:#fff7e6;border:1px solid #f0d9a8;border-radius:8px;
                     padding:8px 10px;font-size:12.5px;box-shadow:0 4px 12px rgba(0,0,0,.08)"
            >
              🚧 <b>Feature in progress</b> — turning the reply into a short video script you can
              record yourself isn't built yet. For now, work with the text reply.
            </div>
          </div>
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
        </template>
      </div>
    </div>
  </div>
</template>
