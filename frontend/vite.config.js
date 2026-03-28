import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/session': 'http://localhost:8000',
      '/upload':  'http://localhost:8000',
      '/chat':    'http://localhost:8000',
      '/context': 'http://localhost:8000',
      '/history': 'http://localhost:8000',
      '/health':  'http://localhost:8000',
    },
  },
})
