import { describe, expect, it } from 'vitest'

import type { CortexMemoryModelOptionsResponse } from '@/types/hermes'

import { selectableCortexMemoryProviders } from './cortex-memory-options'

const baseOptions = {
  recommended: { provider: '', model: '' },
  current: {
    configured: false,
    valid: false,
    triage: {
      provider: '',
      model: '',
      configured: false,
      valid: false,
      runtime_valid: false,
      catalog_selectable: false,
      advanced_route: false,
      unavailable_reason: 'required'
    },
    reasoning: {
      provider: '',
      model: '',
      configured: false,
      valid: false,
      runtime_valid: false,
      catalog_selectable: false,
      advanced_route: false,
      unavailable_reason: 'required'
    }
  },
  tasks: ['cortex_triage', 'cortex_reasoning'],
  constraints: {
    explicit_route_required: true,
    separate_from_main: true,
    structured_json_required: true,
    tool_calling_required: false
  }
} satisfies Omit<CortexMemoryModelOptionsResponse, 'providers'>

describe('selectableCortexMemoryProviders', () => {
  it('keeps only authenticated models with verified selectable structured output', () => {
    const providers = selectableCortexMemoryProviders({
      ...baseOptions,
      providers: [
        {
          name: 'Unauthenticated',
          slug: 'unauthenticated',
          authenticated: false,
          models: ['memory-ready'],
          memory_capabilities: {
            'memory-ready': {
              recommendation_rank: 1,
              recommended: true,
              selectable: true,
              structured_json: true,
              structured_json_validation: 'curated',
              tool_calling_required: false,
              unavailable_reason: ''
            }
          }
        },
        {
          name: 'Authenticated',
          slug: 'authenticated',
          authenticated: true,
          models: ['memory-ready', 'plain-chat', 'missing-metadata'],
          memory_capabilities: {
            'memory-ready': {
              recommendation_rank: 1,
              recommended: true,
              selectable: true,
              structured_json: true,
              structured_json_validation: 'curated',
              tool_calling_required: false,
              unavailable_reason: ''
            },
            'plain-chat': {
              recommendation_rank: 2,
              recommended: false,
              selectable: false,
              structured_json: false,
              structured_json_validation: 'curated',
              tool_calling_required: false,
              unavailable_reason: 'structured JSON is not advertised'
            }
          }
        }
      ]
    })

    expect(providers).toHaveLength(1)
    expect(providers[0]?.slug).toBe('authenticated')
    expect(providers[0]?.models).toEqual(['memory-ready'])
  })
})
