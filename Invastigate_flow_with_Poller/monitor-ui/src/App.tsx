import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import LiveMonitor from './pages/LiveMonitor'
import TraceDetail from './pages/TraceDetail'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="live/:traceId" element={<LiveMonitor />} />
          <Route path="trace/:traceId" element={<TraceDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
