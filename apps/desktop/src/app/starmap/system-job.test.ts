import { describe, expect, it } from 'vitest'

import type { CronJob } from '@/types/hermes'

import { isCortexSystemJob, publicCronJobs } from './system-job'

const job = (overrides: Partial<CronJob>): CronJob => ({ enabled: true, id: 'job-1', ...overrides })

describe('Cortex system job classification', () => {
  it('recognizes the installed Cortex maintenance job by name or script', () => {
    expect(isCortexSystemJob(job({ name: 'Atlas Cortex Dream (system)' }))).toBe(true)
    expect(isCortexSystemJob(job({ script: '/Users/omar/.atlas/atlas_cortex_wake.py' }))).toBe(true)
    expect(isCortexSystemJob(job({ script: 'atlas_cortex_wake.py' }))).toBe(true)
  })

  it('does not hide similarly named user automations', () => {
    expect(isCortexSystemJob(job({ name: 'Summarize Cortex research', prompt: 'Write a weekly summary' }))).toBe(false)
    expect(isCortexSystemJob(job({ name: 'Atlas Cortex Dream', script: 'my_memory_backup.py' }))).toBe(false)
  })

  it('keeps the public Cron Jobs list focused on user-created work', () => {
    const userJob = job({ id: 'user', name: 'Morning report' })
    const systemJob = job({ id: 'system', script: 'atlas_cortex_wake.py' })

    expect(publicCronJobs([systemJob, userJob])).toEqual([userJob])
  })
})
