import { describe, expect, it } from 'vitest'

import { appViewForPath, isOverlayView, PROFILES_ROUTE } from './routes'

describe('workers route', () => {
  it('keeps the existing deep link while rendering as a first-class page', () => {
    expect(PROFILES_ROUTE).toBe('/profiles')
    expect(appViewForPath(PROFILES_ROUTE)).toBe('profiles')
    expect(isOverlayView('profiles')).toBe(false)
  })
})
