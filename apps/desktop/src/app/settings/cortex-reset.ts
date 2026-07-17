type ConfigRecord = Record<string, unknown>

const asRecord = (value: unknown): ConfigRecord =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as ConfigRecord) : {}

/**
 * Reset ordinary Atlas settings without discarding a verified Cortex route.
 *
 * The backend defaults cannot invent a customer credential or self-managed
 * model route. A global reset therefore keeps the two already-verified route
 * records while restoring every other Cortex setting to product defaults.
 */
export function cortexSafeConfigDefaults(defaults: ConfigRecord, current: ConfigRecord): ConfigRecord {
  const defaultAuxiliary = asRecord(defaults.auxiliary)
  const currentAuxiliary = asRecord(current.auxiliary)
  const triage = asRecord(currentAuxiliary.cortex_triage)
  const reasoning = asRecord(currentAuxiliary.cortex_reasoning)

  if (!String(triage.provider ?? '').trim() || !String(triage.model ?? '').trim()) {
    throw new Error('Atlas cannot reset settings until the Cortex memory model is configured.')
  }

  if (!String(reasoning.provider ?? '').trim() || !String(reasoning.model ?? '').trim()) {
    throw new Error('Atlas cannot reset settings until the Cortex memory model is configured.')
  }

  return {
    ...defaults,
    auxiliary: {
      ...defaultAuxiliary,
      cortex_triage: { ...triage },
      cortex_reasoning: { ...reasoning }
    },
    cortex: {
      ...asRecord(defaults.cortex),
      enabled: true
    },
    memory: {
      ...asRecord(defaults.memory),
      provider: 'cortex'
    }
  }
}
