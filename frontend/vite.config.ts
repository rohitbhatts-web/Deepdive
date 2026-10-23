import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vitest/config'

// The dev server proxies API paths to the FastAPI backend, so the browser only ever talks to one origin and no key or
// backend URL is exposed to client code. Override the target with DEEPDIVE_API (a server-side variable, not VITE_*).
const api = process.env.DEEPDIVE_API ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  server: { proxy: { '/api': { target: api, changeOrigin: false, rewrite: (p) => p.replace(/^\/api/, '') } } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], globals: true, css: false },
})
