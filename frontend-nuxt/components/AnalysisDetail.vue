<script setup lang="ts">
// Shared renderer for a post's media analysis: the extraction channels
// (speech / on-screen text / caption) + the auditable evaluation chips.
// Used by the Radar narrative items and the "Analyzed media posts" section.
import type { EvalCategory, MediaExtraction, NarrativeEval } from '~/types/api'

defineProps<{ extraction: MediaExtraction | null; evaluation: NarrativeEval | null }>()

const CAT_SHORT: Record<EvalCategory, string> = {
  false_information: 'false info',
  illegal_content: 'illegal',
  fundamental_rights: 'fund. rights',
  civic_discourse: 'civic discourse',
  vulnerable_groups: 'vulnerable groups',
  pro_democracy: 'pro-democracy',
}

function flags(ev: NarrativeEval | null): { cat: EvalCategory; rationale: string }[] {
  if (!ev || ev.quarantined) return []
  return (Object.entries(ev.categories) as [EvalCategory, { applies: boolean; rationale: string }][])
    .filter(([, v]) => v.applies)
    .map(([cat, v]) => ({ cat, rationale: v.rationale }))
}
</script>

<template>
  <div>
    <div v-if="extraction" style="margin:6px 0">
      <div v-if="extraction.speech.length">
        <span class="meta"><b>🎙 Spoken</b> ({{ extraction.extractor }} extraction):</span>
        <p v-for="(seg, i) in extraction.speech" :key="i" class="txt" style="margin:2px 0">
          <span class="meta">{{ seg.start_s.toFixed(1) }}–{{ seg.end_s.toFixed(1) }}s</span> {{ seg.text }}
        </p>
      </div>
      <div v-else-if="!extraction.has_speech" class="meta">🎙 No speech detected (music/mute — nothing transcribed by design).</div>
      <div v-if="extraction.on_screen_text.length" style="margin-top:4px">
        <span class="meta"><b>🖼 On-screen text</b>:</span>
        <p v-for="(t, i) in extraction.on_screen_text" :key="i" class="txt" style="margin:2px 0">
          <span class="meta">
            {{ t.source.type === 'slide' ? `slide ${t.source.index}` : `${t.source.first_seen_s.toFixed(1)}–${t.source.last_seen_s.toFixed(1)}s` }}
          </span>
          {{ t.text }}
        </p>
      </div>
      <p v-if="extraction.caption" class="txt" style="margin:4px 0">
        <span class="meta"><b>✏️ Caption</b>:</span> {{ extraction.caption }}
      </p>
    </div>

    <div v-if="evaluation" class="row" style="gap:6px;flex-wrap:wrap;margin-top:4px">
      <span v-if="evaluation.quarantined" class="pill pill-warn">⚠ evaluation quarantined (invalid model output)</span>
      <span
        v-for="f in flags(evaluation)" :key="f.cat"
        class="pill" :class="f.cat === 'pro_democracy' ? 'pill-ok' : 'pill-warn'"
        :title="f.rationale"
      >{{ f.cat === 'pro_democracy' ? '💚' : '⚠' }} {{ CAT_SHORT[f.cat] }}</span>
      <span v-if="!evaluation.quarantined && !flags(evaluation).length" class="pill pill-ok">no category applies</span>
      <span class="meta" :title="'prompt sha256: ' + evaluation.prompt_sha256">
        evaluated with {{ evaluation.prompt_version }}
      </span>
    </div>
  </div>
</template>
