// React context providing the active language state and setter for bilingual EN/JA UI support.
// LanguageProvider persists the selected language to localStorage so the preference survives page reloads.
// The useLang() hook gives any component access to the current language and the setLang dispatcher.
// The pick() helper selects the Japanese value when lang=ja and the field exists, otherwise returns English.
//
// バイリンガルEN/JA UIサポートのためのアクティブ言語状態とセッターを提供するReactコンテキスト。
// LanguageProviderは選択された言語をlocalStorageに永続化してページリロード後も設定が保持される。
// useLang()フックにより任意のコンポーネントが現在の言語とsetLangディスパッチャーにアクセスできる。
// pick()ヘルパーはlang=jaかつフィールドが存在する場合に日本語の値を選択し、それ以外は英語を返す。
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
