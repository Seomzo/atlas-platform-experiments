import type { CronJob } from '@/types/hermes'

const CORTEX_WAKE_SCRIPT = 'atlas_cortex_wake.py'

export function isCortexSystemJob(job: CronJob): boolean {
  const script = job.script?.trim().toLowerCase().replaceAll('\\', '/') ?? ''
  const name = job.name?.trim().toLowerCase() ?? ''

  return (
    script.endsWith(`/${CORTEX_WAKE_SCRIPT}`) || script === CORTEX_WAKE_SCRIPT || name === 'atlas cortex dream (system)'
  )
}

export function publicCronJobs(jobs: CronJob[]): CronJob[] {
  return jobs.filter(job => !isCortexSystemJob(job))
}
