// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type * as React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as HermesApi from '@/hermes'
import type { ProfileInfo } from '@/types/hermes'

const api = vi.hoisted(() => ({
  createProfile: vi.fn(),
  getGlobalModelOptions: vi.fn(),
  getProfileNameSuggestion: vi.fn(),
  updateProfileIdentity: vi.fn(),
  updateProfileSoul: vi.fn()
}))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof HermesApi>()),
  ...api
}))

import { CreateProfileDialog } from './create-profile-dialog'

const defaultProfile: ProfileInfo = {
  display_name: 'Atlas',
  gateway_running: true,
  has_avatar: false,
  has_env: false,
  is_default: true,
  model: 'gpt-5.4',
  name: 'default',
  path: '/tmp/atlas',
  provider: 'openai-codex',
  role: 'Operations Lead',
  skill_count: 12
}

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      disconnect() {}
      observe() {}
      unobserve() {}
    }
  )
  api.createProfile.mockResolvedValue({ name: 'worker', ok: true, path: '/tmp/atlas/workers/worker' })
  api.getProfileNameSuggestion.mockResolvedValue({ available: true, name: 'worker', suggestion: 'worker' })
  api.getGlobalModelOptions.mockResolvedValue({
    providers: [
      { authenticated: true, models: ['gpt-5.4'], name: 'OpenAI Codex', slug: 'openai-codex' },
      { authenticated: true, models: ['claude-haiku-4-5'], name: 'Anthropic', slug: 'anthropic' }
    ]
  })
  api.updateProfileIdentity.mockResolvedValue({ avatar: null, display_name: 'Worker', role: '', tagline: '' })
  api.updateProfileSoul.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

async function advanceToCreate(displayName: string) {
  fireEvent.change(screen.getByLabelText('Display name'), { target: { value: displayName } })
  await screen.findByText(/Worker ID:/)
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Create worker' }))
}

function renderDialog(props: React.ComponentProps<typeof CreateProfileDialog>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={queryClient}>
      <CreateProfileDialog {...props} />
    </QueryClientProvider>
  )
}

describe('CreateProfileDialog', () => {
  it('derives the worker id and automatically suffixes a reserved collision', async () => {
    const onCreated = vi.fn()

    api.getProfileNameSuggestion.mockResolvedValue({ available: false, name: 'test', suggestion: 'test-2' })

    renderDialog({ onClose: vi.fn(), onCreated, open: true, profiles: [defaultProfile] })

    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Test' } })

    expect(await screen.findByText('Worker ID: test-2')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create worker' }))

    await waitFor(() =>
      expect(api.createProfile).toHaveBeenCalledWith({
        clone_from: 'default',
        model: 'gpt-5.4',
        name: 'test-2',
        provider: 'openai-codex'
      })
    )
    expect(api.updateProfileIdentity).toHaveBeenCalledWith('test-2', {
      display_name: 'Test',
      role: '',
      tagline: ''
    })
    expect(onCreated).toHaveBeenCalledWith('test-2')
  })

  it('defaults to Atlas and can assign a different chat model during creation', async () => {
    renderDialog({ onClose: vi.fn(), open: true, profiles: [defaultProfile] })

    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Writer' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByText('Same as Atlas')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { expanded: false, name: /Model/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Choose model' }))
    fireEvent.click(await screen.findByText('claude-haiku-4-5'))
    fireEvent.click(screen.getByRole('button', { name: 'Create worker' }))

    await waitFor(() =>
      expect(api.createProfile).toHaveBeenCalledWith({
        clone_from: 'default',
        model: 'claude-haiku-4-5',
        name: 'worker',
        provider: 'anthropic'
      })
    )
  })

  it.each([
    {
      error:
        'Error invoking remote method \'hermes:api\': Error: 400: {"detail":"Profile name \'test\' is reserved — it collides with the Hermes installation."}',
      message: 'That name is reserved — try a different one.'
    },
    {
      error: "Error invoking remote method 'hermes:api': Error: backend exploded inside Hermes",
      message: 'Could not create this worker. Try again.'
    }
  ])('renders branded friendly copy instead of transport details', async ({ error, message }) => {
    api.getProfileNameSuggestion.mockResolvedValue({ available: true, name: 'writer', suggestion: 'writer' })
    api.createProfile.mockRejectedValue(new Error(error))

    renderDialog({ onClose: vi.fn(), open: true, profiles: [defaultProfile] })

    await advanceToCreate('Writer')

    expect(await screen.findByText(message)).toBeTruthy()
    expect(document.body.textContent).not.toContain('hermes:api')
    expect(document.body.textContent).not.toContain('Hermes')
    expect(document.body.textContent).not.toContain('{"detail"')
  })
})
