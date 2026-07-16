import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backend = env.VITE_DEV_BACKEND_URL || 'http://localhost:8765'

  return {
    // Built assets are served by the backend under /app/, so reference them
    // with that prefix. Dev keeps the root base for the Vite server.
    base: command === 'build' ? '/app/' : '/',
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/tasks': { target: backend, changeOrigin: true },
        '/healthz': { target: backend, changeOrigin: true },
        '/api': { target: backend, changeOrigin: true },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      css: true,
    },
  }
})
