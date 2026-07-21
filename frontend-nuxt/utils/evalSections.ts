// Section order + labels for the evaluation categories. Harm sections first,
// pro-democracy last (visually separated as the "supportable" section).
// Shared by Radar (grouping the tracked narratives) and Studio (category
// picker in the add-narrative form). Nuxt auto-imports this from utils/.
import type { EvalCategory } from '~/types/api'

export const EVAL_SECTIONS: { key: EvalCategory; icon: string; title: string; hint: string }[] = [
  { key: 'false_information', icon: '❌', title: 'False & misleading narratives',
    hint: 'Match narratives already debunked by professional fact-checkers — the platform never issues its own verdicts.' },
  { key: 'illegal_content', icon: '⚖️', title: 'Illegal content',
    hint: 'Content illegal in Germany/the EU: incitement, banned symbols, credible threats.' },
  { key: 'hate_speech', icon: '🚫', title: 'Hate speech',
    hint: 'Dehumanizing language, slurs, degrading insults — flagged when present in content, whether the post endorses or criticizes it.' },
  { key: 'fundamental_rights', icon: '🕊️', title: 'Against fundamental rights',
    hint: 'Attacks on human dignity, equal treatment, press freedom and other fundamental rights.' },
  { key: 'civic_discourse', icon: '🗳️', title: 'Against civic discourse',
    hint: 'Delegitimizing elections without evidence, discouraging participation, intimidation.' },
  { key: 'vulnerable_groups', icon: '🛡️', title: 'Minors, minorities & public health',
    hint: 'Content threatening the protection of minors, minorities or public health.' },
  { key: 'pro_democracy', icon: '💚', title: 'Pro-democracy narratives',
    hint: 'Ideas creators can actively SUPPORT: nonpartisan civic information, participation, media literacy.' },
]
