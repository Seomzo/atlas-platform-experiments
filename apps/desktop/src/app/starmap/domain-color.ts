import type { Rgb } from './types'

const DOMAIN_COLORS = ['#56c8ff', '#42e3b4', '#f5b85b', '#7895ff', '#ff7f73', '#8ee7f2', '#a98cff', '#d6e46f'] as const

function hashDomain(value: string): number {
  let valueHash = 2166136261

  for (let index = 0; index < value.length; index += 1) {
    valueHash ^= value.charCodeAt(index)
    valueHash = Math.imul(valueHash, 16777619)
  }

  return valueHash >>> 0
}

export function domainColor(domain: string): string {
  return DOMAIN_COLORS[hashDomain(domain.trim().toLowerCase() || 'general') % DOMAIN_COLORS.length]!
}

export function domainRgb(domain: string): Rgb {
  const hex = domainColor(domain)

  return {
    r: Number.parseInt(hex.slice(1, 3), 16),
    g: Number.parseInt(hex.slice(3, 5), 16),
    b: Number.parseInt(hex.slice(5, 7), 16)
  }
}
