import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Radix Select calls scrollIntoView on its items when the content opens; jsdom
// doesn't implement it (nor hasPointerCapture / releasePointerCapture), so stub
// them to let the dropdown open in tests.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelInfo = vi.fn()
const getGlobalModelOptions = vi.fn()
const getAuxiliaryModels = vi.fn()
const getCortexMemoryModelOptions = vi.fn()
const getMoaModels = vi.fn()
const setModelAssignment = vi.fn()
const getRecommendedDefaultModel = vi.fn()
const saveMoaModels = vi.fn()
const setEnvVar = vi.fn()
const getHermesConfigRecord = vi.fn()
const saveHermesConfig = vi.fn()
const startManualProviderOAuth = vi.fn()
const startManualLocalEndpoint = vi.fn()

vi.mock('@/hermes', () => ({
  getGlobalModelInfo: () => getGlobalModelInfo(),
  getGlobalModelOptions: () => getGlobalModelOptions(),
  getAuxiliaryModels: () => getAuxiliaryModels(),
  getCortexMemoryModelOptions: () => getCortexMemoryModelOptions(),
  getMoaModels: () => getMoaModels(),
  setModelAssignment: (body: unknown) => setModelAssignment(body),
  getRecommendedDefaultModel: (slug: string) => getRecommendedDefaultModel(slug),
  saveMoaModels: (body: unknown) => saveMoaModels(body),
  setEnvVar: (key: string, value: string) => setEnvVar(key, value),
  setApiRequestProfile: vi.fn(),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  saveHermesConfig: (config: unknown) => saveHermesConfig(config)
}))

vi.mock('@/store/onboarding', () => ({
  startManualProviderOAuth: (slug: string) => startManualProviderOAuth(slug),
  startManualLocalEndpoint: () => startManualLocalEndpoint()
}))

beforeEach(() => {
  getGlobalModelInfo.mockResolvedValue({ provider: 'nous', model: 'hermes-4' })
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        name: 'Nous',
        slug: 'nous',
        models: ['hermes-4', 'hermes-4-mini'],
        authenticated: true,
        capabilities: { 'hermes-4': { reasoning: true, fast: true } }
      }
    ]
  })
  getAuxiliaryModels.mockResolvedValue({
    main: { provider: 'nous', model: 'hermes-4' },
    tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
  })
  getCortexMemoryModelOptions.mockResolvedValue({
    recommended: { provider: 'openrouter', model: 'google/gemini-3.1-flash-lite' },
    providers: [
      {
        name: 'OpenRouter',
        slug: 'openrouter',
        models: ['google/gemini-3.1-flash-lite', 'google/gemini-2.5-flash-lite'],
        authenticated: true,
        memory_capabilities: {
          'google/gemini-3.1-flash-lite': {
            selectable: true,
            structured_json: true
          },
          'google/gemini-2.5-flash-lite': {
            selectable: true,
            structured_json: true
          }
        }
      }
    ],
    current: {
      configured: false,
      valid: false,
      triage: {
        provider: '',
        model: '',
        configured: false,
        valid: false,
        unavailable_reason: 'a dedicated explicit provider and model are required'
      },
      reasoning: {
        provider: '',
        model: '',
        configured: false,
        valid: false,
        unavailable_reason: 'a dedicated explicit provider and model are required'
      }
    },
    tasks: ['cortex_triage', 'cortex_reasoning'],
    constraints: {
      explicit_route_required: true,
      separate_from_main: true,
      structured_json_required: true,
      tool_calling_required: false
    }
  })
  getMoaModels.mockResolvedValue(null)
  setModelAssignment.mockResolvedValue({ provider: 'nous', model: 'hermes-4', gateway_tools: [] })
  getRecommendedDefaultModel.mockResolvedValue({ provider: 'nous', model: 'hermes-4', free_tier: null })
  setEnvVar.mockResolvedValue({ ok: true })
  getHermesConfigRecord.mockResolvedValue({ agent: { reasoning_effort: 'medium', service_tier: 'normal' } })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderModelSettings() {
  const { ModelSettings } = await import('./model-settings')
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={queryClient}>
      <ModelSettings />
    </QueryClientProvider>
  )
}

describe('ModelSettings', () => {
  it('loads the current main model and lists configured providers only', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    // Open the provider Select — only configured providers should be listed.
    const triggers = await screen.findAllByRole('combobox')
    fireEvent.click(triggers[0])

    // "Nous" shows in both the trigger and the open list.
    expect((await screen.findAllByText('Nous')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/DeepSeek/)).toBeNull()
  })

  it('writes the profile default speed (service_tier) when the fast switch is toggled', async () => {
    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    const fastSwitch = await screen.findByRole('switch')
    fireEvent.click(fastSwitch)

    await waitFor(() =>
      expect(saveHermesConfig).toHaveBeenCalledWith(
        expect.objectContaining({ agent: expect.objectContaining({ service_tier: 'fast' }) })
      )
    )
  })

  it('hides the reasoning/speed defaults when the main model reports no capabilities', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: false, fast: false } }
        }
      ]
    })

    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('renders the auxiliary task rows', async () => {
    await renderModelSettings()

    expect(await screen.findByText('Vision')).toBeTruthy()
    expect(screen.getAllByText('auto · use main model').length).toBeGreaterThan(0)
    expect(getCortexMemoryModelOptions).toHaveBeenCalled()
  })

  it('uses the Cortex catalog and never offers Cortex a set-to-main action', async () => {
    await renderModelSettings()

    expect(await screen.findByText('Cortex triage')).toBeTruthy()
    // Eight generic helpers retain the action; the two Cortex rows do not.
    expect(screen.getAllByRole('button', { name: 'Set to main' })).toHaveLength(8)

    const changeButtons = screen.getAllByRole('button', { name: 'Change' })
    fireEvent.click(changeButtons[8])
    const selects = screen.getAllByRole('combobox')
    fireEvent.click(selects[selects.length - 1])

    expect((await screen.findAllByText('google/gemini-3.1-flash-lite')).length).toBeGreaterThan(0)
    expect(screen.queryByText('hermes-4-mini')).toBeNull()
  })

  it('does not enable unauthenticated or unverified Cortex catalog rows', async () => {
    getCortexMemoryModelOptions.mockResolvedValueOnce({
      recommended: { provider: '', model: '' },
      providers: [
        {
          name: 'OpenRouter',
          slug: 'openrouter',
          models: ['google/gemini-3.1-flash-lite'],
          authenticated: false,
          memory_capabilities: {
            'google/gemini-3.1-flash-lite': {
              selectable: true,
              structured_json: true
            }
          }
        },
        {
          name: 'Unverified provider',
          slug: 'unverified',
          models: ['unverified-memory-model'],
          authenticated: true,
          memory_capabilities: {
            'unverified-memory-model': {
              selectable: false,
              structured_json: false
            }
          }
        }
      ],
      current: {
        configured: false,
        valid: false,
        triage: { provider: '', model: '', configured: false, valid: false, unavailable_reason: 'required' },
        reasoning: { provider: '', model: '', configured: false, valid: false, unavailable_reason: 'required' }
      },
      tasks: ['cortex_triage', 'cortex_reasoning'],
      constraints: {
        explicit_route_required: true,
        separate_from_main: true,
        structured_json_required: true,
        tool_calling_required: false
      }
    })

    await renderModelSettings()
    expect(await screen.findByText('Cortex triage')).toBeTruthy()

    const changeButtons = screen.getAllByRole('button', { name: 'Change' })
    expect((changeButtons[8] as HTMLButtonElement).disabled).toBe(true)
    expect((changeButtons[9] as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(changeButtons[8])
    expect(screen.queryByText('google/gemini-3.1-flash-lite')).toBeNull()
    expect(screen.queryByText('unverified-memory-model')).toBeNull()
  })

  it('assigns an auxiliary task to the main model via setModelAssignment', async () => {
    await renderModelSettings()

    // One "Set to main" button per task slot; the first is Vision.
    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'hermes-4',
        provider: 'nous',
        scope: 'auxiliary',
        task: 'vision'
      })
    )
  })

  it('warns when a main switch leaves auxiliary tasks pinned to another provider', async () => {
    setModelAssignment.mockResolvedValueOnce({
      provider: 'openrouter',
      model: 'anthropic/claude-opus-4.7',
      gateway_tools: [],
      stale_aux: [{ task: 'compression', provider: 'nous', model: 'hermes-4' }]
    })

    await renderModelSettings()
    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())

    const applyButton = await screen.findByRole('button', { name: 'Apply' })
    fireEvent.click(applyButton)

    // The switch-time notice names the pinned provider and offers a reset.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
    expect(screen.getByText('nous')).toBeTruthy()
  })

  it('shows a persistent banner when a loaded aux slot mismatches the main provider', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [{ task: 'curator', provider: 'openrouter', model: 'anthropic/claude-opus-4.7', base_url: '' }]
    })

    await renderModelSettings()

    // Banner present on load, no switch required.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
  })

  it('does not treat a dedicated Cortex provider as a stale reset-to-main warning', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [
        {
          task: 'cortex_triage',
          provider: 'openrouter',
          model: 'google/gemini-3.1-flash-lite',
          base_url: ''
        }
      ]
    })

    await renderModelSettings()
    expect(await screen.findByText('Cortex triage')).toBeTruthy()
    expect(screen.queryByText(/still run on/)).toBeNull()
  })
})
