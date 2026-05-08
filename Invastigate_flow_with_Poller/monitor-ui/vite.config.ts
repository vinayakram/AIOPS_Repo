// Vite build configuration for the monitor-ui React application.
// Registers the official React plugin for JSX transform and fast refresh during development.
// Configures a dev server on port 5173 with a proxy rule forwarding /api requests to localhost:8000.
//
// monitor-ui Reactアプリケーション用のViteビルド設定。
// 開発中のJSXトランスフォームとfast refreshのための公式Reactプラグインを登録する。
// /apiリクエストをlocalhost:8000に転送するプロキシルールを設定したポート5173の開発サーバーを構成する。
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
