<script setup lang="ts">
// 📋 Audit: the append-only compliance evidence chain.
import type { AuditEntry } from '~/types/api'

const { api } = useApi()
const { guard } = useAppState()

const audit = ref<AuditEntry[]>([])
const loading = ref(true)

onMounted(async () => {
  await guard(async () => {
    audit.value = await api<AuditEntry[]>('/audit')
  })
  loading.value = false
})
</script>

<template>
  <div class="card">
    <h2>📋 Audit log (append-only)</h2>
    <p class="hint">Every ingestion, pipeline run, gate decision, and vault access is recorded. This is the compliance evidence chain.</p>
    <LoadingSpinner v-if="loading" />
    <table v-else>
      <tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr>
      <tr v-for="a in audit" :key="a.id">
        <td class="meta">{{ a.created_at }}</td><td>{{ a.actor }}</td>
        <td><b>{{ a.action }}</b></td><td class="meta">{{ a.detail }}</td>
      </tr>
    </table>
  </div>
</template>
