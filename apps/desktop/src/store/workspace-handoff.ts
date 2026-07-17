import { atom } from 'nanostores'

// A Git create/switch has crossed semantic close and entered its irreversible
// critical section. Destination changes reject briefly instead of claiming to
// supersede an operation that the OS may already have applied.
export const $workspaceMutationActive = atom(false)

export function setWorkspaceMutationActive(active: boolean): void {
  $workspaceMutationActive.set(active)
}
