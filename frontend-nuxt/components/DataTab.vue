<script setup lang="ts">
// ⚙️ Data: URL ingestion, pipeline run + queue depth + dead-letter retry,
// and the tipline form.
import type { AnalyzedPost, JobProgress, PipelineLog, QueueStatus, UrlIngestResult } from '~/types/api'

const { api } = useApi()
const { guard, refreshState } = useAppState()
const rq = useResponseQueue()

const urlsInput = ref('')
const urlResults = ref<UrlIngestResult | null>(null)
const analyzed = ref<AnalyzedPost[]>([])
const pipeLog = ref<PipelineLog | null>(null)
const queue = ref<QueueStatus | null>(null)

// Poll queue depth while this tab is visible (the background worker drains
// jobs on its own — this keeps the numbers honest). The component only exists
// while the Data tab is active, so mount/unmount scopes the timer.
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  loadQueue()
  loadAnalyzed()
  rq.load()
  timer = setInterval(loadQueue, 3000)
})
onUnmounted(() => clearInterval(timer))

async function loadQueue() {
  // Silent on failure: this runs on a timer and must not spam the banner.
  try {
    const q = await api<QueueStatus>('/queue')
    // A job just finished → a new analysis may exist; refresh the cards.
    if (queue.value && q.depth.done !== queue.value.depth.done) loadAnalyzed()
    queue.value = q
  } catch {
    /* ignore */
  }
}

async function loadAnalyzed() {
  try {
    analyzed.value = await api<AnalyzedPost[]>('/analyzed')
  } catch {
    /* ignore — same silent rule as loadQueue */
  }
}

async function retryJob(id: number) {
  await guard(async () => {
    await api(`/queue/dead/${id}/retry`, { method: 'POST' })
    await loadQueue()
  })
}

// Per-URL progress: each queued URL is tracked by its job id and polled while
// the worker acquires + analyzes it. Stage → fill percentage; a bar reaching
// 100% means the post appears under "Analyzed posts" below.
const tracked = ref<{ url: string; job_id: number; status: JobProgress['status']; stage: JobProgress['stage'] }[]>([])
let jobTimer: ReturnType<typeof setInterval> | undefined
onUnmounted(() => clearInterval(jobTimer))

const STAGE_PCT: Record<string, number> = { downloading: 40, extracting: 75, saving: 92 }
function jobPct(t: { status: string; stage: string | null }): number {
  if (t.status === 'done') return 100
  if (t.status === 'dead') return 100
  if (t.status === 'running') return STAGE_PCT[t.stage || ''] ?? 25
  return 8 // queued — waiting for the worker to pick it up
}

function jobLabel(t: { status: string; stage: string | null }): string {
  if (t.status === 'done') return '✔ analyzed'
  if (t.status === 'dead') return '✖ failed (see dead-letter below)'
  if (t.status === 'running') return `${t.stage || 'starting'}…`
  return 'queued…'
}

async function pollJobs() {
  const active = tracked.value.filter(t => t.status !== 'done' && t.status !== 'dead')
  if (!active.length) {
    clearInterval(jobTimer)
    jobTimer = undefined
    return
  }
  try {
    const res = await api<{ jobs: JobProgress[] }>(`/jobs?ids=${active.map(t => t.job_id).join(',')}`)
    let finished = false
    for (const j of res.jobs) {
      const t = tracked.value.find(x => x.job_id === j.id)
      if (!t) continue
      if (j.status === 'done' && t.status !== 'done') finished = true
      t.status = j.status
      t.stage = j.stage
    }
    if (finished) loadAnalyzed() // a bar just hit 100% — show the new card
  } catch {
    /* silent, same rule as loadQueue */
  }
}

async function submitUrls() {
  const urls = urlsInput.value.split(/[\n,]/).map(u => u.trim()).filter(Boolean)
  if (!urls.length) return
  await guard(async () => {
    urlResults.value = await api<UrlIngestResult>('/ingest/urls', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls }),
    })
    urlsInput.value = ''
    tracked.value = urlResults.value.results
      .filter(r => r.status === 'queued' && r.job_id != null)
      .map(r => ({ url: r.url, job_id: r.job_id!, status: 'queued' as const, stage: null }))
    clearInterval(jobTimer)
    if (tracked.value.length) jobTimer = setInterval(pollJobs, 2000)
    loadQueue()
  })
}

function onCsvUpload(e: Event) {
  // Client-side CSV parse: take the `url` column when a header exists,
  // otherwise every cell that looks like a URL. Delimiter , or ;.
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const text = String(reader.result || '').replace(/^﻿/, '') // strip BOM
    const lines = text.split(/\r?\n/).filter(l => l.trim())
    if (!lines.length) return
    const delim = (lines[0].match(/;/g)?.length || 0) > (lines[0].match(/,/g)?.length || 0) ? ';' : ','
    const header = lines[0].toLowerCase().split(delim).map(h => h.trim().replace(/^"|"$/g, ''))
    const urlCol = header.indexOf('url')
    const rows = urlCol >= 0 ? lines.slice(1) : lines
    const urls = rows
      .map(l => (urlCol >= 0 ? l.split(delim)[urlCol] : l.split(delim).find(c => c.includes('http'))) || '')
      .map(u => u.trim().replace(/^"|"$/g, ''))
      .filter(u => u.startsWith('http'))
    urlsInput.value = [urlsInput.value, ...urls].filter(Boolean).join('\n')
  }
  reader.readAsText(file)
  ;(e.target as HTMLInputElement).value = '' // same file can be re-selected
}

async function runPipeline() {
  await guard(async () => {
    pipeLog.value = await api<PipelineLog>('/pipeline/run', { method: 'POST' })
    refreshState()
    loadQueue()
  })
}

</script>

<template>
  <div>
    <div class="card">
      <h2>1 · Ingest data</h2>

      <div class="item" style="margin-top:12px">
        <h3 style="margin-top:0">Add posts by URL (single or bulk)</h3>
        <p class="hint" style="margin-bottom:8px">
          Paste TikTok post URLs (one per line or comma-separated), or upload a CSV with a
          <b>url</b> column. Each post is downloaded, its video/slides analyzed
          (speech + on-screen text + caption), then run through the pipeline.
        </p>
        <textarea
          v-model="urlsInput"
          placeholder="https://www.tiktok.com/@user/video/1234567890&#10;https://www.tiktok.com/@user/photo/9876543210"
          style="min-height:70px"
        />
        <div class="row" style="margin-top:8px">
          <button class="btn btn-teal" @click="submitUrls">Analyze URLs</button>
          <label class="btn btn-ghost" style="cursor:pointer">
            Upload CSV
            <input type="file" accept=".csv,text/csv" style="display:none" @change="onCsvUpload">
          </label>
        </div>
        <div v-if="urlResults" style="margin-top:8px">
          <div v-for="t in tracked" :key="t.job_id" style="margin:8px 0">
            <div class="row" style="justify-content:space-between;gap:10px">
              <span class="meta" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">
                🎬 {{ t.url.replace(/^https?:\/\/(www\.)?/, '') }}</span>
              <span class="meta" :style="t.status === 'dead' ? 'color:var(--red,#c0392b)' : ''">
                {{ jobLabel(t) }}</span>
            </div>
            <div class="meter" style="margin-top:3px">
              <div :style="{
                width: jobPct(t) + '%',
                transition: 'width .8s ease',
                background: t.status === 'dead' ? 'var(--red, #c0392b)' : undefined,
              }" />
            </div>
          </div>
          <div v-for="r in urlResults.results.filter(x => x.status === 'invalid')" :key="r.url" class="gate fail">
            ✖ {{ r.url }} — {{ r.note }}
          </div>
        </div>

        <div v-if="analyzed.length" style="margin-top:14px;border-top:1px solid var(--line);padding-top:10px">
          <h3 style="margin-bottom:2px">Analyzed posts</h3>
          <p class="hint" style="margin-bottom:0">
            Every URL-submitted post with what the system read in it — summary,
            topic, categories, score, and the full channel transcript. Add the
            ones you want to react to; they land on the 💬 Respond tab.
          </p>
          <ContentCard
            v-for="p in analyzed" :key="p.id"
            :post="p" density="full" :queued="rq.queuedIds.value.has(p.id)"
            @queue="rq.add(p.id)" @unqueue="rq.remove(p.id)"
          />
        </div>
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
    <!-- Community tipline form removed from the UI 2026-07-21 (owner decision:
         cleaner demo surface). POST /api/tipline and its tests stay — it is
         the only ingestion path for platforms without an acquisition adapter
         and the landing point for the parked Phase-2 messenger tipline. -->
  </div>
</template>
