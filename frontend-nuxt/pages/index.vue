<script setup lang="ts">
// The shell: header (mode badge, language toggle, role-gated nav, session
// info), the error banner, the login card, and one component per tab.
// Tab components are v-if-mounted, so each loads its own data on mount —
// the same "load on tab click" behavior as the old page.
import type { Session } from '~/types/api'

type Tab = 'setup' | 'radar' | 'respond' | 'studio' | 'outcomes' | 'audit'

const { session, save, clear } = useSession()
const { api } = useApi()
const { state, lang, err, guard, refreshState, fmt } = useAppState()

const tab = useState<Tab>('tab', () => 'setup')

onMounted(() => {
  // Responders land on the only flow they can use.
  if (session.value?.role === 'responder') tab.value = 'respond'
  refreshState()
})

// Whenever the session goes away (logout, 401, server restart) reset the tab
// so the next login starts on Data — mirrors the old clearSession().
watch(session, (s: Session | null) => {
  if (!s) tab.value = 'setup'
})

async function loginAs(username: string) {
  await guard(async () => {
    const r = await api<Session>('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    })
    save(r)
    tab.value = r.role === 'responder' ? 'respond' : 'setup'
    refreshState()
  })
}

async function logout() {
  try {
    await api('/logout', { method: 'POST' })
  } catch {
    // best effort — clear the local session either way
  }
  clear()
}
</script>

<template>
  <div>
    <header>
      <h1>🛡️ Democracy Platform <span style="color:var(--muted);font-weight:400">prototype</span></h1>
      <span class="mode" :class="state.ai_mode === 'mock' ? 'mock' : 'real'">AI: {{ state.ai_mode }}</span>
      <button class="lang" @click="lang = lang === 'de' ? 'en' : 'de'">
        {{ lang === 'de' ? '🇬🇧 View in English' : '🇩🇪 Original (Deutsch)' }}
      </button>
      <nav v-if="session">
        <!-- Responders only get the Respond flow; the other tabs are staff/admin
             (the API enforces this server-side too — hiding is just UX). -->
        <template v-if="session.role !== 'responder'">
          <button :class="{ active: tab === 'setup' }" @click="tab = 'setup'">⚙️ Data</button>
          <button :class="{ active: tab === 'radar' }" @click="tab = 'radar'">📡 Radar</button>
        </template>
        <button :class="{ active: tab === 'respond' }" @click="tab = 'respond'">💬 Respond</button>
        <template v-if="session.role !== 'responder'">
          <button :class="{ active: tab === 'studio' }" @click="tab = 'studio'">🎨 Studio</button>
          <button :class="{ active: tab === 'outcomes' }" @click="tab = 'outcomes'">📈 Outcomes</button>
          <button :class="{ active: tab === 'audit' }" @click="tab = 'audit'">📋 Audit</button>
        </template>
      </nav>
      <span v-if="session" class="meta" style="margin-left:auto;white-space:nowrap">
        logged in as <b>{{ session.username }}</b> ({{ session.role }}) ·
        <a href="#" style="color:var(--blue)" @click.prevent="logout">logout</a>
      </span>
    </header>
    <main>
      <div v-if="err" class="banner" style="background:var(--red-soft);border-color:#f0caca;color:var(--red)">
        ⚠ {{ err }}
        <button class="btn btn-ghost" style="padding:2px 10px;margin-left:8px" @click="err = ''">Dismiss</button>
      </div>

      <LoginCard v-if="!session" @login="loginAs" />

      <template v-if="session">
        <DataTab v-if="tab === 'setup'" />
        <RadarTab v-if="tab === 'radar'" />
        <RespondTab v-if="tab === 'respond'" />
        <StudioTab v-if="tab === 'studio'" />
        <OutcomesTab v-if="tab === 'outcomes'" />
        <AuditTab v-if="tab === 'audit'" />

        <!-- Active thresholds (from app/config.py — env-overridable, see .env.example) -->
        <p v-if="state.config" class="meta" style="text-align:right;margin-top:20px;padding-right:8px">
          Active thresholds: floor {{ fmt(state.config.amplification_floor_views) }} views ·
          score ≥{{ state.config.score_threshold }} ·
          edit ≥{{ state.config.edit_gate_min_pct }}% ·
          dwell {{ state.config.dwell_min_seconds }}s ·
          cap {{ state.config.rate_cap_per_day }}/day ·
          copypasta ≥{{ Math.round(state.config.copypasta_jaccard_threshold * 100) }}% ·
          expiry {{ state.config.opportunity_expiry_hours }}h
        </p>
      </template>
    </main>
  </div>
</template>
