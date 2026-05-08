// Root React application component that configures routing and global context providers.
// Wraps the entire app in LanguageProvider for bilingual EN/JA support and BrowserRouter for navigation.
// Defines three routes: Dashboard (index), LiveMonitor (/live/:traceId), and TraceDetail (/trace/:traceId).
//
// ルーティングとグローバルコンテキストプロバイダーを設定するReactアプリケーションのルートコンポーネント。
// バイリンガルEN/JAサポートのLanguageProviderとナビゲーション用BrowserRouterでアプリ全体をラップする。
// Dashboard（インデックス）、LiveMonitor（/live/:traceId）、TraceDetail（/trace/:traceId）の3つのルートを定義する。
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { LanguageProvider } from './LanguageContext'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import LiveMonitor from './pages/LiveMonitor'
import TraceDetail from './pages/TraceDetail'

export default function App() {
  return (
    <LanguageProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="live/:traceId" element={<LiveMonitor />} />
            <Route path="trace/:traceId" element={<TraceDetail />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </LanguageProvider>
  )
}
