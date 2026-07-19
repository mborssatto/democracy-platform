// Nuxt is dev/build tooling only. `npm run generate` emits a static SPA into
// .output/public, which FastAPI serves at / (see app/main.py). At runtime there
// is no Node server — same deployment model as the old zero-build page.
//
// `npm run dev` (HMR) proxies /api and /static to a running FastAPI backend.
// Default target is http://localhost:8400; override with e.g.
//   NUXT_API_PROXY=http://localhost:8410 npm run dev
const apiProxy = process.env.NUXT_API_PROXY || 'http://localhost:8400'

export default defineNuxtConfig({
  ssr: false, // SPA: the API is the only backend; no server-side rendering
  compatibilityDate: '2025-07-01',
  devtools: { enabled: false },
  css: ['~/assets/main.css'],
  app: {
    head: {
      title: 'Democracy Platform — Prototype',
      htmlAttrs: { lang: 'en' },
      meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1.0' }],
    },
  },
  nitro: {
    devProxy: {
      '/api': { target: `${apiProxy}/api`, changeOrigin: true },
      '/static': { target: `${apiProxy}/static`, changeOrigin: true },
    },
  },
})
