import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  checkHermesUpdate,
  getActionStatus,
  getCortexGraph,
  getCortexHealth,
  getStatus,
  restartGateway,
  setApiRequestProfile,
  updateHermes
} from './hermes'

// Contract: every backend-targeted action helper must carry the active gateway
// profile, so a multi-profile / global-remote user's restart, status poll, and
// update hit the backend they're actually on — not the primary/default. The
// System-panel "restart does nothing" bug was these helpers dropping it.
describe('backend action helpers are profile-scoped', () => {
  const api = vi.fn(async (_req: { path: string; profile?: string }) => ({}) as never)

  beforeEach(() => {
    ;(window as { hermesDesktop?: unknown }).hermesDesktop = { api }
    api.mockClear()
  })

  afterEach(() => {
    setApiRequestProfile(null)
    delete (window as { hermesDesktop?: unknown }).hermesDesktop
  })

  const lastProfile = () => api.mock.calls.at(-1)?.[0].profile

  it('omits profile when none is active (single-profile users unaffected)', () => {
    void getStatus()
    expect(lastProfile()).toBeUndefined()
  })

  it('forwards the active profile to every backend action', () => {
    setApiRequestProfile('coder')

    void getStatus()
    void restartGateway()
    void updateHermes()
    void checkHermesUpdate()
    void getActionStatus('gateway-restart')

    for (const call of api.mock.calls) {
      expect(call[0].profile).toBe('coder')
    }
  })

  it('routes an explicitly selected Cortex brain through the primary backend query', async () => {
    setApiRequestProfile('coder')
    api.mockResolvedValueOnce({ version: 'atlas.cortex.graph.v1' } as never)

    await getCortexGraph(250, 'research worker')

    expect(api).toHaveBeenLastCalledWith({
      path: '/api/cognitive/graph?limit=250&projection=growth&profile=research%20worker'
    })
  })

  it('keeps implicit Cortex reads on the active profile backend', async () => {
    setApiRequestProfile('coder')
    api.mockResolvedValueOnce({ version: 'atlas.cortex.health.v1' } as never)

    await getCortexHealth()

    expect(api).toHaveBeenLastCalledWith({ path: '/api/cognitive/health', profile: 'coder' })
  })
})
