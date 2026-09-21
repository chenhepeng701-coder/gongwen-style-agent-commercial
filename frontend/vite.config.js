import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

  console.log(`[vite proxy] /api -> ${apiProxyTarget}`)

  return {
    plugins: [react()],
    build: {
      outDir: 'dist-commercial',
      emptyOutDir: false,
    },
    server: {
      port: 5177,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
