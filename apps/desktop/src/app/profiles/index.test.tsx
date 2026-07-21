// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as HermesApi from '@/hermes'
import type { ProfileInfo } from '@/types/hermes'

const getProfiles = vi.fn()
const getProfileIdentity = vi.fn()
const getProfileSoul = vi.fn()

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof HermesApi>()),
  getProfiles: () => getProfiles(),
  getProfileIdentity: (name: string) => getProfileIdentity(name),
  getProfileSoul: (name: string) => getProfileSoul(name)
}))

vi.mock('@/components/chat/code-editor', () => ({
  CodeEditor: () => <div data-testid="soul-editor" />
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

function worker(patch: Partial<ProfileInfo> = {}): ProfileInfo {
  return {
    display_name: 'Riley',
    gateway_running: false,
    has_avatar: false,
    has_env: false,
    is_default: false,
    model: 'claude-sonnet-4-5',
    name: 'service-advisor',
    path: '/tmp/atlas/workers/service-advisor',
    provider: 'anthropic',
    role: 'Service Advisor',
    skill_count: 7,
    ...patch
  }
}

beforeEach(async () => {
  const { $activeGatewayProfile } = await import('@/store/profile')

  $activeGatewayProfile.set('default')
  getProfiles.mockResolvedValue({
    profiles: [
      worker({
        display_name: 'Atlas',
        gateway_running: true,
        is_default: true,
        model: 'gpt-5.4',
        name: 'default',
        path: '/tmp/atlas',
        provider: 'openai-codex',
        role: 'Operations Lead',
        skill_count: 12
      }),
      worker()
    ]
  })
  getProfileIdentity.mockImplementation(async (name: string) => ({
    avatar: null,
    display_name: name === 'default' ? 'Atlas' : 'Riley',
    role: name === 'default' ? 'Operations Lead' : 'Service Advisor',
    tagline: ''
  }))
  getProfileSoul.mockResolvedValue({ content: '', exists: false })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderWorkers() {
  const { ProfilesView } = await import('./index')

  return render(
    <MemoryRouter initialEntries={['/profiles']}>
      <ProfilesView />
    </MemoryRouter>
  )
}

describe('Workers page', () => {
  it('renders the worker roster, current indicator, and endpoint-backed detail summary', async () => {
    await renderWorkers()

    await waitFor(() => expect(document.querySelector('[data-worker-row="default"]')).not.toBeNull())
    const defaultRow = document.querySelector('[data-worker-row="default"]')

    expect(defaultRow?.textContent).toContain('Atlas')
    expect(defaultRow?.textContent).toContain('Operations Lead')
    expect(defaultRow?.textContent).toContain('Current')

    const serviceRow = document.querySelector('[data-worker-row="service-advisor"]')
    const selectWorker = serviceRow?.querySelector('button')

    expect(selectWorker).not.toBeNull()
    fireEvent.click(selectWorker!)

    expect(await screen.findByText('claude-sonnet-4-5')).toBeTruthy()
    expect(screen.getByText('7 skills')).toBeTruthy()
    expect(screen.getByText('/tmp/atlas/workers/service-advisor')).toBeTruthy()
    expect(screen.getAllByText('Standby').length).toBeGreaterThan(0)
  })

  it('filters the roster while keeping worker language in the empty state', async () => {
    await renderWorkers()

    const search = await screen.findByRole('textbox', { name: 'Search workers...' })
    fireEvent.change(search, { target: { value: 'missing' } })

    expect(await screen.findByText('No workers match “missing”.')).toBeTruthy()
    expect(document.querySelectorAll('[data-worker-row]').length).toBe(0)
  })

  it('opens the shared create flow and exposes management actions for named workers', async () => {
    await renderWorkers()

    fireEvent.click(await screen.findByRole('button', { name: 'New worker' }))

    const dialog = await screen.findByRole('dialog', { name: 'New worker' })

    expect(within(dialog).getByLabelText('Display name')).toBeTruthy()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(await screen.findByRole('button', { name: 'Actions for Riley' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Actions for Atlas' })).toBeNull()
  })
})
