import type { StarmapNode } from '@/types/hermes'

import { cortexNodeAriaLabel } from './cortex'

export function CortexNodeList({
  nodes,
  onSelect,
  selectedId
}: {
  nodes: StarmapNode[]
  onSelect: (id: string) => void
  selectedId: null | string
}) {
  return (
    <details className="pointer-events-auto absolute right-3 top-16 z-20 w-64 rounded-xl border border-(--ui-stroke-secondary) bg-[color-mix(in_srgb,var(--ui-bg-elevated)_94%,transparent)] text-xs shadow-md backdrop-blur-md [-webkit-app-region:no-drag]">
      <summary className="cursor-pointer list-none px-3 py-2 font-medium">Browse graph nodes ({nodes.length})</summary>
      <ul
        aria-label="Atlas Cortex graph nodes"
        className="max-h-72 overflow-y-auto border-t border-(--ui-stroke-secondary) p-1"
      >
        {nodes.map(node => (
          <li key={node.id}>
            <button
              aria-label={cortexNodeAriaLabel(node)}
              aria-pressed={selectedId === node.id}
              className="block w-full rounded-lg px-2 py-1.5 text-left hover:bg-(--ui-control-hover-background) aria-pressed:bg-(--ui-control-active-background)"
              onClick={() => onSelect(node.id)}
              type="button"
            >
              <span className="block truncate font-medium">{node.label}</span>
              <span className="block truncate text-[0.66rem] text-muted-foreground">
                {node.cortexType} · {node.category} · {node.state}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </details>
  )
}
