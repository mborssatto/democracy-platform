<script setup lang="ts">
// ⚙️ Data: ingest buttons, fact-check sync, pipeline run + queue depth +
// dead-letter retry, and the tipline form.
import type { FactcheckSync, IngestResult, PipelineLog, QueueStatus } from '~/types/api'

const { api } = useApi()
const { guard, refreshState } = useAppState()

const ingestMsg = ref('')
const pipeLog = ref<PipelineLog | null>(null)
const queue = ref<QueueStatus | null>(null)
const tip = ref({ url: '', text: '' })
const tipMsg = ref('')

// Poll queue depth while this tab is visible (the background worker drains
// jobs on its own — this keeps the numbers honest). The component only exists
// while the Data tab is active, so mount/unmount scopes the timer.
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  loadQueue()
  timer = setInterval(loadQueue, 3000)
})
onUnmounted(() => clearInterval(timer))

async function loadQueue() {
  // Silent on failure: this runs on a timer and must not spam the banner.
  try {
    queue.value = await api<QueueStatus>('/queue')
  } catch {
    /* ignore */
  }
}

async function retryJob(id: number) {
  await guard(async () => {
    await api(`/queue/dead/${id}/retry`, { method: 'POST' })
    await loadQueue()
  })
}

async function ingest(src: string) {
  await guard(async () => {
    const r = await api<IngestResult>('/ingest/' + src, { method: 'POST' })
    ingestMsg.value = `+${r.new_items} new items from ${src} (${r.enqueued} queued for enrichment)`
    refreshState()
    loadQueue()
  })
}

async function syncFactchecks() {
  await guard(async () => {
    const r = await api<FactcheckSync>('/sync/factchecks', { method: 'POST' })
    ingestMsg.value =
      r.mode === 'real'
        ? `Fact-check sync: ${r.upserted} fact-checks upserted from ${r.queried_keywords} keyword queries`
        : r.note || 'Mock mode — no FACTCHECK_API_KEY set'
  })
}

async function runPipeline() {
  await guard(async () => {
    pipeLog.value = await api<PipelineLog>('/pipeline/run', { method: 'POST' })
    refreshState()
    loadQueue()
  })
}

async function submitTip() {
  if (!tip.value.url || !tip.value.text) {
    tipMsg.value = 'Please fill both fields.'
    return
  }
  await guard(async () => {
    await api('/tipline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(tip.value),
    })
    tipMsg.value = 'Tip received — run the pipeline to process it.'
    tip.value = { url: '', text: '' }
  })
}
</script>

<template>
  <div>
    <div class="banner">
      Mock mode: platform integrations are simulated with realistic fixture data.
      Each "Ingest" button stands in for a real API — see <b>docs/integrations/</b> for the step-by-step
      guide to make each one real.
    </div>
    <div class="card">
      <h2>1 · Ingest data</h2>
      <p class="hint">Pull posts from each source. Authors are pseudonymized the moment data enters the system.</p>
      <div class="row">
        <button class="btn btn-blue" @click="ingest('tiktok')">Ingest TikTok (mock)</button>
        <button class="btn btn-blue" @click="ingest('junkipedia')">Ingest Junkipedia (mock)</button>
        <button class="btn btn-blue" @click="ingest('tipline')">Ingest tipline queue</button>
        <button class="btn btn-blue" @click="syncFactchecks">Sync fact-checks (Google API)</button>
        <span v-if="ingestMsg" class="meta">{{ ingestMsg }}</span>
      </div>
    </div>
    <div class="card">
      <h2>2 · Run the AI pipeline</h2>
      <p class="hint">Six stages: triage → classify → embed → match fact-checks → score → create opportunities.</p>
      <button class="btn btn-teal" @click="runPipeline">Run pipeline</button>
      <div v-if="pipeLog" style="margin-top:12px">
        <div v-if="pipeLog.note" class="gate" style="background:var(--amber-soft);color:var(--amber)">
          ℹ {{ pipeLog.note }}
        </div>
        <div class="gate ok">✔ Kept {{ pipeLog.triage_kept }} relevant posts · dropped {{ pipeLog.triage_dropped }} irrelevant (e.g. the cheesecake recipe)</div>
        <div class="gate ok">✔ Classified {{ pipeLog.classified }} posts into narratives</div>
        <div class="gate ok">✔ Matched {{ pipeLog.claim_matched }} posts to published fact-checks</div>
        <div class="gate ok">✔ Created {{ pipeLog.opportunities }} response opportunities (low-reach posts never surface)</div>
      </div>
      <div v-if="queue" style="margin-top:12px">
        <span class="meta">Job queue: <b>{{ queue.depth.queued }}</b> queued ·
          <b>{{ queue.depth.running }}</b> running · <b>{{ queue.depth.done }}</b> done ·
          <b>{{ queue.depth.dead }}</b> dead</span>
        <div v-if="queue.depth.dead > 0" style="margin-top:8px">
          <div class="gate fail">✖ {{ queue.depth.dead }} job(s) failed repeatedly and were parked
            (dead-letter) — nothing is lost; inspect the error and retry.</div>
          <table style="margin-top:6px">
            <tr><th>Job</th><th>Attempts</th><th>Last error</th><th></th></tr>
            <tr v-for="j in queue.dead" :key="j.id">
              <td class="meta">#{{ j.id }} · {{ j.payload }}</td>
              <td>{{ j.attempts }}</td>
              <td class="meta">{{ j.last_error }}</td>
              <td><button class="btn btn-ghost" style="padding:3px 10px" @click="retryJob(j.id)">Retry</button></td>
            </tr>
          </table>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>3 · Submit a tip (working feature)</h2>
      <p class="hint">The community tipline is real — try it, then re-run the pipeline.</p>
      <input
        v-model="tip.url"
        placeholder="Link to the post"
        style="width:100%;padding:9px;border:1.5px solid var(--line);border-radius:8px;margin-bottom:8px;font-size:13.5px"
      >
      <textarea v-model="tip.text" placeholder="What does the post claim?" style="min-height:70px" />
      <button class="btn btn-ghost" style="margin-top:8px" @click="submitTip">Submit tip</button>
      <span v-if="tipMsg" class="meta"> {{ tipMsg }}</span>
    </div>
  </div>
</template>
