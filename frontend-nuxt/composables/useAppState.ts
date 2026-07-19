import type { State } from '~/types/api'
import { ApiError } from './useApi'

/** Shell state shared by every tab: server /api/state snapshot, language
 *  toggle, and the guard()/fail() error-banner pattern from the old page —
 *  every user action runs through guard() so failures become a dismissible
 *  banner instead of a silent console error or a broken view. */
export function useAppState() {
  const state = useState<State>('app-state', () => ({
    ai_mode: '…',
    counts: { items: 0, opportunities: 0, posted: 0 },
  }))
  const lang = useState<'de' | 'en'>('lang', () => 'de')
  const err = useState<string>('err', () => '')
  const { session, clear } = useSession()
  const { api } = useApi()

  function fail(e: unknown) {
    err.value = e instanceof Error ? e.message : String(e)
    if (e instanceof ApiError && e.status === 401) {
      clear() // token no longer valid → back to the login card
      err.value = 'Session expired — please log in again.'
    }
  }

  async function guard<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try {
      err.value = ''
      return await fn()
    } catch (e) {
      fail(e)
    }
  }

  async function refreshState() {
    await guard(async () => {
      state.value = await api<State>('/state')
      // /api/state echoes the user for a valid token; a stored token the
      // server no longer recognizes (restart, expiry) means log in again.
      if (session.value && !state.value.user) clear()
    })
  }

  /** 12345 → "12.3k" (same formatting as the old page). */
  function fmt(n: number | null | undefined): string {
    if (n == null) return ''
    return n >= 1000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + 'k' : String(n)
  }

  return { state, lang, err, guard, fail, refreshState, fmt }
}
