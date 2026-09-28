/**
 * The main chat's note that a chat it handed work to has finished (RFC one-chat
 * first run §6.9, MC.9): a gateway-authored `handoff_done` row that
 * `dashboard/handoff_notice.py` posts in the main chat. `meta.slot` names the
 * chat that finished and `meta.title` its name, which the gateway flattens and
 * bounds; the notice runs no model turn.
 *
 * `meta.outcome` says how that chat's turn ended: `done` (it replied) or `error`
 * (it ended on an error with no reply); a row without one is `done`.
 *
 * The copy is keyed on `meta.kind` and `meta.outcome`, never on the row's English
 * content, which is the fallback for readers without the catalog. Each action is
 * drawn only where the surface wires it:
 *
 * - Open "<title>" switches to that chat (the completion cards' session hand-off).
 * - Ask for the result, for `done` only, sends `What did "<title>" find?` in this
 *   chat AS the user, through the composer's own send: an ordinary message, so
 *   the turn it starts is a person's (it may raise a card) and the agent reads
 *   the chat then. After an error there is no result to ask for; Open shows what
 *   happened.
 */
import { useState } from 'react'
import { CircleCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Btn } from '../../components/ui'
import type { ChatMessage } from '../../types'
import NoticeCard from './NoticeCard'

/** The gateway's bound on a quoted title (`handoff_notice.TITLE_MAX_CHARS`),
 *  re-applied because a transcript on disk can carry anything. */
const TITLE_MAX_CHARS = 60

export interface HandoffDone {
  slot: string
  title: string
  outcome: 'done' | 'error'
}

/** The finished chat a `handoff_done` row names, or null for any other row. */
export function handoffDoneOf(m: Pick<ChatMessage, 'role' | 'kind' | 'meta'>): HandoffDone | null {
  if (m.role !== 'assistant') return null
  const meta = (m.meta ?? {}) as { kind?: unknown; slot?: unknown; title?: unknown; outcome?: unknown }
  if ((m.kind ?? meta.kind) !== 'handoff_done') return null
  const slot = typeof meta.slot === 'string' ? meta.slot : ''
  if (!slot) return null
  const raw = typeof meta.title === 'string' ? meta.title.replace(/\s+/g, ' ').trim() : ''
  const title = Array.from(raw || slot).slice(0, TITLE_MAX_CHARS).join('')
  return { slot, title, outcome: meta.outcome === 'error' ? 'error' : 'done' }
}

export function isHandoffDoneRow(m: Pick<ChatMessage, 'role' | 'kind' | 'meta'>): boolean {
  return handoffDoneOf(m) !== null
}

export default function HandoffDoneNotice({ message, onOpen, onAsk }: {
  message: ChatMessage
  /** Switch to the chat that finished. Omitted: no Open button. */
  onOpen?: (slot: string) => void
  /** Send `text` in this chat as the user. Omitted: no Ask button. */
  onAsk?: (text: string) => void
}) {
  const { t } = useTranslation()
  const [asked, setAsked] = useState(false)
  const done = handoffDoneOf(message)
  if (!done) return null
  const failed = done.outcome === 'error'
  const ask = failed ? undefined : onAsk
  const actions = (onOpen || ask) && (
    <>
      {onOpen && (
        <Btn type="button" onClick={() => onOpen(done.slot)} className="min-h-9" data-testid="handoff-done-open">
          {t('pages.chat.handoffDoneNotice.open', { title: done.title })}
        </Btn>
      )}
      {ask && (
        <Btn
          type="button"
          onClick={() => {
            setAsked(true)
            ask(t('pages.chat.handoffDoneNotice.ask_message', { title: done.title }))
          }}
          disabled={asked}
          className="min-h-9 border-transparent text-muted hover:text-text"
          data-testid="handoff-done-ask"
        >
          {t('pages.chat.handoffDoneNotice.ask')}
        </Btn>
      )}
    </>
  )
  return (
    <div className="w-full min-w-0" data-testid="handoff-done-notice" data-slot={done.slot} data-outcome={done.outcome}>
      <NoticeCard
        content={
          failed
            ? t('pages.chat.handoffDoneNotice.failed', { title: done.title })
            : t('pages.chat.handoffDoneNotice.finished', { title: done.title })
        }
        tone={failed ? 'warn' : undefined}
        icon={failed ? undefined : CircleCheck}
        actions={actions || undefined}
      />
    </div>
  )
}
