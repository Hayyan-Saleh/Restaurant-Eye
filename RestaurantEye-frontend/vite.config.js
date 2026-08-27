import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from "@tailwindcss/vite"
import path from "path"
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src")
    }
  },
  server: {
    proxy: {
      // Same-origin for MJPEG so the probe can read X-Stream-Source
      "/api/v1/video": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      // Same-origin for the /ws/dashboard WebSocket (CAMERA_STREAM_* events)
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
        changeOrigin: true,
      }
    }
  }
})
