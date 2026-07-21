// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as HermesApi from '@/hermes'
import type { ProfileInfo } from '@/types/hermes'

const api = vi.hoisted(() => ({
  createProfile: vi.fn(),
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
  api.createProfile.mockResolvedValue({ name: 'worker', ok: true, path: '/tmp/atlas/workers/worker' })
  api.getProfileNameSuggestion.mockResolvedValue({ available: true, name: 'worker', suggestion: 'worker' })
  api.updateProfileIdentity.mockResolvedValue({ avatar: null, display_name: 'Worker', role: '', tagline: '' })
  api.updateProfileSoul.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function advanceToCreate(displayName: string) {
  fireEvent.change(screen.getByLabelText('Display name'), { target: { value: displayName } })
  await screen.findByText(/Worker ID:/)
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Create worker' }))
}

describe('CreateProfileDialog', () => {
  it('derives the worker id and automatically suffixes a reserved collision', async () => {
    const onCreated = vi.fn()

    api.getProfileNameSuggestion.mockResolvedValue({ available: false, name: 'test', suggestion: 'test-2' })

    render(<CreateProfileDialog onClose={vi.fn()} onCreated={onCreated} open profiles={[defaultProfile]} />)

    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Test' } })

    expect(await screen.findByText('Worker ID: test-2')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create worker' }))

    await waitFor(() => expect(api.createProfile).toHaveBeenCalledWith({ clone_from: 'default', name: 'test-2' }))
    expect(api.updateProfileIdentity).toHaveBeenCalledWith('test-2', {
      display_name: 'Test',
      role: '',
      tagline: ''
    })
    expect(onCreated).toHaveBeenCalledWith('test-2')
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

    render(<CreateProfileDialog onClose={vi.fn()} open profiles={[defaultProfile]} />)

    await advanceToCreate('Writer')

    expect(await screen.findByText(message)).toBeTruthy()
    expect(document.body.textContent).not.toContain('hermes:api')
    expect(document.body.textContent).not.toContain('Hermes')
    expect(document.body.textContent).not.toContain('{"detail"')
  })
})
