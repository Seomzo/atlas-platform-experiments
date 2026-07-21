// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesApiRequest } from '@/global'
import type { ModelOptionProvider, ProfileInfo } from '@/types/hermes'

import { WorkerModelSection } from './worker-model-section'

vi.mock('@/store/notifications', () => ({ notify: vi.fn() }))

const profile: ProfileInfo = {
  display_name: 'Codex',
  gateway_running: false,
  has_avatar: false,
  has_env: false,
  is_default: false,
  model: 'claude-sonnet-4-5',
  name: 'codex',
  path: '/tmp/atlas/workers/codex',
  provider: 'anthropic',
  role: 'Coding worker',
  skill_count: 3
}

const providers: ModelOptionProvider[] = [
  {
    authenticated: true,
    models: ['claude-sonnet-4-5'],
    name: 'Anthropic',
    slug: 'anthropic'
  },
  {
    authenticated: true,
    models: ['gpt-4o-mini'],
    name: 'OpenAI API',
    slug: 'openai-api'
  }
]

interface Deferred<T> {
  promise: Promise<T>
  reject: (reason?: unknown) => void
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: Deferred<T>['resolve']
  let reject!: Deferred<T>['reject']

  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })

  return { promise, reject, resolve }
}

describe('WorkerModelSection', () => {
  const api = vi.fn<(request: HermesApiRequest) => Promise<unknown>>()
  let current: { model: string; provider: string }
  let catalog: ModelOptionProvider[]

  beforeEach(() => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        disconnect() {}
        observe() {}
        unobserve() {}
      }
    )
    current = { model: 'claude-sonnet-4-5', provider: 'anthropic' }
    catalog = providers
    api.mockImplementation(async request => {
      if (request.path === '/api/config' && request.method === 'PUT') {
        const body = request.body as {
          config: { model: { default: string; provider: string } }
          profile: string
        }

        current = { model: body.config.model.default, provider: body.config.model.provider }

        return { ok: true }
      }

      if (request.path === '/api/config') {
        return { model: current.model }
      }

      if (request.path === '/api/model/info') {
        return { ...current, capabilities: {} }
      }

      if (request.path.startsWith('/api/model/options')) {
        return { ...current, providers: catalog }
      }

      throw new Error(`Unexpected request: ${request.path}`)
    })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  function renderSection(onUpdated = vi.fn(async () => undefined)) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={queryClient}>
        <WorkerModelSection onUpdated={onUpdated} profile={profile} />
      </QueryClientProvider>
    )

    return { onUpdated, queryClient }
  }

  async function pickCheapModel() {
    fireEvent.click(await screen.findByRole('button', { name: 'Change model' }))
    const dialog = await screen.findByRole('dialog', { name: 'Switch model' })
    fireEvent.click(await within(dialog).findByText('gpt-4o-mini'))
  }

  it('renders the current model and friendly provider from profile-scoped endpoints', async () => {
    renderSection()

    expect(await screen.findByText('Sonnet 4 5')).toBeTruthy()
    expect(screen.getByText('Anthropic · claude-sonnet-4-5')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/config', profile: 'codex' }))
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/model/info', profile: 'codex' }))
  })

  it('saves the exact profile-scoped model config and refreshes the roster', async () => {
    const { onUpdated } = renderSection()

    await pickCheapModel()

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith({
        body: {
          config: { model: { default: 'gpt-4o-mini', provider: 'openai-api' } },
          profile: 'codex'
        },
        method: 'PUT',
        path: '/api/config',
        profile: 'codex'
      })
    )
    expect(await screen.findByText('OpenAI API · gpt-4o-mini')).toBeTruthy()
    expect(onUpdated).toHaveBeenCalledTimes(1)
  })

  it('rolls optimistic UI back when the scoped save fails', async () => {
    const save = deferred<unknown>()
    api.mockImplementation(async request => {
      if (request.path === '/api/config' && request.method === 'PUT') {
        return save.promise
      }

      if (request.path === '/api/config') {
        return { model: current.model }
      }

      if (request.path === '/api/model/info') {
        return { ...current, capabilities: {} }
      }

      if (request.path.startsWith('/api/model/options')) {
        return { ...current, providers: catalog }
      }

      throw new Error(`Unexpected request: ${request.path}`)
    })
    renderSection()

    await pickCheapModel()

    expect(await screen.findByText('OpenAI API · gpt-4o-mini')).toBeTruthy()
    save.reject(new Error('backend unavailable'))

    expect(await screen.findByText('Anthropic · claude-sonnet-4-5')).toBeTruthy()
    expect(
      screen.getByText('Could not change or confirm this worker’s model. The previous model is still shown.')
    ).toBeTruthy()
  })

  it('warns when the worker has no credential for its configured provider', async () => {
    catalog = [
      {
        authenticated: false,
        auth_type: 'api_key',
        key_env: 'ANTHROPIC_API_KEY',
        models: [],
        name: 'Anthropic',
        slug: 'anthropic'
      }
    ]
    renderSection()

    expect(await screen.findByText('Needs setup')).toBeTruthy()
    expect(screen.getByText('Anthropic needs an API key in this worker before it can chat.')).toBeTruthy()
  })
})
