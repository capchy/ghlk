import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies API calls to the FastAPI backend, so the browser
// talks to one origin and no CORS setup is needed during development.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const target = env.VITE_PROXY_TARGET || 'http://localhost:8000'
  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          // Recharts is the bulk of the bundle and changes rarely; its own chunk stays cached across releases.
          manualChunks: {
            charts: ['recharts'],
          },
        },
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': target,
        '/health': target,
      },
    },
  }
})
