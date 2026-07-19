/** Error carrying the HTTP status, so callers can react to 401 (expired
 *  session) differently from other failures. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

/** Typed port of the old page's `api()` helper: plain fetch against /api,
 *  bearer token from the stored session, FastAPI `detail` extraction
 *  (string or pydantic validation array) into a readable Error message. */
export function useApi() {
  const { session } = useSession()

  async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      ...(opts.headers as Record<string, string> | undefined),
    }
    if (session.value?.token) headers.Authorization = `Bearer ${session.value.token}`
    let r: Response
    try {
      r = await fetch('/api' + path, { ...opts, headers })
    } catch {
      throw new Error('Cannot reach the server — is it still running?')
    }
    const body = await r.json().catch(() => ({}))
    if (!r.ok) {
      const d = (body as { detail?: unknown }).detail
      const msg =
        typeof d === 'string'
          ? d
          : Array.isArray(d)
            ? d.map((x: { msg: string }) => x.msg).join('; ')
            : `Request failed (${r.status})`
      throw new ApiError(msg, r.status)
    }
    return body as T
  }

  return { api }
}
