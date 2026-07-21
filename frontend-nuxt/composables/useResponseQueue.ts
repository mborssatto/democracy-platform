/** The per-user response queue, shared across tabs (Data/Radar add to it,
 *  Respond works through it). State lives in useState so tab switches keep it;
 *  each consumer calls load() on mount to stay fresh. No claiming — several
 *  users may queue and draft the same item (owner decision 2026-07-21). */
import type { QueueEntry } from '~/types/api'

export function useResponseQueue() {
  const { api } = useApi()
  const { guard } = useAppState()
  const entries = useState<QueueEntry[]>('response-queue', () => [])
  const loading = useState<boolean>('response-queue-loading', () => true)

  const queuedIds = computed(() => new Set(entries.value.map(e => e.id)))

  async function load() {
    try {
      entries.value = await api<QueueEntry[]>('/response-queue')
    } catch {
      /* silent — queue badge is non-critical; guard() paths surface errors */
    } finally {
      loading.value = false
    }
  }

  async function add(itemId: number) {
    // guard() so a floor rejection (409) shows its explanation in the banner.
    await guard(async () => {
      await api('/response-queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_id: itemId }),
      })
      await load()
    })
  }

  async function remove(itemId: number) {
    await guard(async () => {
      await api(`/response-queue/${itemId}`, { method: 'DELETE' })
      await load()
    })
  }

  return { entries, queuedIds, loading, load, add, remove }
}
