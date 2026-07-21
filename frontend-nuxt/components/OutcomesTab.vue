<script setup lang="ts">
// 📈 Outcomes (M4.2): thread-climate deltas vs. the projected no-intervention
// baseline for every posted reply, plus the demo fast-forward button.
import type { FastForwardResult, OutcomesResponse, PostedOutcome } from '~/types/api'

const { api } = useApi()
const { guard, fmt } = useAppState()

const outcomes = ref<PostedOutcome[]>([])
const ffMsg = ref('')
const loading = ref(true)

onMounted(loadOutcomes)

async function loadOutcomes() {
  await guard(async () => {
    outcomes.value = (await api<OutcomesResponse>('/outcomes')).posted
  })
  loading.value = false
}

async function fastForward() {
  await guard(async () => {
    const r = await api<FastForwardResult>('/outcomes/fast-forward', { method: 'POST' })
    ffMsg.value = r.advanced
      ? `Advanced ${r.advanced} snapshot job(s); processed ${r.processed}.`
      : 'No snapshot jobs waiting — mark a reply as posted first.'
    await loadOutcomes()
  })
}
</script>

<template>
  <div class="card">
    <h2>📈 Outcomes — is it working?</h2>
    <p class="hint">After a reply is posted, the thread is snapshotted at <b>+1h / +24h / +72h</b>
      and compared against the projected <b>no-intervention baseline</b> — what the model expects
      had nobody replied. A green "thread climate Δ" means the conversation got friendlier than
      that baseline. We measure this, not reply volume (a volume KPI would reward a spam machine).
      Thread re-fetches are mocked in the prototype; production re-checks the real thread.</p>
    <div class="row" style="margin-bottom:12px">
      <button class="btn btn-ghost" @click="fastForward">⏩ Fast-forward time (demo)</button>
      <span class="meta">Runs the scheduled +1h/+24h/+72h snapshot jobs now instead of waiting.
        Production simply waits — this button exists so a demo doesn't take three days.</span>
    </div>
    <span v-if="ffMsg" class="meta">{{ ffMsg }}</span>
    <LoadingSpinner v-if="loading" />
    <div v-else-if="!outcomes.length" class="empty">No posted replies yet — complete the 💬 Respond flow
      (claim → rework → mark as posted) first.</div>
    <div v-for="p in outcomes" :key="p.id" class="item">
      <div class="row">
        <span class="pill pill-plat">reply #{{ p.id }}</span>
        <a :href="p.posted_url" target="_blank" rel="noopener noreferrer" class="meta">{{ p.posted_url }} ↗</a>
        <span class="meta">posted {{ p.posted_at }}</span>
      </div>
      <div v-if="!p.delta.offsets.length" class="meta" style="margin-top:6px">
        Snapshots scheduled — waiting for +1h/+24h/+72h (or use fast-forward above).</div>
      <template v-else>
        <table style="margin-top:8px">
          <tr><th>Snapshot</th><th>Thread climate Δ vs baseline</th><th>Thread views</th><th>Replies in thread</th></tr>
          <tr v-for="o in p.delta.offsets" :key="o.offset">
            <td>{{ o.offset }}</td>
            <td :style="{ color: o.sentiment_delta > 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }">
              {{ o.sentiment_delta > 0 ? '+' : '' }}{{ o.sentiment_delta }}</td>
            <td class="meta">{{ fmt(o.views) }}</td>
            <td class="meta">{{ o.reply_count }}</td>
          </tr>
        </table>
        <p class="meta" style="margin-top:6px">Overall:
          <b :style="{ color: (p.delta.overall_sentiment_delta ?? 0) > 0 ? 'var(--green)' : 'var(--red)' }">
            {{ (p.delta.overall_sentiment_delta ?? 0) > 0 ? '+' : '' }}{{ p.delta.overall_sentiment_delta }}</b>
          mean sentiment vs. the no-intervention baseline</p>
      </template>
    </div>
  </div>
</template>
