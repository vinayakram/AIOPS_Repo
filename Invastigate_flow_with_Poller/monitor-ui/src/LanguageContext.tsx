import { createContext, useContext, useState, type ReactNode } from 'react'
import type { Lang } from './i18n'

interface LanguageContextValue {
  lang: Lang
  setLang: (l: Lang) => void
}

const LanguageContext = createContext<LanguageContextValue>({
  lang: 'en',
  setLang: () => {},
})

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    return (localStorage.getItem('lang') as Lang) ?? 'en'
  })

  function setLang(l: Lang) {
    setLangState(l)
    localStorage.setItem('lang', l)
  }

  return (
    <LanguageContext.Provider value={{ lang, setLang }}>
      {children}
    </LanguageContext.Provider>
  )
}

export function useLang() {
  return useContext(LanguageContext)
}

/** Pick the localised value: prefer _ja when lang=ja and the field exists, else fall back. */
export function pick(en: string | undefined, ja: string | null | undefined, lang: Lang): string {
  if (lang === 'ja' && ja) return ja
  return en ?? ''
}
