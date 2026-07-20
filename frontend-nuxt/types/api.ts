/** Typed mirrors of what app/main.py actually returns — keep these honest:
 *  when an endpoint's shape changes, change it here in the same PR. */

export type Role = 'admin' | 'staff' | 'responder'

/** POST /api/login (and the localStorage-persisted session). */
export interface Session {
  token: string
  username: string
  role: Role
}

/** config.as_dict() — the active thresholds shown in the footer. */
export interface AppConfig {
  amplification_floor_views: number
  score_threshold: number
  opportunity_expiry_hours: number
  edit_gate_min_pct: number
  dwell_min_seconds: number
  rate_cap_per_day: number
  copypasta_jaccard_threshold: number
}

/** GET /api/state */
export interface State {
  ai_mode: string
  counts: { items: number; opportunities: number; posted: number }
  config?: AppConfig
  user?: { username: string; role: Role } | null
}

/** One content_items row inside a radar narrative (subset the UI uses). */
export interface NarrativeItem {
  id: number
  platform: string
  author_pseudo: string
  author_followers_band: string | null
  text: string
  text_en: string | null
  url: string | null
  views: number
  score_json: string | null
}

/** GET /api/radar — one narrative with its top items. */
export interface Narrative {
  code: string
  label_de: string
  label_en: string
  description_en: string
  post_count: number
  total_views: number
  growth_pct: number
  items: NarrativeItem[]
}

/** GET /api/opportunities — opportunities row joined with item + fact-check. */
export interface Opportunity {
  id: number
  item_id: number
  kind: string
  score: number
  score_breakdown: string
  status: 'open' | 'claimed' | 'posted' | 'dismissed' | 'expired'
  claimed_by: string | null
  claimed_at: string | null
  expires_at: string | null
  text: string
  text_en: string | null
  item_url: string | null
  views: number
  platform: string
  author_followers_band: string | null
  claim_id: number | null
  fact_checker: string | null
  verdict: string | null
  fc_url: string | null
  claim_en: string | null
}

/** POST /api/opportunities/{id}/draft */
export interface Draft {
  draft_id: number
  ai_seed: string
}

export interface GateCheck {
  name: string
  passed: boolean
  detail: string
}

/** PUT /api/drafts/{id} — full gate evaluation. */
export interface GateResult {
  edit_distance_pct: number
  post_ready: boolean
  checks: GateCheck[]
}

/** A claims row (published fact-checks only — never generated). */
export interface Claim {
  id: number
  claim_de: string | null
  claim_en: string | null
  verdict: string | null
  fact_checker: string | null
  url: string | null
  narrative_code: string | null
  published_at: string | null
}

/** POST /api/briefs/{code} */
export interface Brief {
  narrative: Pick<Narrative, 'code' | 'label_de' | 'label_en' | 'description_en'>
  brief: {
    summary_en: string
    summary_de: string
    key_facts: string[]
    angles: string[]
    avoid: string[]
  }
  factchecks: Claim[]
}

/** GET /api/audit — one audit_log row. */
export interface AuditEntry {
  id: number
  created_at: string
  actor: string
  action: string
  detail: string
}

export interface DeadJob {
  id: number
  payload: string
  attempts: number
  last_error: string | null
  updated_at: string
}

/** GET /api/queue */
export interface QueueStatus {
  depth: { queued: number; running: number; done: number; dead: number }
  dead: DeadJob[]
}

/** POST /api/pipeline/run */
export interface PipelineLog {
  triage_kept: number
  triage_dropped: number
  classified: number
  claim_matched: number
  opportunities: number
  mode: string
  jobs_processed: number
  pending_before: number
  note?: string
}

/** POST /api/ingest/{source} */
export interface IngestResult {
  source: string
  new_items: number
  enqueued: number
}

/** POST /api/import/zeeschuimer */
export interface ImportResult {
  parsed: number
  new_items: number
  enqueued: number
  skipped_lines: number
}

/** POST /api/sync/factchecks */
export interface FactcheckSync {
  mode: string
  upserted?: number
  queried_keywords?: number
  note?: string
}

export interface OutcomeOffset {
  offset: string
  sentiment_delta: number
  views: number
  reply_count: number
}

export interface OutcomeDelta {
  offsets: OutcomeOffset[]
  overall_sentiment_delta: number | null
}

/** One posted reply with its snapshots + climate delta (GET /api/outcomes). */
export interface PostedOutcome {
  id: number
  draft_id: number
  posted_url: string
  posted_at: string
  snapshots: Record<string, unknown>[]
  delta: OutcomeDelta
}

/** GET /api/outcomes */
export interface OutcomesResponse {
  posted: PostedOutcome[]
  note: string
}

/** POST /api/outcomes/fast-forward */
export interface FastForwardResult {
  advanced: number
  processed: number
}
