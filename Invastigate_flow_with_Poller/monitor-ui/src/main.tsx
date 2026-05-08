// React application entry point that mounts the root App component into the DOM.
// Wraps the application in StrictMode to surface potential issues during development.
// Imports the global CSS stylesheet before rendering to ensure styles are applied at startup.
//
// ルートAppコンポーネントをDOMにマウントするReactアプリケーションエントリーポイント。
// 開発中の潜在的な問題を表面化するためにStrictModeでアプリケーションをラップする。
// 起動時にスタイルが適用されるようにレンダリング前にグローバルCSSスタイルシートをインポートする。
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
