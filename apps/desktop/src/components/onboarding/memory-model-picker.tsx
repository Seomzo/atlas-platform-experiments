import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { normalize } from '@/lib/text'
import { cn } from '@/lib/utils'
import type { OnboardingFlow } from '@/store/onboarding'
import type { ModelOptionProvider } from '@/types/hermes'

type MemoryFlow = Extract<OnboardingFlow, { status: 'confirming_memory_model' }>

export function MemoryModelPickerDialog({
  flow,
  onOpenChange,
  onSelect,
  open
}: {
  flow: MemoryFlow
  onOpenChange: (open: boolean) => void
  onSelect: (provider: string, model: string) => void
  open: boolean
}) {
  const { t } = useI18n()
  const copy = t.onboarding.memoryModel
  const [search, setSearch] = useState('')
  const query = normalize(search)

  const matches = (provider: ModelOptionProvider, model: string) =>
    !query ||
    model.toLowerCase().includes(query) ||
    provider.name.toLowerCase().includes(query) ||
    provider.slug.toLowerCase().includes(query)

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="z-[1310] max-h-[85vh] max-w-2xl gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border px-4 py-3">
          <DialogTitle>{copy.pickerTitle}</DialogTitle>
          <DialogDescription className="text-xs leading-relaxed">{copy.pickerDescription}</DialogDescription>
        </DialogHeader>

        <Command className="rounded-none bg-card" shouldFilter={false}>
          <CommandInput autoFocus onValueChange={setSearch} placeholder={copy.search} value={search} />
          <CommandList className="max-h-96">
            <CommandEmpty>{copy.noModels}</CommandEmpty>
            {flow.providers.map(provider => {
              const models = (provider.models ?? []).map(String).filter(model => matches(provider, model))

              if (models.length === 0) {
                return null
              }

              const unavailable = new Set((provider.unavailable_models ?? []).map(String))

              return (
                <CommandGroup
                  heading={
                    <span className="flex items-center gap-2">
                      <span>{provider.name}</span>
                      <span className="font-mono text-[0.6rem] font-normal text-muted-foreground">{provider.slug}</span>
                    </span>
                  }
                  key={provider.slug}
                >
                  {models.map(model => {
                    const isMain =
                      provider.slug.trim().toLowerCase() === flow.mainProvider.trim().toLowerCase() &&
                      model.trim() === flow.mainModel.trim()

                    const disconnected = provider.authenticated !== true
                    const capability = provider.memory_capabilities?.[model]

                    const locked =
                      unavailable.has(model) || capability?.selectable !== true || capability.structured_json !== true

                    const disabled = isMain || disconnected || locked

                    const selected =
                      provider.slug.trim().toLowerCase() === flow.currentProvider.trim().toLowerCase() &&
                      model === flow.currentModel

                    const recommended =
                      provider.slug.trim().toLowerCase() === flow.recommendedProvider.trim().toLowerCase() &&
                      model === flow.recommendedModel

                    return (
                      <CommandItem
                        aria-label={`${provider.name} ${model}${isMain ? `, ${copy.chatModel}` : ''}`}
                        className={cn(
                          'flex items-center gap-2 pl-6 font-mono',
                          selected &&
                            'bg-primary text-primary-foreground data-[selected=true]:bg-primary data-[selected=true]:text-primary-foreground',
                          disabled && 'cursor-not-allowed opacity-45'
                        )}
                        disabled={disabled}
                        key={`${provider.slug}:${model}`}
                        onSelect={() => {
                          if (!disabled) {
                            onSelect(provider.slug, model)
                            onOpenChange(false)
                          }
                        }}
                        title={locked ? capability?.unavailable_reason || copy.unavailable : undefined}
                        value={`${provider.slug}:${model}`}
                      >
                        <span className="min-w-0 flex-1 truncate">{model}</span>
                        {recommended && (
                          <span className="shrink-0 text-[0.6rem] uppercase tracking-wide">{copy.recommended}</span>
                        )}
                        {isMain && (
                          <span className="shrink-0 text-[0.6rem] uppercase tracking-wide">{copy.chatModel}</span>
                        )}
                        {!isMain && disconnected && (
                          <span className="shrink-0 text-[0.6rem] uppercase tracking-wide">{copy.notConnected}</span>
                        )}
                        {!isMain && !disconnected && locked && (
                          <span className="shrink-0 text-[0.6rem] uppercase tracking-wide">{copy.unavailable}</span>
                        )}
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              )
            })}
          </CommandList>
        </Command>

        <DialogFooter className="bg-card p-3">
          <Button onClick={() => onOpenChange(false)} variant="outline">
            {t.common.cancel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
