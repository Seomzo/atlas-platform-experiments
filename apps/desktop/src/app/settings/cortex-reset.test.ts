import { describe, expect, it } from 'vitest'

import { cortexSafeConfigDefaults } from './cortex-reset'

describe('cortexSafeConfigDefaults', () => {
  it('resets ordinary values while retaining both verified memory routes', () => {
    const result = cortexSafeConfigDefaults(
      {
        agent: { max_turns: 50 },
        auxiliary: {
          vision: { model: '', provider: 'auto' },
          cortex_triage: { model: 'atlas-cortex-memory', provider: 'altas' },
          cortex_reasoning: { model: 'atlas-cortex-memory', provider: 'altas' }
        },
        cortex: { enabled: false, recall: { max_items: 8 } },
        memory: { provider: '' }
      },
      {
        agent: { max_turns: 99 },
        auxiliary: {
          cortex_triage: {
            fallback_chain: [],
            model: 'google/gemini-3.1-flash-lite',
            provider: 'openrouter'
          },
          cortex_reasoning: {
            fallback_chain: [],
            model: 'google/gemini-3.1-flash-lite',
            provider: 'openrouter'
          }
        },
        cortex: { enabled: true, recall: { max_items: 30 } },
        memory: { provider: 'cortex' }
      }
    )

    expect(result).toMatchObject({
      agent: { max_turns: 50 },
      auxiliary: {
        vision: { model: '', provider: 'auto' },
        cortex_triage: { model: 'google/gemini-3.1-flash-lite', provider: 'openrouter' },
        cortex_reasoning: { model: 'google/gemini-3.1-flash-lite', provider: 'openrouter' }
      },
      cortex: { enabled: true, recall: { max_items: 8 } },
      memory: { provider: 'cortex' }
    })
  })

  it('refuses to manufacture a route when either current slot is missing', () => {
    expect(() =>
      cortexSafeConfigDefaults(
        { auxiliary: {}, cortex: { enabled: true }, memory: { provider: 'cortex' } },
        { auxiliary: { cortex_triage: { model: 'model', provider: 'openrouter' } } }
      )
    ).toThrow(/memory model is configured/i)
  })
})
