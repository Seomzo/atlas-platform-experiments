export interface MentionCandidate {
  name: string
  workerId: string
}

export interface MentionAutocompleteState {
  activeIndex: number
  query: string
  tokenLength: number
}

export interface MentionAutocompleteKeyResult {
  consumed: boolean
  next: MentionAutocompleteState | null
  selectIndex?: number
}

export function filterMentionCandidates<T extends MentionCandidate>(candidates: readonly T[], query: string): T[] {
  const needle = query.trim().toLocaleLowerCase()

  if (!needle) {
    return [...candidates]
  }

  return candidates.filter(candidate => `${candidate.name}\n${candidate.workerId}`.toLocaleLowerCase().includes(needle))
}

export function nextMentionAutocompleteState(
  current: MentionAutocompleteState | null,
  query: string,
  tokenLength: number
): MentionAutocompleteState {
  return {
    activeIndex: current?.query === query ? current.activeIndex : 0,
    query,
    tokenLength
  }
}

export function mentionAutocompleteKey(
  state: MentionAutocompleteState | null,
  key: string,
  itemCount: number
): MentionAutocompleteKeyResult {
  if (!state) {
    return { consumed: false, next: null }
  }

  if (key === 'Escape') {
    return { consumed: true, next: null }
  }

  if ((key === 'Enter' || key === 'Tab') && itemCount > 0) {
    return {
      consumed: true,
      next: null,
      selectIndex: Math.min(state.activeIndex, itemCount - 1)
    }
  }

  if ((key === 'ArrowDown' || key === 'ArrowUp') && itemCount > 0) {
    const direction = key === 'ArrowDown' ? 1 : -1

    return {
      consumed: true,
      next: { ...state, activeIndex: (state.activeIndex + direction + itemCount) % itemCount }
    }
  }

  return { consumed: false, next: state }
}
