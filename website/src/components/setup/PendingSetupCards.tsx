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
 * The tray must never crowd out what the agent is saying. It is capped at a
 * third of its chat pane (scrolling inside), and it folds to a one-line bar —
 * "<title> · needs you · Show" — whenever the transcript has something newer
 * than the card (`setupCardAtTail`) or the user scrolls up to read. The bar is
 * the same box, so the fold animates rather than swapping one thing for
 * another; the cards stay mounted (inert) inside it, so a half-filled card
 * keeps its input. When the card is the newest thing, the tray shows it in full.
 *
 * It never folds out from under someone using it. Once the user touches a card
 * (a pointer press, a key, focus inside it), newer content no longer folds the
 * tray until that card is decided, they hide it, or they scroll up themselves.
 * That is keyed to the card, not to where focus happens to be: focus moves for
 * reasons that are not the user leaving (a label's mousedown blurs the button
 * before it, and a decided card's button unmounts without a blur).
 *
 * Same placement as PendingQuestionCard: mounted above the composer by the
 * single-chat view and by every grid pane, for that surface's own slot.
 *
 * The list is `['setup-cards', slot]`; the owner-only `setup_card_update`
 * WebSocket frame keeps it current (useWebSocket upserts into it), and a
 * reconnect re-reads it. Loading it seeds each card's own cache entry so the
 * tray's cards render without a second round trip.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useTranslation } from 'react-i18next'
import { CircleDot, ShieldCheck } from 'lucide-react'
import type React from 'react'

import { api } from '../../api/client'
import {
  isTerminalSetupStatus,
  setupCardQueryKey,
  setupCardsQueryKey,
  type SetupCard as SetupCardData,
} from '../../api/setupCards'
import type { ChatMessage } from '../../types'
import { Btn } from '../ui'
import SetupCard from './SetupCard'
import { cardTitle } from './setupCardRegistry'
import { setupCardAtTail } from './setupCardTray'

/** Finger travel (px) before a touch drag on the transcript counts as a scroll. */
const TOUCH_SLOP_PX = 8

type ScrollIntent = 'up' | 'down'

function keyIntent(e: KeyboardEvent): ScrollIntent | null {
  const t = e.target as HTMLElement | null
  // An arrow key in a field moves its caret; it does not scroll the transcript.
  const editable = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
  if (e.key === 'PageUp' || e.key === 'Home' || (e.key === 'ArrowUp' && !editable)) return 'up'
  if (e.key === 'PageDown' || e.key === 'End' || (e.key === 'ArrowDown' && !editable)) return 'down'
  return null
}

export default function PendingSetupCards({
  slotKey,
  className = '',
  style,
  messages,
  atBottom = true,
  scrollerRef,
  onLocate,
}: {
  /** The surface's own slot (the active slot, or a grid pane's). */
  slotKey: string | null | undefined
  className?: string
  style?: React.CSSProperties
  /** The transcript the tray sits under. Without it the tray never folds. */
  messages?: readonly ChatMessage[]
  /** Whether that transcript is scrolled to its newest row. */
  atBottom?: boolean
  /** Its scroller: the tray reads the user's scroll GESTURES there, never the
   *  geometry, since folding the tray itself moves the bottom edge. */
  scrollerRef?: React.RefObject<HTMLElement | null>
  /** Scroll the transcript to a card's own row. Without it the bar's title is text. */
  onLocate?: (cardId: string, behavior: ScrollBehavior) => void
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const reduceMotion = useReducedMotion()
  const slot = slotKey || ''
  const cardsId = useId()
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
  const hasLive = !!slot && live.length > 0

  // ── When to fold ──────────────────────────────────────────────────────────
  // `undefined` when the host passed no transcript: the tray then stays open.
  const tail = useMemo(() => (messages ? setupCardAtTail(messages) : undefined), [messages])
  const cardIsNewest = tail === undefined || live.some(c => c.id === tail)
  // The user scrolled up to read. Set by an upward gesture on the transcript;
  // cleared by scrolling back down to the bottom, by a new row arriving while
  // they are there, or by Show.
  const [readingBack, setReadingBack] = useState(false)
  // An explicit Show / Hide. Holds until the transcript's tail changes (the
  // next card, or the first thing written after one) or the user scrolls up.
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  // The card the user is working in; holds only while that card is live.
  const [engagedId, setEngagedId] = useState<string | null>(null)
  const engaged = !!engagedId && live.some(c => c.id === engagedId)
  const lastIntentRef = useRef<ScrollIntent | null>(null)
  const atBottomRef = useRef(atBottom)
  atBottomRef.current = atBottom

  useEffect(() => {
    setReadingBack(false)
    setUserOpen(null)
    setEngagedId(null)
    lastIntentRef.current = null
  }, [slot])

  const prevTailRef = useRef(tail)
  useEffect(() => {
    if (prevTailRef.current === tail) return
    prevTailRef.current = tail
    setUserOpen(null)
  }, [tail])

  useEffect(() => {
    if (atBottom && lastIntentRef.current === 'down') setReadingBack(false)
  }, [atBottom])

  const rowCount = messages?.length ?? 0
  useEffect(() => {
    if (atBottomRef.current) setReadingBack(false)
  }, [rowCount])

  useEffect(() => {
    const el = scrollerRef?.current
    if (!el || !hasLive) return
    const intent = (dir: ScrollIntent) => {
      lastIntentRef.current = dir
      if (dir === 'up') {
        setReadingBack(true)
        setUserOpen(null)
        setEngagedId(null)
      } else if (atBottomRef.current) {
        setReadingBack(false)
      }
    }
    const onWheel = (e: WheelEvent) => { if (e.deltaY) intent(e.deltaY < 0 ? 'up' : 'down') }
    const onKey = (e: KeyboardEvent) => { const dir = keyIntent(e); if (dir) intent(dir) }
    let touchY: number | null = null
    const onTouchStart = (e: TouchEvent) => { touchY = e.touches[0]?.clientY ?? null }
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY
      if (touchY === null || y === undefined || Math.abs(y - touchY) < TOUCH_SLOP_PX) return
      // A finger moving DOWN drags the content down: the reader goes back up.
      intent(y > touchY ? 'up' : 'down')
      touchY = y
    }
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('keydown', onKey)
    el.addEventListener('touchstart', onTouchStart, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: true })
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('keydown', onKey)
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
    }
  }, [scrollerRef, hasLive])

  const expanded = engaged || (userOpen ?? (cardIsNewest && !readingBack))
  const showBar = !expanded || userOpen === true

  const toggle = useCallback(() => {
    setReadingBack(false)
    if (expanded) setEngagedId(null)
    setUserOpen(!expanded)
  }, [expanded])

  // ── Height cap: a third of the chat pane ──────────────────────────────────
  // Measured from the host's `data-setup-tray-pane` element; the `33dvh` class
  // is the fallback where there is none to measure.
  const [cap, setCap] = useState<number | null>(null)
  const observerRef = useRef<ResizeObserver | null>(null)
  const sectionRef = useCallback((node: HTMLElement | null) => {
    observerRef.current?.disconnect()
    observerRef.current = null
    const pane = node?.closest<HTMLElement>('[data-setup-tray-pane]')
    if (!pane) { setCap(null); return }
    const measure = () => { const h = pane.clientHeight; setCap(h > 0 ? Math.floor(h / 3) : null) }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(pane)
    observerRef.current = observer
  }, [])
  useEffect(() => () => observerRef.current?.disconnect(), [])

  if (!hasLive) return null

  // The newest card names the bar: it sits last, nearest the composer.
  const newest = qc.getQueryData<SetupCardData>(setupCardQueryKey(live[live.length - 1].id)) ?? live[live.length - 1]
  const title = cardTitle(newest)
  const fold = reduceMotion ? { duration: 0 } : { duration: 0.2, ease: [0.2, 0.8, 0.2, 1] as const }

  return (
    <section
      ref={sectionRef}
      className={`flex flex-col min-w-0 max-h-[33dvh] overflow-y-auto overscroll-contain ${className}`}
      style={cap ? { ...style, maxHeight: cap } : style}
      aria-label={t('components.setupCard.tray_label')}
      data-testid="setup-card-tray"
      data-expanded={String(expanded)}
    >
      <AnimatePresence initial={false}>
        {showBar && (
          <motion.div
            key="bar"
            // shrink-0 on both children: the section is a height-capped flex
            // column, and an overflow-hidden item would otherwise give up its
            // height first, folding the bar away under the cards.
            className="sticky top-0 z-10 shrink-0 min-w-0 overflow-hidden bg-bg"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={fold}
          >
            <div
              className={`flex items-center gap-2 min-w-0 rounded-lg border bg-card px-3 py-1 text-[13px] ${
                newest.stakes === 'high' ? 'border-accent' : 'border-border'
              } ${expanded ? 'mb-2' : ''}`}
              data-testid="setup-card-tray-bar"
            >
              {newest.stakes === 'high'
                ? <ShieldCheck className="lucide-inline shrink-0 text-accent" aria-hidden="true" />
                : <CircleDot className="lucide-inline shrink-0 text-accent" aria-hidden="true" />}
              {onLocate ? (
                <Btn
                  className="min-w-0 min-h-11 md:min-h-0 border-none bg-transparent px-0 py-0 font-medium hover:bg-transparent hover:underline"
                  onClick={() => onLocate(newest.id, reduceMotion ? 'auto' : 'smooth')}
                  title={t('components.setupCardTray.locate', { title })}
                  aria-label={t('components.setupCardTray.locate', { title })}
                  data-testid="setup-card-tray-locate"
                >
                  <span className="truncate">{title}</span>
                </Btn>
              ) : (
                <span className="min-w-0 truncate font-medium">{title}</span>
              )}
              <span className="shrink-0 text-muted" aria-hidden="true">·</span>
              <span className="shrink-0 text-accent">
                {newest.status === 'pending' ? t('components.setupCardTray.needs_you') : t('components.setupCardTray.in_progress')}
              </span>
              {live.length > 1 && (
                <>
                  <span className="shrink-0 text-muted" aria-hidden="true">·</span>
                  <span className="shrink-0 text-muted">{t('components.setupCardTray.cards', { count: live.length })}</span>
                </>
              )}
              <Btn
                className="ml-auto shrink-0 min-h-11 md:min-h-0 py-0.5"
                onClick={toggle}
                aria-expanded={expanded}
                aria-controls={cardsId}
                data-testid="setup-card-tray-toggle"
              >
                {expanded ? t('components.setupCardTray.hide') : t('components.setupCardTray.show')}
              </Btn>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <motion.div
        id={cardsId}
        className="flex flex-col gap-2 shrink-0 min-w-0"
        initial={false}
        animate={expanded
          ? { height: 'auto', opacity: 1, transitionEnd: { overflow: 'visible' } }
          : { height: 0, opacity: 0, overflow: 'hidden' }}
        transition={fold}
        // Folded cards stay mounted, so a half-filled one keeps its input, but
        // out of reach: no focus, no reading.
        {...(expanded ? {} : { inert: '', 'aria-hidden': true })}
        data-testid="setup-card-tray-cards"
      >
        {/* Creation order, so the newest card sits last, nearest the composer. */}
        <AnimatePresence initial={false}>
          {live.map(card => (
            <motion.div
              key={card.id}
              className="min-w-0"
              // Capture phase, so the card's own handlers cannot hide the touch.
              onPointerDownCapture={() => setEngagedId(card.id)}
              onKeyDownCapture={() => setEngagedId(card.id)}
              onFocusCapture={() => setEngagedId(card.id)}
              initial={reduceMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0 }}
              transition={{ duration: 0.18 }}
            >
              <SetupCard cardId={card.id} placement="tray" />
            </motion.div>
          ))}
        </AnimatePresence>
      </motion.div>
    </section>
  )
}
