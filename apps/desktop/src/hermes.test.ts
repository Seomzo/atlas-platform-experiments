import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  avatarUrl,
  generateProfileAvatar,
  getCortexMemoryModelOptions,
  getCronJobs,
  getGlobalModelInfo,
  getGlobalModelOptions,
  getHermesConfig,
  getHermesConfigDefaults,
  getProfileIdentity,
  getProfiles,
  getSessionMessages,
  getStatus,
  listAllProfileSessions,
  listSessions,
  PROFILE_AVATAR_MAX_BYTES,
  setCortexMemoryModelAssignment,
  updateProfileIdentity,
  uploadProfileAvatar
} from './hermes'
import { refreshActiveProfile } from './store/profile'

const emptySessionsResponse = {
  limit: 0,
  offset: 0,
  sessions: [],
  total: 0
}

describe('Atlas REST session helpers', () => {
  let api: ReturnType<typeof vi.fn>

  beforeEach(() => {
    api = vi.fn().mockResolvedValue(emptySessionsResponse)
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        api,
        getConnection: vi.fn().mockResolvedValue({
          baseUrl: 'http://127.0.0.1:9119',
          token: 'desktop-token'
        })
      }
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  it('uses a longer timeout for the single-profile session list', async () => {
    await listSessions(50, 1)

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/sessions?limit=50&offset=0&min_messages=1&archived=exclude&order=recent',
        timeoutMs: 60_000
      })
    )
  })

  it('uses a longer timeout for the all-profile session list', async () => {
    await listAllProfileSessions(50, 1)

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/profiles/sessions?limit=50&offset=0&min_messages=1&archived=exclude&order=recent&profile=all',
        timeoutMs: 60_000
      })
    )
  })

  it('uses a longer timeout for profile listing during desktop startup', async () => {
    api.mockResolvedValue({ profiles: [] })

    await getProfiles()

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/profiles',
        timeoutMs: 60_000
      })
    )
  })

  it('maps worker identity reads and partial updates to the profile endpoints', async () => {
    const identity = {
      avatar: null,
      display_name: 'Riley',
      role: 'Service Advisor',
      tagline: 'Keeps every repair order moving.'
    }

    api.mockResolvedValue(identity)

    await expect(getProfileIdentity('service_writer')).resolves.toEqual(identity)
    expect(api).toHaveBeenLastCalledWith({
      path: '/api/profiles/service_writer/identity'
    })

    await expect(updateProfileIdentity('service_writer', { role: 'Senior Advisor' })).resolves.toEqual(identity)
    expect(api).toHaveBeenLastCalledWith({
      path: '/api/profiles/service_writer/identity',
      method: 'PATCH',
      body: { role: 'Senior Advisor' }
    })
  })

  it('uploads supported avatar bytes through the JSON desktop bridge', async () => {
    const identity = { avatar: 'avatars/avatar.png', display_name: 'Riley', role: '', tagline: '' }

    const file = {
      arrayBuffer: vi.fn().mockResolvedValue(Uint8Array.from([1, 2, 3]).buffer),
      size: 3,
      type: 'image/png'
    } as unknown as File

    api.mockResolvedValue({ identity, ok: true })

    await expect(uploadProfileAvatar('service_writer', file)).resolves.toEqual(identity)
    expect(api).toHaveBeenCalledWith({
      path: '/api/profiles/service_writer/avatar',
      method: 'PUT',
      body: {
        content_type: 'image/png',
        data_base64: 'AQID'
      }
    })
  })

  it('rejects invalid avatar files before calling the backend', async () => {
    const invalidType = { size: 3, type: 'image/gif' } as File
    const oversize = { size: PROFILE_AVATAR_MAX_BYTES + 1, type: 'image/webp' } as File

    await expect(uploadProfileAvatar('writer', invalidType)).rejects.toThrow('unsupported_avatar_type')
    await expect(uploadProfileAvatar('writer', oversize)).rejects.toThrow('avatar_too_large')
    expect(api).not.toHaveBeenCalled()
  })

  it('generates avatars and builds authenticated cache-busted avatar URLs', async () => {
    const identity = { avatar: 'avatars/avatar.webp', display_name: 'Avery', role: '', tagline: '' }
    api.mockResolvedValue({ identity, ok: true })

    await expect(generateProfileAvatar('advisor', 'A confident dealership advisor')).resolves.toEqual(identity)
    expect(api).toHaveBeenCalledWith({
      path: '/api/profiles/advisor/avatar/generate',
      method: 'POST',
      body: { prompt: 'A confident dealership advisor' }
    })

    await expect(avatarUrl('advisor', 1721312345678)).resolves.toBe(
      'http://127.0.0.1:9119/api/profiles/advisor/avatar?v=1721312345678&token=desktop-token'
    )
  })

  it('uses a longer timeout for active profile refresh during desktop startup', async () => {
    api.mockResolvedValueOnce({ current: 'default' }).mockResolvedValueOnce({ profiles: [] })

    await refreshActiveProfile()

    expect(api).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        path: '/api/profiles/active',
        timeoutMs: 60_000
      })
    )
    expect(api).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        path: '/api/profiles',
        timeoutMs: 60_000
      })
    )
  })

  it('gives the whole startup data burst the long timeout, not just profiles', async () => {
    api.mockResolvedValue({})

    const bootCalls: [() => Promise<unknown>, string][] = [
      [getHermesConfig, '/api/config'],
      [getHermesConfigDefaults, '/api/config/defaults'],
      [getGlobalModelInfo, '/api/model/info'],
      [() => getGlobalModelOptions(), '/api/model/options?explicit_only=1'],
      [() => getCortexMemoryModelOptions(), '/api/model/cortex-memory/options'],
      [getCronJobs, '/api/cron/jobs']
    ]

    for (const [call, path] of bootCalls) {
      api.mockClear()
      await call()
      expect(api).toHaveBeenCalledWith(expect.objectContaining({ path, timeoutMs: 60_000 }))
    }
  })

  it('keeps the liveness poll on the short default so a dead backend fails fast', async () => {
    api.mockResolvedValue({})
    api.mockClear()

    await getStatus()

    // /api/status must NOT carry the long startup timeout — it is the runtime
    // liveness probe and has to fail quickly when the backend drops.
    const call = api.mock.calls[0]?.[0] as { path: string; timeoutMs?: number }
    expect(call.path).toBe('/api/status')
    expect(call.timeoutMs).toBeUndefined()
  })

  it('tags cross-profile message reads for Electron routing and backend lookup', async () => {
    api.mockResolvedValue({ messages: [], session_id: 'session-1' })

    await getSessionMessages('session-1', 'xiaoxuxu')

    expect(api).toHaveBeenCalledWith({
      path: '/api/sessions/session-1/messages?profile=xiaoxuxu',
      profile: 'xiaoxuxu'
    })
  })

  it('defaults model options to configured providers only', async () => {
    await getGlobalModelOptions()

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/model/options?explicit_only=1'
      })
    )
  })

  it('can opt into unconfigured providers for onboarding flows', async () => {
    await getGlobalModelOptions({ includeUnconfigured: true, refresh: true, explicitOnly: false })

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/model/options?refresh=1&include_unconfigured=1'
      })
    )
  })

  it('uses the dedicated Cortex catalog and atomic assignment contracts', async () => {
    await getCortexMemoryModelOptions({ refresh: true })

    expect(api).toHaveBeenLastCalledWith(
      expect.objectContaining({
        path: '/api/model/cortex-memory/options?refresh=1',
        timeoutMs: 60_000
      })
    )

    api.mockClear()
    await setCortexMemoryModelAssignment({
      provider: 'openrouter',
      model: 'google/gemini-3.1-flash-lite'
    })
    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/model/cortex-memory',
        method: 'PUT',
        body: {
          provider: 'openrouter',
          model: 'google/gemini-3.1-flash-lite'
        }
      })
    )
  })

  it('lets onboarding pin model reads and memory writes to a captured profile', async () => {
    await getGlobalModelOptions({
      includeUnconfigured: true,
      explicitOnly: false,
      profile: 'customer-west'
    })
    expect(api).toHaveBeenLastCalledWith(
      expect.objectContaining({
        path: '/api/model/options?include_unconfigured=1',
        profile: 'customer-west'
      })
    )

    await getCortexMemoryModelOptions({ profile: 'customer-west', refresh: true })
    expect(api).toHaveBeenLastCalledWith(
      expect.objectContaining({
        path: '/api/model/cortex-memory/options?refresh=1',
        profile: 'customer-west'
      })
    )

    await setCortexMemoryModelAssignment(
      { provider: 'openrouter', model: 'google/gemini-3.1-flash-lite' },
      { profile: 'customer-west' }
    )
    expect(api).toHaveBeenLastCalledWith(
      expect.objectContaining({
        path: '/api/model/cortex-memory',
        profile: 'customer-west'
      })
    )
  })
})
