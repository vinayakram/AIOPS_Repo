// Application shell component providing the top navigation bar and page content outlet.
// Includes a language toggle button (EN/JP) that persists the selection to localStorage.
// Includes a dark/light theme toggle that persists the selection to localStorage via a CSS class on documentElement.
//
// トップナビゲーションバーとページコンテンツアウトレットを提供するアプリケーションシェルコンポーネント。
// 選択をlocalStorageに永続化する言語切替ボタン（EN/JP）を含む。
// documentElementのCSSクラスを介してlocalStorageに選択を永続化するダーク/ライトテーマトグルを含む。
import { useEffect, useState } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import { useLang } from '../LanguageContext'
import { t } from '../i18n'

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  )
}

export default function Layout() {
  const { lang, setLang } = useLang()

  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('theme') as 'dark' | 'light') ?? 'dark'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light')
    localStorage.setItem('theme', theme)
  }, [theme])

  return (
    <div className="layout">
      <nav className="navbar">
        <div className="navbar-brand">
          <span>🔍</span>
          <span>{t('appTitle', lang)}</span>
        </div>
        <div className="navbar-links">
          <NavLink
            to="/"
            end
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            {t('dashboard', lang)}
          </NavLink>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {/* Language toggle */}
          <button
            onClick={() => setLang(lang === 'en' ? 'ja' : 'en')}
            title={lang === 'en' ? 'Switch to Japanese' : '英語に切り替え'}
            style={{
              background: 'var(--surface2)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              padding: '4px 10px',
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--text)',
              letterSpacing: '0.03em',
            }}
          >
            {lang === 'en' ? 'JP' : 'EN'}
          </button>

          {/* Theme toggle */}
          <button
            className="theme-toggle"
            onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            title={theme === 'dark' ? t('switchLight', lang) : t('switchDark', lang)}
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
      </nav>
      <main className="page">
        <Outlet />
      </main>
    </div>
  )
}
