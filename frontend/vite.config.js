import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端地址与 dev 端口可由环境变量覆盖（start.ps1 在换端口运行时会注入），
// 默认仍是 FastAPI 的 8000 / Vite 的 5173。
// 为什么要环境变量：同机并行的另一个项目默认也可能占用 8000，
// 两个项目并行跑时需要把本项目整体挪到别的端口，代理目标必须跟着一起挪。
const BACKEND = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'
const DEV_PORT = Number(process.env.VITE_DEV_PORT || 5173)

export default defineConfig({
  plugins: [react()],
  server: {
    port: DEV_PORT,
    // 端口被占时直接报错，不要自动改用 5174：
    // 否则启动脚本打印的地址与实际页面不一致，用户会打开到别的项目上。
    strictPort: true,
    host: '127.0.0.1',
    proxy: {
      // 业务接口
      '/api': { target: BACKEND, changeOrigin: true },
      // TTS 音频缓存目录（必须代理，否则 <audio src="/static/..."> 会 404）
      '/static': { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
  },
})
