import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ProfileInfo } from '@/types/hermes'

import type { NebulaBrain } from './nebula'
import { BrainFilterChips } from './nebula-overview'

function profile(name: string, isDefault = false): ProfileInfo {
  return {
    display_name: isDefault ? 'Atlas' : 'Service',
    gateway_running: false,
    has_avatar: false,
    has_env: true,
    is_default: isDefault,
    model: null,
    name,
    path: `/tmp/${name}`,
    provider: null,
    role: isDefault ? 'main' : 'worker',
    skill_count: 0
  }
}

const brains: NebulaBrain[] = [
  { nodeIds: ['brain:default:one'], profile: profile('default', true), status: 'ready', totalNodeCount: 12 },
  { nodeIds: [], profile: profile('service'), status: 'empty', totalNodeCount: 0 }
]

afterEach(cleanup)

describe('nebula brain filter chips', () => {
  it('uses a chip click as a filter and a separate affordance as isolate', () => {
    const onFilter = vi.fn()
    const onIsolate = vi.fn()
    render(<BrainFilterChips brains={brains} onFilter={onFilter} onIsolate={onIsolate} selectedProfile={null} />)

    fireEvent.click(screen.getByRole('button', { name: 'Highlight Service and dim other brains' }))
    expect(onFilter).toHaveBeenLastCalledWith('service')
    expect(onIsolate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Open Service in the full brain view' }))
    expect(onIsolate).toHaveBeenLastCalledWith('service')

    fireEvent.doubleClick(screen.getByRole('button', { name: 'Highlight Service and dim other brains' }))
    expect(onIsolate).toHaveBeenLastCalledWith('service')

    fireEvent.click(screen.getByRole('button', { name: 'Show the whole sky' }))
    expect(onFilter).toHaveBeenLastCalledWith(null)
  })

  it('renders an honest no-memory state without a placeholder node', () => {
    const { container } = render(
      <BrainFilterChips brains={brains} onFilter={() => {}} onIsolate={() => {}} selectedProfile={null} />
    )

    expect(screen.getByText('No memory yet')).toBeTruthy()
    expect(container.querySelector('[data-brain-chip="service"]')?.getAttribute('data-no-memory')).toBe('true')
    expect(brains[1]?.nodeIds).toHaveLength(0)
  })
})
