/**
 * PendingSetupCards — the "needs your decision" tray above the composer.
 *
 * A setup card is proposed mid-turn, and the agent usually keeps writing after
 * it, so the card's own transcript row ends up far above the newest message —
 * where stick-to-bottom scrolling fights anyone reaching for it. The tray keeps
 * every LIVE card (`pending`, `working`, `waiting`) of the slot pinned where the
 * owner is already looking, drawn by the same SetupCard (same buttons, same
 * test ids), while each card's transcript row folds to a one-line pointer
 * (SetupCard `placement="transcript"`). A decided card leaves the tray and its
 * row becomes the result line, so the full card is on screen exactly once.
 *
 * Same placement as PendingQuestionCard: mounted above the composer by the
 * single-chat view and by every grid pane, for that surface's own slot.
 *
 * The list is `['setup-cards', slot]`; the owner-only `setup_card_update`
 * WebSocket frame keeps it current (useWebSocket upserts into it), and a
 * reconnect re-reads it. Loading it seeds each card's own cache entry so the
 * tray's cards render without a second round trip.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useTranslation } from 'react-i18next'
import type React from 'react'

import { api } from '../../api/client'
import {
  isTerminalSetupStatus,
  setupCardQueryKey,
  setupCardsQueryKey,
  type SetupCard as SetupCardData,
} from '../../api/setupCards'
import SetupCard from './SetupCard'

export default function PendingSetupCards({
  slotKey,
  className = '',
  style,
}: {
  /** The surface's own slot (the active slot, or a grid pane's). */
  slotKey: string | null | undefined
  className?: string
  style?: React.CSSProperties
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const reduceMotion = useReducedMotion()
  const slot = slotKey || ''
  const list = useQuery({
    queryKey: setupCardsQueryKey(slot),
    queryFn: async () => {
      const res = await api.setupCards(slot)
      // Seed each card's own entry, never overwriting a fresher decided copy
      // with a live one (the same guard the WebSocket fold applies).
      for (const card of res.cards ?? []) {
        qc.setQueryData<SetupCardData>(setupCardQueryKey(card.id), prev =>
          prev && isTerminalSetupStatus(prev.status) && !isTerminalSetupStatus(card.status) ? prev : card)
      }
      return res
    },
    enabled: !!slot,
    // A refusal here (a non-owner viewer, a gateway without setup cards) is not
    // the owner's to fix: the tray stays empty and each transcript row, seeing
    // no tray copy, draws its full card instead.
    retry: false,
  })

  const live = (list.data?.cards ?? []).filter(c => !isTerminalSetupStatus(c.status))
  if (!slot || live.length === 0) return null

  return (
    <section
      className={`flex flex-col gap-2 min-w-0 max-h-[50vh] overflow-y-auto overscroll-contain ${className}`}
      style={style}
      aria-label={t('components.setupCard.tray_label')}
      data-testid="setup-card-tray"
    >
      {/* Creation order, so the newest card sits last, nearest the composer. */}
      <AnimatePresence initial={false}>
        {live.map(card => (
          <motion.div
            key={card.id}
            className="min-w-0"
            initial={reduceMotion ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduceMotion ? undefined : { opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            <SetupCard cardId={card.id} placement="tray" />
          </motion.div>
        ))}
      </AnimatePresence>
    </section>
  )
}
