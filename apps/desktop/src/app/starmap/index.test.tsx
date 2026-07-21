// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type * as HermesApi from '@/hermes'

const getProfiles = vi.hoisted(() => vi.fn())

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof HermesApi>()),
  getProfiles
}))

vi.mock('@/store/starmap', async () => {
  const { atom } = await import('nanostores')

  return {
    $starmapBrainProfile: atom('default'),
    $starmapBrainStatus: atom('ready'),
    $starmapConstellation: atom({}),
    $starmapError: atom(null),
    $starmapGraph: atom(null),
    $starmapLoading: atom(false),
    $starmapMode: atom('constellation'),
    selectStarmapBrain: vi.fn(),
    showStarmapConstellation: vi.fn()
  }
})

vi.mock('./constellation-overview', () => ({
  ConstellationOverview: () => <div data-testid="constellation" />
}))

import { StarmapView } from './index'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function NavigationHarness() {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <>
      <StarmapView onClose={() => navigate('/')} />
      <output data-testid="location">{location.pathname}</output>
    </>
  )
}

describe('StarmapView overlay chrome', () => {
  it('renders the standard close control above the constellation and navigates back to chat', async () => {
    getProfiles.mockResolvedValue({ profiles: [] })

    render(
      <MemoryRouter initialEntries={['/starmap']}>
        <NavigationHarness />
      </MemoryRouter>
    )

    const close = await screen.findByRole('button', { name: 'Close memory graph' })

    expect(close.parentElement?.className).toContain('z-40')
    fireEvent.click(close)
    expect(screen.getByTestId('location').textContent).toBe('/')
  })
})
