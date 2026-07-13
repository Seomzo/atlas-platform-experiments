const IS_ATLAS = process.env.HERMES_PUBLIC_BRAND?.trim().toLowerCase() === 'atlas'

export const AGENT_NAME = IS_ATLAS ? 'Atlas' : 'Hermes Agent'
export const COMMAND_NAME = IS_ATLAS ? 'atlas' : 'hermes'
export const SHORT_NAME = IS_ATLAS ? 'Atlas' : 'Hermes'
