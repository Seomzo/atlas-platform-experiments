import { useEffect, useMemo, useRef, useState } from 'react'

import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { Button } from '@/components/ui/button'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { AtSign, Send } from '@/lib/icons'
import { cn } from '@/lib/utils'
import type { AtlasChannel } from '@/store/channels'
import type { ProfileInfo } from '@/types/hermes'

import {
  composerPlainText,
  deleteChipBeforeCaret,
  insertPlainTextAtCaret,
  normalizeComposerEditorDom,
  placeCaretEnd
} from '../chat/composer/rich-editor'
import { detectTrigger, textBeforeCaret } from '../chat/composer/text-utils'

import {
  filterMentionCandidates,
  mentionAutocompleteKey,
  type MentionAutocompleteState,
  nextMentionAutocompleteState
} from './mention-autocomplete'
import { channelMentionToken, channelMessageParts } from './routing'

interface ChannelComposerProps {
  channel: AtlasChannel
  memberProfiles: ProfileInfo[]
  onSend: (text: string) => Promise<{ targets: string[] }>
  sending: boolean
}

interface MemberCandidate {
  name: string
  profile: ProfileInfo
  workerId: string
}

function appendText(target: DocumentFragment | HTMLElement, text: string) {
  text.split('\n').forEach((line, index) => {
    if (index > 0) {
      target.append(document.createElement('br'))
    }

    if (line) {
      target.append(document.createTextNode(line))
    }
  })
}

function mentionChipElement(workerId: string, name: string): HTMLElement {
  const chip = document.createElement('span')

  chip.contentEditable = 'false'
  chip.dataset.refText = channelMentionToken(workerId)
  chip.dataset.workerId = workerId
  chip.className =
    'mx-0.5 inline-flex max-w-56 items-center rounded-md border border-[#7FA3F5]/28 bg-[#4F7BE8]/16 px-1.5 py-0.5 align-middle font-mono text-[0.78em] font-semibold leading-none text-[#AFC4FA]'
  chip.textContent = `@${name}`

  return chip
}

function renderMentionComposerContents(
  editor: HTMLElement,
  text: string,
  membersById: ReadonlyMap<string, MemberCandidate>
) {
  const fragment = document.createDocumentFragment()

  for (const part of channelMessageParts(text)) {
    if (part.kind === 'text') {
      appendText(fragment, part.text)
    } else {
      const member = membersById.get(part.workerId)
      fragment.append(mentionChipElement(part.workerId, member?.name || part.workerId))
    }
  }

  editor.replaceChildren(fragment)
}

function insertMentionAtTrigger(
  editor: HTMLDivElement,
  trigger: MentionAutocompleteState,
  candidate: MemberCandidate,
  membersById: ReadonlyMap<string, MemberCandidate>
): boolean {
  const selection = window.getSelection()
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null
  const node = range?.startContainer
  const offset = range?.startOffset ?? 0
  const chip = mentionChipElement(candidate.workerId, candidate.name)
  const space = document.createTextNode(' ')

  if (!selection || !range || node?.nodeType !== Node.TEXT_NODE || offset < trigger.tokenLength) {
    const current = composerPlainText(editor)
    const prefix = current.slice(0, Math.max(0, current.length - trigger.tokenLength))

    renderMentionComposerContents(editor, prefix, membersById)
    editor.append(chip, space)
    placeCaretEnd(editor)

    return true
  }

  const replaceRange = document.createRange()
  replaceRange.setStart(node, offset - trigger.tokenLength)
  replaceRange.setEnd(node, offset)
  replaceRange.deleteContents()
  replaceRange.insertNode(space)
  replaceRange.insertNode(chip)

  const caret = document.createRange()
  caret.setStart(space, 1)
  caret.collapse(true)
  selection.removeAllRanges()
  selection.addRange(caret)

  return true
}

export function ChannelComposer({ channel, memberProfiles, onSend, sending }: ChannelComposerProps) {
  const { t } = useI18n()
  const c = t.channels
  const editorRef = useRef<HTMLDivElement>(null)
  const composingRef = useRef(false)
  const [draft, setDraft] = useState('')
  const [mention, setMention] = useState<MentionAutocompleteState | null>(null)
  const [noMentionHint, setNoMentionHint] = useState(false)

  const candidates = useMemo<MemberCandidate[]>(
    () =>
      memberProfiles.map(profile => ({
        name: profile.display_name || profile.name,
        profile,
        workerId: profile.name
      })),
    [memberProfiles]
  )

  const membersById = useMemo(() => new Map(candidates.map(candidate => [candidate.workerId, candidate])), [candidates])

  const filtered = useMemo(
    () => filterMentionCandidates(candidates, mention?.query ?? ''),
    [candidates, mention?.query]
  )

  useEffect(() => {
    setDraft('')
    setMention(null)
    setNoMentionHint(false)
    editorRef.current?.replaceChildren()
  }, [channel.id])

  useEffect(() => {
    setMention(current =>
      current ? { ...current, activeIndex: Math.min(current.activeIndex, Math.max(0, filtered.length - 1)) } : null
    )
  }, [filtered.length])

  const syncDraft = (editor: HTMLDivElement) => {
    normalizeComposerEditorDom(editor)
    const value = composerPlainText(editor)
    setDraft(value)

    return value
  }

  const refreshMention = () => {
    const editor = editorRef.current

    if (!editor) {
      return
    }

    const found = detectTrigger(textBeforeCaret(editor) ?? composerPlainText(editor))

    setMention(current =>
      found?.kind === '@' ? nextMentionAutocompleteState(current, found.query, found.tokenLength) : null
    )
  }

  const selectMention = (candidate: MemberCandidate) => {
    const editor = editorRef.current

    if (!editor || !mention || !insertMentionAtTrigger(editor, mention, candidate, membersById)) {
      return
    }

    syncDraft(editor)
    setMention(null)
    setNoMentionHint(false)
    editor.focus()
  }

  const submit = async () => {
    const editor = editorRef.current
    const text = editor ? composerPlainText(editor).trim() : draft.trim()

    if (!text || sending) {
      return
    }

    setMention(null)
    setNoMentionHint(false)
    editor?.replaceChildren()
    setDraft('')

    const result = await onSend(text)

    setNoMentionHint(channel.kind === 'channel' && result.targets.length === 0)
    editor?.focus()
  }

  const openMentionPicker = () => {
    const editor = editorRef.current

    if (!editor || candidates.length === 0 || sending) {
      return
    }

    if (mention) {
      setMention(null)

      return
    }

    editor.focus()
    placeCaretEnd(editor)
    insertPlainTextAtCaret(editor, '@')
    syncDraft(editor)
    refreshMention()
  }

  return (
    <footer className="relative z-20 shrink-0 border-t border-[#7FA3F5]/14 bg-[#0A1228]/94 px-4 pb-4 pt-3 backdrop-blur-xl">
      <div className="relative mx-auto max-w-3xl">
        {mention && (
          <div
            aria-label={c.mentionWorker}
            className="absolute bottom-[calc(100%+0.5rem)] left-0 z-30 w-72 overflow-hidden rounded-xl border border-[#7FA3F5]/22 bg-[#101C3D]/98 p-1 shadow-[0_20px_60px_rgba(0,0,0,.5)] backdrop-blur-xl"
            role="listbox"
          >
            <div className="flex items-center justify-between px-2 py-1.5 font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.15em] text-[#7FA3F5]/60">
              <span>{c.mentionWorker}</span>
              <span className="normal-case tracking-normal text-[#F4F2EC]/28">{mention.query || '@'}</span>
            </div>
            {filtered.length === 0 ? (
              <div className="px-2 py-3 text-xs text-[#F4F2EC]/45">{c.mentionNoMatches}</div>
            ) : (
              filtered.map((candidate, index) => (
                <button
                  aria-selected={mention.activeIndex === index}
                  className={cn(
                    'flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-xs text-[#F4F2EC]/75 transition-colors',
                    mention.activeIndex === index && 'bg-[#4F7BE8]/18 text-white'
                  )}
                  key={candidate.workerId}
                  onMouseDown={event => {
                    event.preventDefault()
                    selectMention(candidate)
                  }}
                  onMouseEnter={() => setMention(current => (current ? { ...current, activeIndex: index } : current))}
                  role="option"
                  type="button"
                >
                  <WorkerAvatar className="size-7 rounded-md text-[0.5625rem]" profile={candidate.profile} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{candidate.name}</span>
                    <span className="block truncate font-mono text-[0.5625rem] text-[#7FA3F5]/52">
                      {candidate.workerId}
                    </span>
                  </span>
                </button>
              ))
            )}
          </div>
        )}

        <div className="rounded-2xl border border-[#7FA3F5]/20 bg-[#101C3D]/88 p-2 shadow-[0_16px_45px_rgba(0,0,0,.28)] transition-colors focus-within:border-[#4F7BE8]/65">
          <div
            aria-disabled={sending || undefined}
            aria-label={c.composerPlaceholder(channel.name)}
            aria-multiline="true"
            className={cn(
              'min-h-16 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-transparent px-2 py-1.5 text-sm leading-6 text-[#F4F2EC] outline-none [overflow-wrap:anywhere]',
              'empty:before:pointer-events-none empty:before:content-[attr(data-placeholder)] empty:before:text-[#F4F2EC]/28',
              sending && 'cursor-wait opacity-65'
            )}
            contentEditable={!sending}
            data-placeholder={c.composerPlaceholder(channel.name)}
            onBlur={() => window.setTimeout(() => setMention(null), 100)}
            onCompositionEnd={event => {
              composingRef.current = false
              syncDraft(event.currentTarget)
              refreshMention()
            }}
            onCompositionStart={() => {
              composingRef.current = true
            }}
            onInput={event => {
              if (composingRef.current) {
                return
              }

              syncDraft(event.currentTarget)
              setNoMentionHint(false)
              refreshMention()
            }}
            onKeyDown={event => {
              if (composingRef.current) {
                return
              }

              const result = mentionAutocompleteKey(mention, event.key, filtered.length)

              if (result.consumed) {
                event.preventDefault()

                if (result.selectIndex !== undefined) {
                  const selected = filtered[result.selectIndex]

                  if (selected) {
                    selectMention(selected)
                  }
                } else {
                  setMention(result.next)
                }

                return
              }

              if (event.key === 'Backspace' && deleteChipBeforeCaret(event.currentTarget)) {
                event.preventDefault()
                syncDraft(event.currentTarget)

                return
              }

              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void submit()
              }
            }}
            onMouseUp={refreshMention}
            onPaste={event => {
              event.preventDefault()
              insertPlainTextAtCaret(event.currentTarget, event.clipboardData.getData('text/plain'))
              syncDraft(event.currentTarget)
              refreshMention()
            }}
            ref={editorRef}
            role="textbox"
            spellCheck
            suppressContentEditableWarning
          />
          <div className="flex items-center gap-2 border-t border-[#7FA3F5]/10 px-1 pt-2">
            <Tip label={c.mentionWorker}>
              <Button
                aria-expanded={Boolean(mention)}
                aria-label={c.mentionWorker}
                className={cn(
                  'text-[#7FA3F5]/70 hover:bg-[#4F7BE8]/15 hover:text-[#7FA3F5]',
                  mention && 'bg-[#4F7BE8]/15 text-[#7FA3F5]'
                )}
                disabled={candidates.length === 0 || sending}
                onClick={openMentionPicker}
                onMouseDown={event => event.preventDefault()}
                size="icon-xs"
                variant="ghost"
              >
                <AtSign className="size-4" />
              </Button>
            </Tip>
            <span
              className={cn(
                'min-w-0 flex-1 truncate font-mono text-[0.5625rem]',
                noMentionHint ? 'text-[#F3C77A]/75' : 'text-[#F4F2EC]/32'
              )}
            >
              {sending
                ? c.sending
                : noMentionHint
                  ? c.noMentionHint
                  : channel.kind === 'dm'
                    ? c.dmComposerHint
                    : c.composerHint}
            </span>
            <Tip label={c.sendMessage}>
              <span>
                <Button
                  aria-label={c.sendMessage}
                  className="bg-[#4F7BE8] text-white shadow-[0_8px_22px_rgba(79,123,232,.24)] hover:bg-[#628CF0]"
                  disabled={!draft.trim() || sending}
                  onClick={() => void submit()}
                  size="icon-sm"
                >
                  <Send className="size-4" />
                </Button>
              </span>
            </Tip>
          </div>
        </div>
      </div>
    </footer>
  )
}
