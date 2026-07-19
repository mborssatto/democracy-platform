import type { Session } from '~/types/api'

const STORAGE_KEY = 'dp_session'

/** Session state from the dev auth stub ({token, username, role}), persisted
 *  in localStorage exactly like the old zero-build page — the two UIs can even
 *  share a session. Production swaps this for Supabase Auth JWTs. */
export function useSession() {
  const session = useState<Session | null>('session', () => {
    // ssr: false → this initializer only ever runs in the browser.
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    } catch {
      return null
    }
  })

  function save(s: Session) {
    session.value = s
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
  }

  function clear() {
    session.value = null
    localStorage.removeItem(STORAGE_KEY)
  }

  return { session, save, clear }
}
