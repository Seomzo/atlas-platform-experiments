import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $desktopOnboarding, type DesktopOnboardingState, type OnboardingContext } from '@/store/onboarding'
import type { CortexMemoryModelCapability, OAuthProvider } from '@/types/hermes'

import { FlowPanel } from './flow'

import { Picker, SetupBlueprint } from '.'

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

function memoryCapability(): CortexMemoryModelCapability {
  return {
    recommendation_rank: 1,
    recommended: true,
    selectable: true,
    structured_json: true,
    structured_json_validation: 'openrouter',
    tool_calling_required: false,
    unavailable_reason: ''
  }
}

function setProviders(providers: OAuthProvider[]) {
  $desktopOnboarding.set({
    configured: false,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  } satisfies DesktopOnboardingState)
}

const ctx: OnboardingContext = { requestGateway: async () => undefined as never }

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  delete (window.HTMLElement.prototype as { scrollIntoView?: () => void }).scrollIntoView

  try {
    window.localStorage.clear()
  } catch {
    // jsdom localStorage should always be present; ignore if not.
  }

  $desktopOnboarding.set({
    configured: null,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  })
})

describe('onboarding Picker', () => {
  it('features Nous Portal and hides other providers behind a disclosure', () => {
    setProviders([provider('anthropic', 'Anthropic Claude'), provider('nous', 'Nous Portal')])
    render(<Picker ctx={ctx} />)

    expect(screen.getByText('Nous Portal')).toBeTruthy()
    expect(screen.getByText('Recommended')).toBeTruthy()
    expect(screen.queryByText('Anthropic API Key')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Other providers' }))

    expect(screen.getByText('Anthropic API Key')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Collapse' })).toBeTruthy()
  })

  it('shows every provider directly when Nous Portal is absent', () => {
    setProviders([provider('anthropic', 'Anthropic Claude'), provider('openai-codex', 'OpenAI Codex / ChatGPT')])
    render(<Picker ctx={ctx} />)

    expect(screen.getByText('Anthropic API Key')).toBeTruthy()
    expect(screen.getByText('OpenAI OAuth (ChatGPT)')).toBeTruthy()
    expect(screen.queryByText('Other sign-in options')).toBeNull()
    expect(screen.queryByText('Recommended')).toBeNull()
  })

  it('offers "choose later" on first run and persists the skip', () => {
    setProviders([provider('nous', 'Nous Portal')])
    render(<Picker ctx={ctx} />)

    const skip = screen.getByRole('button', { name: "I'll choose a provider later" })

    fireEvent.click(skip)

    expect($desktopOnboarding.get().firstRunSkipped).toBe(true)
    expect(window.localStorage.getItem('atlas-onboarding-skipped-v1')).toBe('1')
  })

  it('hides "choose later" in manual (add-provider) mode', () => {
    setProviders([provider('nous', 'Nous Portal')])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), manual: true })
    render(<Picker ctx={ctx} />)

    expect(screen.queryByRole('button', { name: "I'll choose a provider later" })).toBeNull()
  })
})

describe('Atlas setup blueprint', () => {
  it('explains managed defaults before continuing to provider setup', () => {
    let continued = false

    render(<SetupBlueprint onContinue={() => (continued = true)} />)

    expect(screen.getByText('Configure the workstation once.')).toBeTruthy()
    expect(screen.getByText('Dedicated persistent profile')).toBeTruthy()
    expect(screen.getByText('Atlas Core + Jay Premium')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Connect intelligence' }))

    expect(continued).toBe(true)
  })
})

describe('Cortex memory model step', () => {
  const memoryFlow = {
    status: 'confirming_memory_model' as const,
    label: 'OpenRouter',
    mainProvider: 'openrouter',
    mainModel: 'frontier-model',
    currentProvider: 'openrouter',
    currentModel: 'cheap-memory-model',
    recommendedProvider: 'openrouter',
    recommendedModel: 'cheap-memory-model',
    providers: [
      {
        authenticated: true,
        name: 'OpenRouter',
        slug: 'openrouter',
        models: ['frontier-model', 'cheap-memory-model', 'alternate-memory-model'],
        memory_capabilities: {
          'frontier-model': memoryCapability(),
          'cheap-memory-model': memoryCapability(),
          'alternate-memory-model': memoryCapability()
        }
      }
    ],
    profile: 'default',
    saving: false,
    message: null
  }

  it('shows the selected route and explains its session-end privacy boundary', () => {
    render(<FlowPanel ctx={ctx} flow={memoryFlow} leaving={false} onBegin={() => undefined} />)

    expect(document.body.textContent?.toLowerCase()).toContain('choose a memory model')
    expect(screen.getByText('OpenRouter')).toBeTruthy()
    expect(screen.getByText('Separate route · session-end only')).toBeTruthy()
    expect(screen.getByText('Recommended')).toBeTruthy()
    expect(document.body.textContent).toContain('cheap-memory-model')
    expect(document.body.textContent).toContain('bounded session evidence')
    expect(document.body.textContent).toContain('never runs in the live turn')
    expect(screen.getByRole('button', { name: /Finish setup/ })).toBeTruthy()
  })

  it('disables the exact chat pair in the dedicated picker and allows a distinct choice', () => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        disconnect() {}
        observe() {}
        unobserve() {}
      }
    )
    Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn()
    })
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), configured: false, flow: memoryFlow })
    render(<FlowPanel ctx={ctx} flow={memoryFlow} leaving={false} onBegin={() => undefined} />)

    fireEvent.click(screen.getByRole('button', { name: 'Change' }))

    const chatModel = screen.getByLabelText('OpenRouter frontier-model, Chat model')
    expect(chatModel.getAttribute('aria-disabled')).toBe('true')

    fireEvent.click(screen.getByLabelText('OpenRouter alternate-memory-model'))

    expect($desktopOnboarding.get().flow).toMatchObject({
      status: 'confirming_memory_model',
      currentModel: 'alternate-memory-model'
    })
  })

  it('exposes catalog failures as an alert with retry and provider recovery', () => {
    render(
      <FlowPanel
        ctx={ctx}
        flow={{
          status: 'memory_model_error',
          label: 'OpenRouter',
          mainProvider: 'openrouter',
          mainModel: 'frontier-model',
          message: 'catalog offline',
          profile: 'default',
          connectableProvider: null,
          connecting: false,
          credentialMessage: null
        }}
        leaving={false}
        onBegin={() => undefined}
      />
    )

    expect(screen.getByRole('alert').textContent).toContain('catalog offline')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Pick a different provider' })).toBeTruthy()
  })

  it('offers a memory-only provider connection without changing the chat route', () => {
    render(
      <FlowPanel
        ctx={ctx}
        flow={{
          status: 'memory_model_error',
          label: 'Anthropic',
          mainProvider: 'anthropic',
          mainModel: 'claude-sonnet-4-6',
          message: 'No connected memory model is available.',
          profile: 'customer-west',
          connectableProvider: {
            authType: 'api_key',
            keyEnv: 'OPENROUTER_API_KEY',
            name: 'OpenRouter',
            slug: 'openrouter'
          },
          connecting: false,
          credentialMessage: null
        }}
        leaving={false}
        onBegin={() => undefined}
      />
    )

    expect(screen.getByText('Connect OpenRouter for memory')).toBeTruthy()
    expect(screen.getByText(/anthropic · claude-sonnet-4-6/)).toBeTruthy()
    expect(screen.getByText(/chat model unchanged/)).toBeTruthy()
    expect(screen.getByLabelText('OPENROUTER_API_KEY')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Connect and choose memory model' })).toBeTruthy()
  })
})
