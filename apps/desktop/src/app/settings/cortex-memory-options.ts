import type { CortexMemoryModelOptionsResponse, ModelOptionProvider } from '@/types/hermes'

/**
 * Reduce the setup-oriented Cortex catalog to rows safe for assignment.
 *
 * The API keeps unauthenticated providers and unavailable model metadata so
 * onboarding can explain what must be connected. Settings must never turn
 * those informational rows into selectable values.
 */
export function selectableCortexMemoryProviders(
  options: CortexMemoryModelOptionsResponse | null
): ModelOptionProvider[] {
  return (options?.providers ?? [])
    .filter(provider => provider.authenticated === true)
    .map(provider => ({
      ...provider,
      models: (provider.models ?? []).filter(model => {
        const capability = provider.memory_capabilities?.[model]

        return capability?.selectable === true && capability.structured_json === true
      })
    }))
    .filter(provider => (provider.models?.length ?? 0) > 0)
}
