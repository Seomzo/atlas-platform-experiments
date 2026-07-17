import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestProfile } from '@/hermes'
import * as notifications from '@/store/notifications'
import { $activeGatewayProfile } from '@/store/profile'
import type { OAuthProvider } from '@/types/hermes'

import {
  $desktopOnboarding,
  cancelOnboardingFlow,
  completeDesktopOnboarding,
  confirmOnboardingMemoryModel,
  confirmOnboardingModel,
  connectOnboardingMemoryProvider,
  type DesktopOnboardingState,
  type OnboardingContext,
  prepareOnboardingMemoryModel,
  refreshOnboarding,
  requestDesktopOnboarding,
  retryOnboardingMemoryModel,
  saveOnboardingLocalEndpoint,
  setOnboardingMemoryModel,
  submitOnboardingCode
} from './onboarding'

function provider(id: string, name = id): OAuthProvider {
  return {
    cli_command: `hermes login ${id}`,
    docs_url: `https://example.com/${id}`,
    flow: 'pkce',
    id,
    name,
    status: { logged_in: false }
  }
}

function baseState(overrides: Partial<DesktopOnboardingState> = {}): DesktopOnboardingState {
  return {
    configured: false,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false,
    ...overrides
  }
}

function installApiMock(api: (request: { body?: unknown; path: string; profile?: string }) => Promise<unknown>) {
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { api }
  })
}

function runtimeMismatchGateway(): OnboardingContext['requestGateway'] {
  return async method => {
    if (method === 'setup.status') {
      return { provider_configured: true } as never
    }

    if (method === 'setup.runtime_check') {
      return { error: 'Selected runtime is not available.', ok: false } as never
    }

    throw new Error(`unexpected gateway method: ${method}`)
  }
}

function runtimeReadyGateway(): OnboardingContext['requestGateway'] {
  return async method => {
    if (method === 'setup.status') {
      return { provider_configured: true } as never
    }

    if (method === 'setup.runtime_check') {
      return { ok: true } as never
    }

    throw new Error(`unexpected gateway method: ${method}`)
  }
}

function onboardingContext(requestGateway: OnboardingContext['requestGateway']): OnboardingContext {
  return { requestGateway }
}

function memoryOptions(
  recommended = { provider: 'openrouter', model: 'google/gemini-3.1-flash-lite-preview' },
  models = ['google/gemini-3.1-flash-lite-preview', 'google/gemini-2.5-flash-lite']
) {
  return {
    recommended,
    providers: [
      {
        authenticated: true,
        name: 'OpenRouter',
        slug: 'openrouter',
        models,
        memory_capabilities: Object.fromEntries(
          models.map(model => [model, { selectable: true, structured_json: true }])
        )
      }
    ]
  }
}

function fallbackTimeoutGateway(): OnboardingContext['requestGateway'] {
  return async method => {
    if (method === 'setup.status' || method === 'setup.runtime_check') {
      throw new Error(`request timed out: ${method}`)
    }

    throw new Error(`unexpected gateway method: ${method}`)
  }
}

describe('refreshOnboarding', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $activeGatewayProfile.set('default')
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    $activeGatewayProfile.set('default')
    setApiRequestProfile(null)
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  it('refreshes OAuth providers again when onboarding was explicitly requested', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/providers/oauth') {
        return { providers: [provider('fresh')] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    $desktopOnboarding.set(baseState({ providers: [provider('cached')] }))
    requestDesktopOnboarding('Need provider setup')

    const ready = await refreshOnboarding(onboardingContext(runtimeMismatchGateway()))

    expect(ready).toBe(false)
    expect(api).toHaveBeenCalledTimes(1)
    expect($desktopOnboarding.get().providers?.map(p => p.id)).toEqual(['fresh'])
    expect($desktopOnboarding.get().reason).toContain('Selected runtime is not available.')
    expect($desktopOnboarding.get().reason).toContain('setup.status reports configured credentials')
  })

  it('keeps cached providers when onboarding was not re-requested', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/providers/oauth') {
        return { providers: [provider('fresh')] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    $desktopOnboarding.set(baseState({ providers: [provider('cached')] }))

    const ready = await refreshOnboarding(onboardingContext(runtimeMismatchGateway()))

    expect(ready).toBe(false)
    expect(api).not.toHaveBeenCalled()
    expect($desktopOnboarding.get().providers?.map(p => p.id)).toEqual(['cached'])
  })

  it('does not downgrade configured=true on fallback-only readiness failures', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/providers/oauth') {
        return { providers: [provider('fresh')] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    // Simulate a returning user: cache is set and store is configured.
    window.localStorage.setItem('atlas-desktop-onboarded-v1', '1')
    $desktopOnboarding.set(
      baseState({
        configured: true,
        providers: [provider('cached')],
        reason: null,
        requested: false
      })
    )

    const ready = await refreshOnboarding(onboardingContext(fallbackTimeoutGateway()))

    expect(ready).toBe(false)
    expect(api).not.toHaveBeenCalled()
    expect($desktopOnboarding.get().configured).toBe(true)
    expect($desktopOnboarding.get().reason).toBeNull()
    // The cache must survive the refresh — proving we didn't downgrade.
    expect(window.localStorage.getItem('atlas-desktop-onboarded-v1')).toBe('1')
  })

  it('shows a non-blocking notification when preserving configured on fallback', async () => {
    const notifySpy = vi.spyOn(notifications, 'notify')

    installApiMock(vi.fn())
    window.localStorage.setItem('atlas-desktop-onboarded-v1', '1')
    $desktopOnboarding.set(
      baseState({
        configured: true,
        providers: [provider('cached')],
        reason: null,
        requested: false
      })
    )

    await refreshOnboarding(onboardingContext(fallbackTimeoutGateway()))

    expect(notifySpy).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'runtime-not-ready',
        kind: 'error'
      })
    )
    expect($desktopOnboarding.get().configured).toBe(true)
  })

  it('completes a cached profile only when its existing Cortex routes are valid', async () => {
    const requests: { path: string; profile?: string }[] = []

    installApiMock(async request => {
      requests.push(request)

      if (request.path === '/api/model/cortex-memory/options') {
        return {
          ...memoryOptions(),
          current: {
            configured: true,
            valid: true,
            triage: {
              provider: 'openrouter',
              model: 'google/gemini-3.1-flash-lite-preview',
              configured: true,
              valid: true,
              unavailable_reason: ''
            },
            reasoning: {
              provider: 'openrouter',
              model: 'google/gemini-3.1-flash-lite-preview',
              configured: true,
              valid: true,
              unavailable_reason: ''
            }
          }
        }
      }

      throw new Error(`unexpected api path: ${request.path}`)
    })
    $activeGatewayProfile.set('customer-west')
    setApiRequestProfile('customer-west')
    window.localStorage.setItem('atlas-desktop-onboarded-v1:customer-west', '1')
    $desktopOnboarding.set(baseState({ configured: true }))
    const onCompleted = vi.fn()

    const ready = await refreshOnboarding({ requestGateway: runtimeReadyGateway(), onCompleted })

    expect(ready).toBe(true)
    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect($desktopOnboarding.get().configured).toBe(true)
    expect(requests).toEqual([
      expect.objectContaining({ path: '/api/model/cortex-memory/options', profile: 'customer-west' })
    ])
  })

  it('trusts captured backend route validity even when another profile left the global cache false', async () => {
    const requests: Array<{ path: string; profile?: string }> = []

    installApiMock(async request => {
      requests.push(request)

      if (request.path === '/api/model/cortex-memory/options') {
        return {
          ...memoryOptions(),
          current: {
            configured: true,
            valid: true,
            triage: {
              provider: 'openrouter',
              model: 'google/gemini-3.1-flash-lite-preview',
              configured: true,
              valid: true,
              unavailable_reason: ''
            },
            reasoning: {
              provider: 'openrouter',
              model: 'google/gemini-3.1-flash-lite-preview',
              configured: true,
              valid: true,
              unavailable_reason: ''
            }
          }
        }
      }

      throw new Error(`unexpected api path: ${request.path}`)
    })
    window.localStorage.removeItem('atlas-desktop-onboarded-v1')
    $activeGatewayProfile.set('profile-a')
    $desktopOnboarding.set(baseState({ configured: false }))
    const onCompleted = vi.fn()

    const ready = await refreshOnboarding({ requestGateway: runtimeReadyGateway(), onCompleted })

    expect(ready).toBe(true)
    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect($desktopOnboarding.get().configured).toBe(true)
    expect(requests).toEqual([
      expect.objectContaining({ path: '/api/model/cortex-memory/options', profile: 'profile-a' })
    ])
  })

  it('ignores a stale profile refresh after a newer profile finishes', async () => {
    let resolveFirstStatus: ((value: unknown) => void) | undefined
    let markFirstStarted: (() => void) | undefined

    const firstStarted = new Promise<void>(resolve => {
      markFirstStarted = resolve
    })

    const firstStatus = new Promise(resolve => {
      resolveFirstStatus = resolve
    })

    const requests: Array<{ path: string; profile?: string }> = []

    installApiMock(async request => {
      requests.push(request)

      if (request.path === '/api/model/cortex-memory/options') {
        return {
          ...memoryOptions(),
          current: {
            configured: true,
            valid: true,
            triage: { provider: 'openrouter', model: 'memory', configured: true, valid: true, unavailable_reason: '' },
            reasoning: {
              provider: 'openrouter',
              model: 'memory',
              configured: true,
              valid: true,
              unavailable_reason: ''
            }
          }
        }
      }

      throw new Error(`unexpected api path: ${request.path}`)
    })

    const firstGateway: OnboardingContext['requestGateway'] = async method => {
      if (method === 'setup.status') {
        markFirstStarted?.()

        return (await firstStatus) as never
      }

      if (method === 'setup.runtime_check') {
        return { ok: true } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }

    $activeGatewayProfile.set('profile-a')
    $desktopOnboarding.set(baseState({ configured: true }))
    const staleCompleted = vi.fn()
    const first = refreshOnboarding({ requestGateway: firstGateway, onCompleted: staleCompleted })
    await firstStarted

    $activeGatewayProfile.set('profile-b')
    const currentCompleted = vi.fn()
    const second = await refreshOnboarding({ requestGateway: runtimeReadyGateway(), onCompleted: currentCompleted })
    resolveFirstStatus?.({ provider_configured: true })
    const stale = await first

    expect(second).toBe(true)
    expect(stale).toBe(false)
    expect(currentCompleted).toHaveBeenCalledTimes(1)
    expect(staleCompleted).not.toHaveBeenCalled()
    expect(requests).toEqual([
      expect.objectContaining({ path: '/api/model/cortex-memory/options', profile: 'profile-b' })
    ])
  })

  it('migrates a cached but unroutable branding default through memory setup', async () => {
    const paths: string[] = []

    installApiMock(async ({ path }) => {
      paths.push(path)

      if (path === '/api/model/cortex-memory/options') {
        return {
          ...memoryOptions(),
          current: {
            configured: true,
            valid: false,
            triage: {
              provider: 'altas',
              model: 'atlas-cortex-memory',
              configured: true,
              valid: false,
              unavailable_reason: 'managed binding unavailable'
            },
            reasoning: {
              provider: 'altas',
              model: 'atlas-cortex-memory',
              configured: true,
              valid: false,
              unavailable_reason: 'managed binding unavailable'
            }
          }
        }
      }

      if (path.startsWith('/api/model/options')) {
        return {
          provider: 'anthropic',
          model: 'claude-sonnet-4-6',
          providers: [{ authenticated: true, name: 'Anthropic', slug: 'anthropic', models: ['claude-sonnet-4-6'] }]
        }
      }

      throw new Error(`unexpected api path: ${path}`)
    })
    window.localStorage.setItem('atlas-desktop-onboarded-v1', '1')
    window.localStorage.setItem('atlas-onboarding-skipped-v1', '1')
    $desktopOnboarding.set(baseState({ configured: true, firstRunSkipped: true }))
    const onCompleted = vi.fn()

    const ready = await refreshOnboarding({ requestGateway: runtimeReadyGateway(), onCompleted })

    expect(ready).toBe(false)
    expect(onCompleted).not.toHaveBeenCalled()
    expect(window.localStorage.getItem('atlas-desktop-onboarded-v1')).toBeNull()
    expect(window.localStorage.getItem('atlas-onboarding-skipped-v1')).toBeNull()
    expect($desktopOnboarding.get()).toMatchObject({
      configured: false,
      firstRunSkipped: false,
      flow: {
        status: 'confirming_memory_model',
        mainProvider: 'anthropic',
        mainModel: 'claude-sonnet-4-6',
        currentProvider: 'openrouter'
      }
    })
    expect(paths.filter(path => path === '/api/model/cortex-memory/options')).toHaveLength(1)
  })

  it('preserves a cached install when Cortex route validation is temporarily unavailable', async () => {
    const notifySpy = vi.spyOn(notifications, 'notify')

    installApiMock(async ({ path }) => {
      if (path === '/api/model/cortex-memory/options') {
        throw new Error('status temporarily unavailable')
      }

      throw new Error(`unexpected api path: ${path}`)
    })
    window.localStorage.setItem('atlas-desktop-onboarded-v1', '1')
    $desktopOnboarding.set(baseState({ configured: true }))
    const onCompleted = vi.fn()

    const ready = await refreshOnboarding({ requestGateway: runtimeReadyGateway(), onCompleted })

    expect(ready).toBe(true)
    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect($desktopOnboarding.get().configured).toBe(true)
    expect(window.localStorage.getItem('atlas-desktop-onboarded-v1')).toBe('1')
    expect(notifySpy).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'cortex-route-validation-unavailable', kind: 'error' })
    )
  })

  it('does not use another profile cache bit when Cortex validation fails', async () => {
    installApiMock(async ({ path, profile }) => {
      expect(profile).toBe('profile-b')

      if (path === '/api/model/cortex-memory/options') {
        throw new Error('cortex status unavailable')
      }

      if (path === '/api/model/options?include_unconfigured=1') {
        return {
          provider: 'anthropic',
          model: 'claude-sonnet-4-6',
          providers: [{ authenticated: true, name: 'Anthropic', slug: 'anthropic', models: ['claude-sonnet-4-6'] }]
        }
      }

      throw new Error(`unexpected api path: ${path}`)
    })
    window.localStorage.setItem('atlas-desktop-onboarded-v1:profile-a', '1')
    $activeGatewayProfile.set('profile-b')
    $desktopOnboarding.set(baseState({ configured: true }))
    const onCompleted = vi.fn()

    const ready = await refreshOnboarding({ requestGateway: runtimeReadyGateway(), onCompleted })

    expect(ready).toBe(false)
    expect(onCompleted).not.toHaveBeenCalled()
    expect($desktopOnboarding.get()).toMatchObject({
      configured: false,
      flow: {
        status: 'memory_model_error',
        profile: 'profile-b',
        message: expect.stringContaining('cortex status unavailable')
      }
    })
    expect(window.localStorage.getItem('atlas-desktop-onboarded-v1:profile-b')).toBeNull()
  })

  it('does not preserve configured when onboarding was explicitly requested', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/providers/oauth') {
        return { providers: [provider('fresh')] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    $desktopOnboarding.set(
      baseState({
        configured: true,
        providers: [provider('cached')],
        reason: null,
        requested: true
      })
    )

    const ready = await refreshOnboarding(onboardingContext(fallbackTimeoutGateway()))

    expect(ready).toBe(false)
    // requested overrides preservation — should downgrade.
    expect($desktopOnboarding.get().configured).toBe(false)
    expect(api).toHaveBeenCalledTimes(1)
  })

  it('still surfaces onboarding when fallback failure happens before configured state', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/providers/oauth') {
        return { providers: [provider('fresh')] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    $desktopOnboarding.set(baseState({ configured: false, providers: null, requested: true }))

    const ready = await refreshOnboarding(onboardingContext(fallbackTimeoutGateway()))

    expect(ready).toBe(false)
    expect(api).toHaveBeenCalledTimes(1)
    expect($desktopOnboarding.get().configured).toBe(false)
    expect($desktopOnboarding.get().reason).toContain('request timed out')
  })

  it('deduplicates concurrent provider refresh calls', async () => {
    let resolveProviders!: (value: { providers: OAuthProvider[] }) => void

    const providersPromise = new Promise<{ providers: OAuthProvider[] }>(resolve => {
      resolveProviders = value => {
        resolve(value)
      }
    })

    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/providers/oauth') {
        return providersPromise
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    $desktopOnboarding.set(baseState({ requested: true }))

    const first = refreshOnboarding(onboardingContext(runtimeMismatchGateway()))
    const second = refreshOnboarding(onboardingContext(runtimeMismatchGateway()))

    await vi.waitFor(() => expect(api).toHaveBeenCalledTimes(1))

    resolveProviders({ providers: [provider('shared')] })
    await Promise.all([first, second])

    expect($desktopOnboarding.get().providers?.map(p => p.id)).toEqual(['shared'])
  })

  it('routes an uncached runnable install through Cortex setup before completion', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path.startsWith('/api/model/options')) {
        return {
          provider: 'anthropic',
          model: 'claude-sonnet-4-6',
          providers: [{ authenticated: true, name: 'Anthropic', slug: 'anthropic', models: ['claude-sonnet-4-6'] }]
        }
      }

      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const requestGateway: OnboardingContext['requestGateway'] = async method => {
      if (method === 'setup.status') {
        return { provider_configured: true } as never
      }

      if (method === 'setup.runtime_check') {
        return { ok: true } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }

    const onCompleted = vi.fn()

    const ready = await refreshOnboarding({ requestGateway, onCompleted })

    expect(ready).toBe(false)
    expect(onCompleted).not.toHaveBeenCalled()
    expect($desktopOnboarding.get().configured).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      mainProvider: 'anthropic',
      mainModel: 'claude-sonnet-4-6',
      currentProvider: 'openrouter',
      currentModel: 'google/gemini-3.1-flash-lite-preview'
    })
  })
})

describe('OAuth onboarding', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  it('clears stale readiness errors after OAuth succeeds and model confirmation is shown', async () => {
    const model = 'anthropic/claude-opus-4.8'
    const calls: { body?: unknown; path: string }[] = []

    installApiMock(async ({ body, path }: { body?: unknown; path: string }) => {
      calls.push({ body, path })

      if (path === '/api/providers/oauth/nous/submit') {
        return { ok: true, status: 'approved' }
      }

      if (path.startsWith('/api/model/options')) {
        return {
          providers: [
            {
              name: 'Nous Portal',
              slug: 'nous',
              models: [model]
            }
          ]
        }
      }

      if (path.startsWith('/api/model/recommended-default?')) {
        return { provider: 'nous', model, free_tier: false }
      }

      if (path === '/api/model/set') {
        return { ok: true, provider: 'nous', model, gateway_tools: [] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const requestGateway: OnboardingContext['requestGateway'] = async (method, params) => {
      if (method === 'reload.env') {
        return {} as never
      }

      if (method === 'setup.status') {
        return { provider_configured: true } as never
      }

      if (method === 'setup.runtime_check') {
        expect(params).toEqual({ provider: 'nous' })

        return { ok: true } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }

    $desktopOnboarding.set(
      baseState({
        flow: {
          status: 'awaiting_user',
          provider: provider('nous', 'Nous Portal'),
          start: {
            auth_url: 'https://portal.example/auth',
            expires_in: 600,
            flow: 'pkce',
            session_id: 'portal-session'
          },
          code: 'fresh-code'
        },
        reason:
          'No access token found for Nous Portal login. setup.status reports configured credentials, but runtime resolution still failed.',
        requested: true
      })
    )

    await submitOnboardingCode(onboardingContext(requestGateway))

    const state = $desktopOnboarding.get()
    expect(state.reason).toBeNull()
    expect(state.flow.status).toBe('confirming_model')

    if (state.flow.status === 'confirming_model') {
      expect(state.flow.label).toBe('Nous Portal')
      expect(state.flow.currentModel).toBe(model)
    }

    expect(calls.some(c => c.path === '/api/model/set')).toBe(true)

    const optionsIndex = calls.findIndex(c => c.path.startsWith('/api/model/options'))
    const recommendedIndex = calls.findIndex(c => c.path.startsWith('/api/model/recommended-default'))
    const setIndex = calls.findIndex(c => c.path === '/api/model/set')

    expect(optionsIndex).toBeGreaterThanOrEqual(0)
    expect(recommendedIndex).toBeGreaterThan(optionsIndex)
    expect(setIndex).toBeGreaterThan(recommendedIndex)
  })
})

describe('Cortex memory model onboarding', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $activeGatewayProfile.set('default')
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    $activeGatewayProfile.set('default')
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  it('defaults to the backend-recommended model when it is distinct and available', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const loaded = await prepareOnboardingMemoryModel('anthropic', 'claude-sonnet-4-6', 'Anthropic')

    expect(loaded).toBe(true)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      currentProvider: 'openrouter',
      currentModel: 'google/gemini-3.1-flash-lite-preview',
      recommendedProvider: 'openrouter',
      recommendedModel: 'google/gemini-3.1-flash-lite-preview'
    })
  })

  it('connects a dedicated memory provider without replacing the selected chat model', async () => {
    const calls: Array<{ body?: unknown; path: string; profile?: string }> = []
    let catalogRead = 0

    installApiMock(async request => {
      calls.push(request)

      if (request.path === '/api/model/cortex-memory/options') {
        catalogRead += 1

        return {
          recommended: { provider: '', model: '' },
          providers: [
            {
              authenticated: false,
              auth_type: 'api_key',
              key_env: 'OPENROUTER_API_KEY',
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['google/gemini-3.1-flash-lite'],
              memory_capabilities: {
                'google/gemini-3.1-flash-lite': { selectable: true, structured_json: true }
              }
            }
          ]
        }
      }

      if (request.path === '/api/env') {
        return { ok: true }
      }

      if (request.path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: 'verified' }
      }

      if (request.path === '/api/model/cortex-memory/options?refresh=1') {
        return {
          recommended: { provider: 'openrouter', model: 'google/gemini-3.1-flash-lite' },
          providers: [
            {
              authenticated: true,
              auth_type: 'api_key',
              key_env: 'OPENROUTER_API_KEY',
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['google/gemini-3.1-flash-lite'],
              memory_capabilities: {
                'google/gemini-3.1-flash-lite': { selectable: true, structured_json: true }
              }
            }
          ]
        }
      }

      throw new Error(`unexpected api path: ${request.path}`)
    })
    $activeGatewayProfile.set('customer-west')

    const prepared = await prepareOnboardingMemoryModel('anthropic', 'claude-sonnet-4-6', 'Anthropic')

    expect(prepared).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'memory_model_error',
      mainProvider: 'anthropic',
      mainModel: 'claude-sonnet-4-6',
      profile: 'customer-west',
      connectableProvider: {
        slug: 'openrouter',
        keyEnv: 'OPENROUTER_API_KEY',
        authType: 'api_key'
      }
    })

    expect(await connectOnboardingMemoryProvider('sk-memory-only')).toBe(true)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      mainProvider: 'anthropic',
      mainModel: 'claude-sonnet-4-6',
      currentProvider: 'openrouter',
      currentModel: 'google/gemini-3.1-flash-lite',
      profile: 'customer-west'
    })
    expect(catalogRead).toBe(1)
    expect(calls).toContainEqual(
      expect.objectContaining({
        path: '/api/env',
        profile: 'customer-west',
        body: { key: 'OPENROUTER_API_KEY', value: 'sk-memory-only' }
      })
    )
    expect(calls).toContainEqual(
      expect.objectContaining({
        path: '/api/model/cortex-memory/options?refresh=1',
        profile: 'customer-west'
      })
    )
    expect(calls.some(call => call.path === '/api/model/set')).toBe(false)
  })

  it('keeps memory provider connection failures explicit and retryable', async () => {
    installApiMock(async ({ path }) => {
      if (path === '/api/model/cortex-memory/options') {
        return {
          recommended: { provider: '', model: '' },
          providers: [
            {
              authenticated: false,
              auth_type: 'api_key',
              key_env: 'OPENROUTER_API_KEY',
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['memory-model'],
              memory_capabilities: {
                'memory-model': { selectable: true, structured_json: true }
              }
            }
          ]
        }
      }

      if (path === '/api/env') {
        throw new Error('key store unavailable')
      }

      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: 'verified' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    await prepareOnboardingMemoryModel('nous', 'hermes-4', 'Nous')
    expect(await connectOnboardingMemoryProvider('sk-memory-only')).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'memory_model_error',
      connecting: false,
      credentialMessage: expect.stringContaining('key store unavailable')
    })
  })

  it('does not treat missing capability metadata as an eligible or connectable memory route', async () => {
    installApiMock(async ({ path }) => {
      if (path === '/api/model/cortex-memory/options') {
        return {
          recommended: { provider: 'openrouter', model: 'metadata-missing' },
          providers: [
            {
              authenticated: true,
              auth_type: 'api_key',
              key_env: 'OPENROUTER_API_KEY',
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['metadata-missing']
            }
          ]
        }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    expect(await prepareOnboardingMemoryModel('nous', 'hermes-4', 'Nous')).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'memory_model_error',
      connectableProvider: null
    })
  })

  it('does not persist a memory-provider key explicitly rejected by the provider', async () => {
    const paths: string[] = []

    installApiMock(async ({ path }) => {
      paths.push(path)

      if (path === '/api/model/cortex-memory/options') {
        return {
          recommended: { provider: '', model: '' },
          providers: [
            {
              authenticated: false,
              auth_type: 'api_key',
              key_env: 'OPENROUTER_API_KEY',
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['memory-model'],
              memory_capabilities: {
                'memory-model': { selectable: true, structured_json: true }
              }
            }
          ]
        }
      }

      if (path === '/api/providers/validate') {
        return { ok: false, reachable: true, message: 'OpenRouter rejected the API key (401).' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    await prepareOnboardingMemoryModel('nous', 'hermes-4', 'Nous')
    expect(await connectOnboardingMemoryProvider('sk-rejected')).toBe(false)
    expect(paths).not.toContain('/api/env')
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'memory_model_error',
      connecting: false,
      credentialMessage: expect.stringContaining('401')
    })
  })

  it('does not reopen memory setup when a cancelled key probe resolves late', async () => {
    let resolveProbe: ((value: unknown) => void) | undefined
    let markProbeStarted: (() => void) | undefined

    const probeStarted = new Promise<void>(resolve => {
      markProbeStarted = resolve
    })

    const probe = new Promise(resolve => {
      resolveProbe = resolve
    })

    const paths: string[] = []

    installApiMock(async ({ path }) => {
      paths.push(path)

      if (path === '/api/model/cortex-memory/options') {
        return {
          recommended: { provider: '', model: '' },
          providers: [
            {
              authenticated: false,
              auth_type: 'api_key',
              key_env: 'OPENROUTER_API_KEY',
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['memory-model'],
              memory_capabilities: {
                'memory-model': { selectable: true, structured_json: true }
              }
            }
          ]
        }
      }

      if (path === '/api/providers/validate') {
        markProbeStarted?.()

        return probe
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    await prepareOnboardingMemoryModel('nous', 'hermes-4', 'Nous')
    const connecting = connectOnboardingMemoryProvider('sk-late')
    await probeStarted
    cancelOnboardingFlow()
    resolveProbe?.({ ok: true, reachable: true, message: 'verified' })

    expect(await connecting).toBe(false)
    expect($desktopOnboarding.get().flow).toEqual({ status: 'idle' })
    expect(paths).not.toContain('/api/env')
  })

  it('falls back to the first eligible distinct model when the recommendation matches chat', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions({ provider: 'openrouter', model: 'same-model' }, [
          'same-model',
          'google/gemini-2.5-flash-lite'
        ])
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    await prepareOnboardingMemoryModel('openrouter', 'same-model', 'OpenRouter')

    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      currentProvider: 'openrouter',
      currentModel: 'google/gemini-2.5-flash-lite',
      recommendedProvider: '',
      recommendedModel: ''
    })
  })

  it('skips catalog entries that are not selectable for structured memory output', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/model/cortex-memory/options') {
        return {
          recommended: { provider: 'openrouter', model: 'plain-chat-model' },
          providers: [
            {
              authenticated: true,
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['plain-chat-model', 'structured-memory-model'],
              memory_capabilities: {
                'plain-chat-model': { selectable: false, structured_json: false },
                'structured-memory-model': { selectable: true, structured_json: true }
              }
            }
          ]
        }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    await prepareOnboardingMemoryModel('anthropic', 'claude-sonnet-4-6', 'Anthropic')

    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      currentModel: 'structured-memory-model',
      recommendedModel: ''
    })
  })

  it('refuses the exact chat provider/model pair even when the UI is bypassed', () => {
    $desktopOnboarding.set(
      baseState({
        flow: {
          status: 'confirming_memory_model',
          label: 'OpenRouter',
          mainProvider: 'openrouter',
          mainModel: 'frontier-model',
          currentProvider: 'openrouter',
          currentModel: 'cheap-model',
          recommendedProvider: 'openrouter',
          recommendedModel: 'cheap-model',
          providers: [
            {
              authenticated: true,
              name: 'OpenRouter',
              slug: 'openrouter',
              models: ['frontier-model', 'cheap-model']
            }
          ],
          profile: 'default',
          saving: false,
          message: null
        }
      })
    )

    expect(setOnboardingMemoryModel('OPENROUTER', 'frontier-model')).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      currentModel: 'cheap-model',
      message: expect.stringContaining('different')
    })
  })

  it('uses one atomic save and requires both Cortex routes to echo the selected pair', async () => {
    const calls: { body?: unknown; path: string }[] = []

    installApiMock(async ({ body, path }: { body?: unknown; path: string }) => {
      calls.push({ body, path })

      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      if (path === '/api/model/cortex-memory') {
        const pair = { provider: 'openrouter', model: 'google/gemini-3.1-flash-lite-preview' }

        return { ok: true, ...pair, triage: pair, reasoning: pair, gateway_tools: [] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    await prepareOnboardingMemoryModel('anthropic', 'claude-sonnet-4-6', 'Anthropic')
    const saved = await confirmOnboardingMemoryModel()

    expect(saved).toBe(true)
    expect(calls.filter(call => call.path === '/api/model/cortex-memory')).toHaveLength(1)
    expect(calls.find(call => call.path === '/api/model/cortex-memory')?.body).toEqual({
      provider: 'openrouter',
      model: 'google/gemini-3.1-flash-lite-preview'
    })
    expect($desktopOnboarding.get().configured).toBe(false)
  })

  it('does not complete when an atomic response is missing one matching route', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      if (path === '/api/model/cortex-memory') {
        return {
          ok: true,
          provider: 'openrouter',
          model: 'google/gemini-3.1-flash-lite-preview',
          triage: { provider: 'openrouter', model: 'google/gemini-3.1-flash-lite-preview' },
          reasoning: { provider: 'openrouter', model: 'wrong-model' },
          gateway_tools: []
        }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    await prepareOnboardingMemoryModel('anthropic', 'claude-sonnet-4-6', 'Anthropic')
    const saved = await confirmOnboardingMemoryModel()

    expect(saved).toBe(false)
    expect($desktopOnboarding.get().configured).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      saving: false,
      message: expect.stringContaining('both Cortex memory routes')
    })
  })

  it('does not complete a delayed memory save after the active profile changes', async () => {
    let resolveSave: ((value: unknown) => void) | undefined
    let markSaveStarted: (() => void) | undefined

    const saveStarted = new Promise<void>(resolve => {
      markSaveStarted = resolve
    })

    const delayedSave = new Promise(resolve => {
      resolveSave = resolve
    })

    const calls: Array<{ path: string; profile?: string }> = []

    installApiMock(async request => {
      calls.push(request)

      if (request.path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      if (request.path === '/api/model/cortex-memory') {
        markSaveStarted?.()

        return delayedSave
      }

      throw new Error(`unexpected api path: ${request.path}`)
    })
    $activeGatewayProfile.set('profile-b')
    await prepareOnboardingMemoryModel('anthropic', 'claude-sonnet-4-6', 'Anthropic')
    const save = confirmOnboardingMemoryModel()
    await saveStarted

    $activeGatewayProfile.set('profile-a')
    const pair = { provider: 'openrouter', model: 'google/gemini-3.1-flash-lite-preview' }
    resolveSave?.({ ok: true, ...pair, triage: pair, reasoning: pair, gateway_tools: [] })

    expect(await save).toBe(false)
    expect($desktopOnboarding.get().configured).toBe(false)
    expect(calls.find(call => call.path === '/api/model/cortex-memory')).toEqual(
      expect.objectContaining({ profile: 'profile-b' })
    )
  })

  it('does not finalize a completed memory transition for a profile that is no longer active', () => {
    $activeGatewayProfile.set('profile-a')

    expect(completeDesktopOnboarding('profile-b')).toBe(false)
    expect($desktopOnboarding.get().configured).toBe(false)
    expect(window.localStorage.getItem('atlas-desktop-onboarded-v1:profile-b')).toBeNull()
  })

  it('keeps catalog failures retryable', async () => {
    let attempt = 0

    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/model/cortex-memory/options') {
        attempt += 1

        if (attempt === 1) {
          throw new Error('catalog offline')
        }

        return memoryOptions()
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    expect(await prepareOnboardingMemoryModel('anthropic', 'claude-sonnet-4-6', 'Anthropic')).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'memory_model_error',
      message: expect.stringContaining('catalog offline')
    })

    expect(await retryOnboardingMemoryModel()).toBe(true)
    expect($desktopOnboarding.get().flow.status).toBe('confirming_memory_model')
  })

  it('blocks Cortex setup when the main assignment returns ok=false', async () => {
    const calls: string[] = []

    installApiMock(async ({ path }: { path: string }) => {
      calls.push(path)

      if (path === '/api/model/set') {
        return { ok: false, confirm_required: true }
      }

      throw new Error(`unexpected api path: ${path}`)
    })
    $desktopOnboarding.set(
      baseState({
        flow: {
          status: 'confirming_model',
          providerSlug: 'anthropic',
          currentModel: 'claude-opus-4-6',
          label: 'Anthropic',
          saving: false,
          message: null
        }
      })
    )

    const outcome = await confirmOnboardingModel(onboardingContext(async () => undefined as never))

    expect(outcome).toBe(false)
    expect(calls).not.toContain('/api/model/cortex-memory/options')
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_model',
      saving: false,
      message: expect.stringContaining('cost confirmation')
    })
  })

  it('advances from a persisted main pair to the memory selection step', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/model/set') {
        return { ok: true, provider: 'anthropic', model: 'claude-sonnet-4-6', gateway_tools: [] }
      }

      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      throw new Error(`unexpected api path: ${path}`)
    })
    $desktopOnboarding.set(
      baseState({
        flow: {
          status: 'confirming_model',
          providerSlug: 'anthropic',
          currentModel: 'claude-sonnet-4-6',
          label: 'Anthropic',
          saving: false,
          message: null
        }
      })
    )

    const outcome = await confirmOnboardingModel(onboardingContext(async () => undefined as never))

    expect(outcome).toBe('memory')
    expect($desktopOnboarding.get().flow.status).toBe('confirming_memory_model')
  })

  it('does not let deferred first-run setup bypass Cortex through the manual Settings path', async () => {
    const calls: string[] = []

    installApiMock(async ({ path }: { path: string }) => {
      calls.push(path)

      if (path === '/api/model/set') {
        return { ok: true, provider: 'openrouter', model: 'frontier-model', gateway_tools: [] }
      }

      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      throw new Error(`unexpected api path: ${path}`)
    })
    $desktopOnboarding.set(
      baseState({
        configured: false,
        firstRunSkipped: true,
        manual: true,
        flow: {
          status: 'confirming_model',
          providerSlug: 'openrouter',
          currentModel: 'frontier-model',
          label: 'OpenRouter',
          saving: false,
          message: null
        }
      })
    )

    const outcome = await confirmOnboardingModel(onboardingContext(async () => undefined as never))

    expect(outcome).toBe('memory')
    expect(calls).toContain('/api/model/cortex-memory/options')
    expect($desktopOnboarding.get().flow.status).toBe('confirming_memory_model')
  })

  it('keeps provider switching lightweight for an already-configured install', async () => {
    const calls: string[] = []

    installApiMock(async ({ path }: { path: string }) => {
      calls.push(path)

      if (path === '/api/model/set') {
        return { ok: true, provider: 'openrouter', model: 'frontier-model', gateway_tools: [] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })
    $desktopOnboarding.set(
      baseState({
        configured: true,
        manual: true,
        flow: {
          status: 'confirming_model',
          providerSlug: 'openrouter',
          currentModel: 'frontier-model',
          label: 'OpenRouter',
          saving: false,
          message: null
        }
      })
    )

    const outcome = await confirmOnboardingModel(onboardingContext(async () => undefined as never))

    expect(outcome).toBe('complete')
    expect(calls).not.toContain('/api/model/cortex-memory/options')
  })
})

describe('saveOnboardingLocalEndpoint', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  function readyGateway(): OnboardingContext['requestGateway'] {
    return async method => {
      if (method === 'reload.env') {
        return {} as never
      }

      if (method === 'setup.status') {
        return { provider_configured: true } as never
      }

      if (method === 'setup.runtime_check') {
        return { ok: true } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }
  }

  it('errors when the endpoint advertises no models (nothing to route to)', async () => {
    const calls: string[] = []
    installApiMock(async ({ path }: { path: string }) => {
      calls.push(path)

      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: '', models: [] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const result = await saveOnboardingLocalEndpoint('http://127.0.0.1:8000/v1', '', {
      requestGateway: readyGateway()
    })

    expect(result.ok).toBe(false)
    expect(result.message).toContain('no models')
    // Must not attempt to persist an assignment without a model.
    expect(calls).not.toContain('/api/model/set')
  })

  it('auto-discovers the main model, preserves its endpoint, then advances to Cortex setup', async () => {
    const calls: { body?: unknown; path: string }[] = []

    const api = vi.fn(async ({ body, path }: { body?: unknown; path: string }) => {
      calls.push({ body, path })

      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: '', models: ['llama-3.1-8b', 'qwen2.5-7b'] }
      }

      if (path === '/api/model/set') {
        return { ok: true, provider: 'custom', model: 'llama-3.1-8b', base_url: 'http://127.0.0.1:8000/v1' }
      }

      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    const onCompleted = vi.fn()

    const result = await saveOnboardingLocalEndpoint('http://127.0.0.1:8000/v1', '', {
      onCompleted,
      requestGateway: readyGateway()
    })

    expect(result.ok).toBe(true)

    const assign = calls.find(c => c.path === '/api/model/set')
    expect(assign?.body).toMatchObject({
      scope: 'main',
      provider: 'custom',
      model: 'llama-3.1-8b',
      base_url: 'http://127.0.0.1:8000/v1'
    })

    expect(onCompleted).not.toHaveBeenCalled()
    expect($desktopOnboarding.get().configured).toBe(false)
    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      mainProvider: 'custom',
      mainModel: 'llama-3.1-8b'
    })
  })

  it('forwards the API key to the probe and persists it for auth-gated endpoints', async () => {
    const calls: { body?: unknown; path: string }[] = []

    const api = vi.fn(async ({ body, path }: { body?: unknown; path: string }) => {
      calls.push({ body, path })

      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: '', models: ['gpt-oss-120b'] }
      }

      if (path === '/api/model/set') {
        return { ok: true, provider: 'custom', model: 'gpt-oss-120b', base_url: 'https://text.example.com/v1' }
      }

      if (path === '/api/model/cortex-memory/options') {
        return memoryOptions()
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)

    const result = await saveOnboardingLocalEndpoint('https://text.example.com/v1', 'sk-secret', {
      requestGateway: readyGateway()
    })

    expect(result.ok).toBe(true)

    // The probe must receive the key so an auth-gated /v1/models enumerates.
    const probe = calls.find(c => c.path === '/api/providers/validate')
    expect(probe?.body).toMatchObject({
      key: 'OPENAI_BASE_URL',
      value: 'https://text.example.com/v1',
      api_key: 'sk-secret'
    })

    // And the key must be persisted alongside the endpoint for runtime auth.
    const assign = calls.find(c => c.path === '/api/model/set')
    expect(assign?.body).toMatchObject({
      scope: 'main',
      provider: 'custom',
      model: 'gpt-oss-120b',
      base_url: 'https://text.example.com/v1',
      api_key: 'sk-secret'
    })
  })

  it('reports the runtime reason when resolution still fails after saving', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: '', models: ['llama-3.1-8b'] }
      }

      if (path === '/api/model/set') {
        return { ok: true }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const failingGateway: OnboardingContext['requestGateway'] = async method => {
      if (method === 'reload.env') {
        return {} as never
      }

      if (method === 'setup.status') {
        return { provider_configured: false } as never
      }

      if (method === 'setup.runtime_check') {
        return { ok: false, error: 'No provider can serve the selected model.' } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }

    const result = await saveOnboardingLocalEndpoint('http://127.0.0.1:8000/v1', '', {
      requestGateway: failingGateway
    })

    expect(result.ok).toBe(false)
    expect(result.message).toContain('No provider can serve the selected model.')
    expect($desktopOnboarding.get().configured).not.toBe(true)
  })
})
