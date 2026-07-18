import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useEffect, useState } from 'react'

import { avatarUrl } from '@/hermes'
import { profileColorSoft, resolveProfileColor } from '@/lib/profile-color'
import { cn } from '@/lib/utils'
import { $profileAvatarUpdates, $profileColors, normalizeProfileKey } from '@/store/profile'
import type { ProfileInfo } from '@/types/hermes'

interface WorkerAvatarProps extends Omit<React.ComponentProps<'span'>, 'children'> {
  imageClassName?: string
  profile: Pick<ProfileInfo, 'display_name' | 'has_avatar' | 'name'>
}

export function WorkerAvatar({ className, imageClassName, profile, style, ...props }: WorkerAvatarProps) {
  const colors = useStore($profileColors)
  const updates = useStore($profileAvatarUpdates)
  const [src, setSrc] = useState<null | string>(null)
  const [failed, setFailed] = useState(false)
  const color = resolveProfileColor(profile.name, colors)
  const hue = color ?? 'var(--ui-text-quaternary)'
  const label = profile.display_name.trim() || profile.name
  const initial = label.replace(/[^\p{L}\p{N}]/gu, '').charAt(0).toUpperCase() || '?'
  const updatedAt = updates[normalizeProfileKey(profile.name)] ?? 0

  useEffect(() => {
    let cancelled = false

    setFailed(false)
    setSrc(null)

    if (!profile.has_avatar) {
      return
    }

    void avatarUrl(profile.name, updatedAt)
      .then(url => !cancelled && setSrc(url))
      .catch(() => !cancelled && setFailed(true))

    return () => void (cancelled = true)
  }, [profile.has_avatar, profile.name, updatedAt])

  return (
    <span
      aria-hidden="true"
      className={cn(
        'relative grid shrink-0 place-items-center overflow-hidden font-semibold uppercase leading-none',
        className
      )}
      style={{ backgroundColor: profileColorSoft(hue, 22), color: color ?? undefined, ...style }}
      {...props}
    >
      {initial}
      {src && !failed ? (
        <img
          alt=""
          className={cn('absolute inset-0 size-full object-cover', imageClassName)}
          onError={() => setFailed(true)}
          src={src}
        />
      ) : null}
    </span>
  )
}
