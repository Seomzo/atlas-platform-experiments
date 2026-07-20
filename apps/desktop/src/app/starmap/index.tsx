import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { getProfiles } from '@/hermes'
import { useI18n } from '@/i18n'
import {
  $starmapBrainProfile,
  $starmapError,
  $starmapGraph,
  $starmapLoading,
  loadStarmapGraph,
  selectStarmapBrain
} from '@/store/starmap'
import type { ProfileInfo, StarmapGraph } from '@/types/hermes'

import { Panel, PanelEmpty } from '../overlays/panel'

import { CortexWorkspace } from './cortex-workspace'
import { StarMap } from './star-map'

// Star map overlay: a top-down map of what Atlas has learned for a profile,
// over a radial time axis. Data is fetched on demand into the $starmap* atoms;
// the map itself lives in ./star-map. The chrome is owned by the map itself
// (timeline scrubber + legend float over the canvas), so there's no panel
// header here.
export function StarmapView({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const graph = useStore($starmapGraph)
  const loading = useStore($starmapLoading)
  const error = useStore($starmapError)
  const brainProfile = useStore($starmapBrainProfile)

  // A pasted share code populates the map with someone else's (or an exported)
  // graph, overriding the live profile scan. Cleared by "back to my map" and
  // whenever a fresh profile graph loads in.
  const [imported, setImported] = useState<StarmapGraph | null>(null)
  const [selectedCortexNode, setSelectedCortexNode] = useState<null | string>(null)
  const [profiles, setProfiles] = useState<ProfileInfo[]>([])

  useEffect(() => {
    void loadStarmapGraph()
    void getProfiles().then(
      result => setProfiles(result.profiles),
      () => setProfiles([])
    )
  }, [])

  // Drop a stale import when the underlying profile graph changes out from under it.
  useEffect(() => {
    setImported(null)
    setSelectedCortexNode(null)
  }, [graph])

  const shown = imported ?? graph
  const cortex = !imported && shown?.source === 'cortex'

  return (
    <Panel
      className={
        cortex
          ? 'border-white/10! bg-[#050b14]! [--chrome-action-hover:rgba(255,255,255,0.08)] [--ui-text-tertiary:#9db2c7]'
          : undefined
      }
      closeLabel={t.starmap.close}
      contentClassName={cortex ? 'p-0!' : undefined}
      onClose={onClose}
    >
      {error ? (
        <PanelEmpty description={error} icon="warning" title={t.starmap.loadFailed} />
      ) : !shown && loading ? (
        <PageLoader aria-label={t.starmap.loading} className="min-h-0 flex-1" />
      ) : shown && shown.nodes.length === 0 && !imported && !cortex ? (
        <PanelEmpty description={t.starmap.emptyDesc} icon="lightbulb" title={t.starmap.emptyTitle} />
      ) : shown && cortex ? (
        <CortexWorkspace
          brainProfile={brainProfile}
          graph={shown}
          onBrainChange={profile => {
            setImported(null)
            setSelectedCortexNode(null)
            void selectStarmapBrain(profile)
          }}
          onSelectNode={setSelectedCortexNode}
          profiles={profiles}
          selectedNodeId={selectedCortexNode}
        />
      ) : shown ? (
        <div className="flex min-h-0 flex-1">
          <div className="relative min-w-0 flex-1">
            <StarMap
              graph={shown}
              imported={imported !== null}
              onImport={setImported}
              onNodeSelect={undefined}
              onResetMap={() => setImported(null)}
              selectedNodeId={undefined}
            />
          </div>
        </div>
      ) : null}
    </Panel>
  )
}
