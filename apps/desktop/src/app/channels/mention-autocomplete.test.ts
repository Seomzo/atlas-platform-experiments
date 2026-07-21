import { describe, expect, it } from 'vitest'

import { filterMentionCandidates, mentionAutocompleteKey, nextMentionAutocompleteState } from './mention-autocomplete'

const candidates = [
  { name: 'Service Advisor', workerId: 'service-worker' },
  { name: 'Parts Manager', workerId: 'parts-worker' },
  { name: 'Fixed Ops', workerId: 'ops' }
]

describe('mention autocomplete', () => {
  it('filters members by display name or stable worker id', () => {
    expect(filterMentionCandidates(candidates, 'manager')).toEqual([candidates[1]])
    expect(filterMentionCandidates(candidates, 'service-worker')).toEqual([candidates[0]])
    expect(filterMentionCandidates(candidates, '')).toEqual(candidates)
  })

  it('wraps keyboard selection and selects with enter', () => {
    const opened = nextMentionAutocompleteState(null, '', 1)
    const up = mentionAutocompleteKey(opened, 'ArrowUp', candidates.length)

    expect(up).toMatchObject({ consumed: true, next: { activeIndex: 2 } })

    const down = mentionAutocompleteKey(up.next, 'ArrowDown', candidates.length)

    expect(down).toMatchObject({ consumed: true, next: { activeIndex: 0 } })
    expect(mentionAutocompleteKey(down.next, 'Enter', candidates.length)).toEqual({
      consumed: true,
      next: null,
      selectIndex: 0
    })
  })

  it('closes on escape without selecting and ignores navigation while closed', () => {
    const opened = nextMentionAutocompleteState(null, 'par', 4)

    expect(mentionAutocompleteKey(opened, 'Escape', 1)).toEqual({ consumed: true, next: null })
    expect(mentionAutocompleteKey(null, 'ArrowDown', 1)).toEqual({ consumed: false, next: null })
  })
})
