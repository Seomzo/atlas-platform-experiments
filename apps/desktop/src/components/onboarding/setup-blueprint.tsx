import { Button } from '@/components/ui/button'
import { Brain, CheckCircle2, ChevronRight, Link, Lock, Monitor, Package } from '@/lib/icons'

const SETUP_STAGES = [
  { index: '01', label: 'Account', state: 'managed' },
  { index: '02', label: 'Store', state: 'managed' },
  { index: '03', label: 'Intelligence', state: 'current' },
  { index: '04', label: 'Tekion access', state: 'next' },
  { index: '05', label: 'Permissions', state: 'queued' },
  { index: '06', label: 'Delivery', state: 'queued' }
] as const

const MANAGED_DEFAULTS = [
  {
    detail: 'Local, store-scoped worker',
    icon: Monitor,
    label: 'Workstation',
    meta: 'Managed by Atlas'
  },
  {
    detail: 'Dedicated persistent profile',
    icon: Link,
    label: 'Tekion browser',
    meta: 'Human takeover ready'
  },
  {
    detail: 'Atlas Core + Jay Premium',
    icon: Package,
    label: 'Dealership skills',
    meta: 'Signed and indexed'
  },
  {
    detail: 'Read first, approve changes',
    icon: Lock,
    label: 'Action policy',
    meta: 'Store isolated'
  }
] as const

interface SetupBlueprintProps {
  onContinue: () => void
}

export function SetupBlueprint({ onContinue }: SetupBlueprintProps) {
  return (
    <section
      aria-labelledby="atlas-setup-title"
      className="relative min-h-[36rem] overflow-hidden bg-[#061020] text-[#f5f8ff]"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[#3f7cff]" />
      <div className="pointer-events-none absolute -right-20 top-16 size-72 rounded-full border border-[#3274ff]/15" />
      <div className="pointer-events-none absolute -right-3 top-32 size-36 rounded-full border border-[#3274ff]/25" />

      <div className="relative grid min-h-[36rem] md:grid-cols-[13.5rem_1fr]">
        <aside className="border-b border-white/10 bg-[#08162a] px-5 py-6 md:border-r md:border-b-0">
          <div className="flex items-center gap-2.5">
            <div className="grid size-8 place-items-center border border-[#4e82ff]/45 bg-[#0b2145] text-[#7fa5ff]">
              <Brain className="size-4" />
            </div>
            <div>
              <p className="text-[0.65rem] font-semibold tracking-[0.22em] text-[#7fa5ff] uppercase">Atlas</p>
              <p className="text-xs text-white/55">Workstation setup</p>
            </div>
          </div>

          <ol className="mt-8 grid grid-cols-2 gap-x-5 gap-y-1 md:grid-cols-1">
            {SETUP_STAGES.map(stage => {
              const current = stage.state === 'current'

              return (
                <li
                  className={`relative flex min-h-11 items-center gap-3 border-l px-3 text-xs transition-colors ${
                    current ? 'border-[#4e82ff] bg-[#0d2346] text-white' : 'border-white/10 text-white/42'
                  }`}
                  key={stage.index}
                >
                  <span className={`font-mono text-[0.65rem] ${current ? 'text-[#7fa5ff]' : 'text-white/25'}`}>
                    {stage.index}
                  </span>
                  <span className="font-medium">{stage.label}</span>
                  {stage.state === 'managed' ? (
                    <CheckCircle2 aria-label="Managed default" className="ml-auto size-3.5 text-[#5f8eff]" />
                  ) : null}
                </li>
              )
            })}
          </ol>

          <div className="mt-7 border-t border-white/10 pt-4 text-[0.68rem] leading-5 text-white/38">
            <span className="block font-mono text-[#6f98ff]">CONFIG / 03</span>
            Infrastructure choices stay managed. Dealer choices stay visible.
          </div>
        </aside>

        <div className="flex min-w-0 flex-col px-6 py-7 sm:px-9 sm:py-9">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="font-mono text-[0.65rem] tracking-[0.18em] text-[#6f98ff] uppercase">Configuration preview</p>
            <span className="border border-[#315fae]/50 bg-[#0a1d3a] px-2.5 py-1 text-[0.62rem] font-medium tracking-[0.1em] text-[#91afff] uppercase">
              Design-partner default
            </span>
          </div>

          <div className="mt-7 max-w-[39rem]">
            <h1
              className="text-3xl font-semibold leading-[1.05] tracking-[-0.035em] sm:text-4xl"
              id="atlas-setup-title"
            >
              Configure the workstation once.
              <span className="mt-1 block text-[#76a0ff]">Then let Atlas run it.</span>
            </h1>
            <p className="mt-4 max-w-[36rem] text-sm leading-6 text-white/55">
              Atlas will own the model routing, local runtime, browser profile, and skill versions. Your team will
              choose the store, Tekion access, approvals, and delivery channels.
            </p>
          </div>

          <div className="mt-8 border-y border-white/10">
            {MANAGED_DEFAULTS.map(({ detail, icon: Icon, label, meta }) => (
              <div
                className="grid gap-2 border-b border-white/10 py-3.5 last:border-b-0 sm:grid-cols-[1.1rem_8.5rem_1fr_auto] sm:items-center sm:gap-3"
                key={label}
              >
                <Icon className="size-4 text-[#6e97ff]" />
                <span className="text-xs font-medium text-white/58">{label}</span>
                <span className="text-[0.82rem] font-medium text-white/92">{detail}</span>
                <span className="text-[0.68rem] text-white/38">{meta}</span>
              </div>
            ))}
          </div>

          <div className="mt-auto flex flex-col gap-4 pt-7 sm:flex-row sm:items-end sm:justify-between">
            <div className="max-w-sm">
              <p className="text-xs font-medium text-[#8baaff]">Current build</p>
              <p className="mt-1 text-[0.72rem] leading-5 text-white/42">
                Account and store enrollment are coming next. For now, connect the intelligence provider that powers
                this development workstation.
              </p>
            </div>
            <Button
              className="h-10 shrink-0 rounded-none border border-[#6e97ff]/40 bg-[#2364ec] px-4 text-xs font-semibold text-white shadow-[0_0_24px_rgba(35,100,236,0.24)] hover:bg-[#3273ff]"
              onClick={onContinue}
              type="button"
            >
              Connect intelligence
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      </div>
    </section>
  )
}
