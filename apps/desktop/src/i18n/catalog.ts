import { en } from './en'
import { ja } from './ja'
import type { Locale, Translations } from './types'
import { zh } from './zh'
import { zhHant } from './zh-hant'

const atlasText = (value: string): string =>
  value
    .replaceAll('Hermes Desktop', 'Atlas Desktop')
    .replaceAll('Hermes Agent', 'Atlas')
    .replaceAll('Hermes', 'Atlas')
    .replaceAll('hermes://', 'atlas://')
    .replaceAll('~/.hermes', '~/.atlas')

const atlasTranslations = (value: unknown): unknown => {
  if (typeof value === 'string') {
    return atlasText(value)
  }

  if (typeof value === 'function') {
    return (...args: unknown[]) => atlasTranslations(value(...args))
  }

  if (Array.isArray(value)) {
    return value.map(atlasTranslations)
  }

  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, atlasTranslations(entry)]))
  }

  return value
}

export const TRANSLATIONS: Record<Locale, Translations> = {
  en: atlasTranslations(en) as Translations,
  zh: atlasTranslations(zh) as Translations,
  'zh-hant': atlasTranslations(zhHant) as Translations,
  ja: atlasTranslations(ja) as Translations
}
