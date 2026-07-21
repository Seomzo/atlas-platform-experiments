import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { STARMAP_ROUTE } from '@/app/routes'
import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'
import { getProfiles } from '@/hermes'
import { useI18n } from '@/i18n'
import {
  $starmapBrainProfile,
  $starmapBrainStatus,
  $starmapError,
  $starmapGraph,
  $starmapLoading,
  $starmapMode,
  $starmapNebula,
  selectStarmapBrain,
  showStarmapNebula
} from '@/store/starmap'
import type { ProfileInfo, StarmapGraph } from '@/types/hermes'

import { Panel, PanelEmpty } from '../overlays/panel'

import { CortexWorkspace } from './cortex-workspace'
import { normalizeNebulaProfiles } from './nebula'
import { NebulaOverview } from './nebula-overview'
import { decodeStarmapViewState, encodeStarmapViewState } from './share-code'
import { StarMap } from './star-map'

function BrainUnavailable({
  brainName,
  detail,
  disabled,
  onBack,
  title
}: {
  brainName: string
  detail: string
  disabled: boolean
  onBack: () => void
  title: string
}) {
  const { t } = useI18n()

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#050b14] text-[#e9f5ff]">
      <header className="shrink-0 border-b border-white/8 bg-[#07101c]/94 px-6 py-4">
        <button
          className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-[0.68rem] text-[#9ab0c4] transition hover:bg-white/[0.07] hover:text-white"
          onClick={onBack}
          type="button"
        >
          <Codicon name="arrow-left" size="0.75rem" />
          {t.starmap.cortex.nebula.backToNebula}
          <span className="text-white/20">/</span>
          <span className="text-[#d9efff]">{brainName}</span>
        </button>
      </header>
      <div className="relative grid min-h-0 flex-1 place-items-center overflow-hidden px-6 text-center">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              'radial-gradient(circle at 50% 48%, rgba(43,131,185,.15), transparent 28%), linear-gradient(rgba(92,158,200,.022) 1px, transparent 1px), linear-gradient(90deg, rgba(92,158,200,.022) 1px, transparent 1px)',
            backgroundSize: 'auto, 30px 30px, 30px 30px'
          }}
        />
        <div className="relative max-w-sm">
          <div
            className={`mx-auto grid size-16 place-items-center rounded-full border ${
              disabled
                ? 'border-[#d48787]/24 bg-[#d48787]/7 text-[#cb8d92]'
                : 'border-[#c7a579]/20 bg-[#c7a579]/7 text-[#bea077]'
            }`}
          >
            <Codicon name={disabled ? 'circle-slash' : 'debug-disconnect'} size="1.35rem" />
          </div>
          <h1 className="mt-5 text-lg font-semibold text-white">{title}</h1>
          <p className="mt-2 text-xs leading-relaxed text-[#748ba1]">{detail}</p>
        </div>
      </div>
    </div>
  )
}

// The default route is the shared nebula. A `view=brain&brain=...` query
// is a validated deep link into the existing isolated Cortex workspace.
export function StarmapView({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const location = useLocation()
  const navigate = useNavigate()
  const graph = useStore($starmapGraph)
  const loading = useStore($starmapLoading)
  const error = useStore($starmapError)
  const brainProfile = useStore($starmapBrainProfile)
  const brainStatus = useStore($starmapBrainStatus)
  const nebula = useStore($starmapNebula)
  const mode = useStore($starmapMode)

  const [imported, setImported] = useState<StarmapGraph | null>(null)
  const [selectedCortexNode, setSelectedCortexNode] = useState<null | string>(null)
  const [profiles, setProfiles] = useState<ProfileInfo[]>([])
  const [profilesLoaded, setProfilesLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false

    void getProfiles().then(
      result => {
        if (!cancelled) {
          setProfiles(result.profiles)
          setProfilesLoaded(true)
        }
      },
      () => {
        if (!cancelled) {
          setProfiles([])
          setProfilesLoaded(true)
        }
      }
    )

    return () => void (cancelled = true)
  }, [])

  const brains = useMemo(() => normalizeNebulaProfiles(profiles), [profiles])

  useEffect(() => {
    if (!profilesLoaded) {
      return
    }

    const state = decodeStarmapViewState(location.search)
    const knownBrain = brains.some(profile => profile.name === state.brainProfile)

    if (state.mode === 'brain' && knownBrain) {
      void selectStarmapBrain(state.brainProfile)
    } else {
      if (state.mode === 'brain') {
        navigate(STARMAP_ROUTE, { replace: true })
      }

      void showStarmapNebula(brains)
    }
  }, [brains, location.search, navigate, profilesLoaded])

  useEffect(() => {
    setImported(null)
    setSelectedCortexNode(null)
  }, [graph, mode])

  const openBrain = (profile: string) => {
    navigate(`${STARMAP_ROUTE}?${encodeStarmapViewState({ brainProfile: profile, mode: 'brain' })}`)
  }

  const backToNebula = () => navigate(STARMAP_ROUTE)
  const selectedProfile = brains.find(profile => profile.name === brainProfile)
  const brainName = selectedProfile?.display_name.trim() || selectedProfile?.name || brainProfile
  const shown = imported ?? graph
  const cortex = mode === 'brain' && !imported && shown?.source === 'cortex'
  const immersive = mode === 'nebula' || cortex || brainStatus === 'disabled' || brainStatus === 'unavailable'
  const copy = t.starmap.cortex.nebula

  return (
    <Panel
      className={
        immersive
          ? 'border-white/10! bg-[#050b14]! [--chrome-action-hover:rgba(255,255,255,0.08)] [--ui-text-tertiary:#9db2c7]'
          : undefined
      }
      closeLabel={t.starmap.close}
      contentClassName={immersive ? 'p-0!' : undefined}
      onClose={onClose}
    >
      {!profilesLoaded || (loading && mode === 'nebula' && !nebula) ? (
        <PageLoader aria-label={t.starmap.loading} className="min-h-0 flex-1" />
      ) : mode === 'nebula' && nebula ? (
        <NebulaOverview onIsolateBrain={openBrain} scene={nebula} />
      ) : mode === 'brain' && brainStatus === 'disabled' ? (
        <BrainUnavailable
          brainName={brainName}
          detail={copy.disabledBrainDescription(brainName)}
          disabled
          onBack={backToNebula}
          title={copy.disabledBrainTitle}
        />
      ) : mode === 'brain' && brainStatus === 'unavailable' ? (
        <BrainUnavailable
          brainName={brainName}
          detail={copy.unavailableBrainDescription(brainName)}
          disabled={false}
          onBack={backToNebula}
          title={copy.unavailableBrainTitle}
        />
      ) : error ? (
        <PanelEmpty description={error} icon="warning" title={t.starmap.loadFailed} />
      ) : !shown && loading ? (
        <PageLoader aria-label={t.starmap.loading} className="min-h-0 flex-1" />
      ) : shown && shown.nodes.length === 0 && !imported && !cortex ? (
        <PanelEmpty description={t.starmap.emptyDesc} icon="lightbulb" title={t.starmap.emptyTitle} />
      ) : shown && cortex ? (
        <CortexWorkspace
          brainName={brainName}
          brainProfile={brainProfile}
          graph={shown}
          onBack={backToNebula}
          onSelectNode={setSelectedCortexNode}
          selectedNodeId={selectedCortexNode}
        />
      ) : shown ? (
        <div className="flex min-h-0 flex-1 flex-col">
          {mode === 'brain' ? (
            <div className="shrink-0 border-b px-4 py-2">
              <button
                className="text-xs text-muted-foreground hover:text-foreground"
                onClick={backToNebula}
                type="button"
              >
                ← {copy.backToNebula} / {brainName}
              </button>
            </div>
          ) : null}
          <div className="relative min-h-0 min-w-0 flex-1">
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
