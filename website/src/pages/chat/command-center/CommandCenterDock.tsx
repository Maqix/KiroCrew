import { memo, useId, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { EyeOff, PanelRightOpen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Btn } from '../../../components/ui'
import ErrorNotice from '../../../components/ErrorNotice'
import { usePersistedBool } from '../../../hooks/usePersistedBool'
import { fmtNumber } from '../../../i18n/format'
import { useLanguageGeneration } from '../../../i18n/useLanguageGeneration'
import { useCommandCenter } from './useCommandCenter'
import StatusTiles, { type Tile } from './StatusTiles'
import TileList, { TILE_LABEL_KEYS } from './TileList'
import { PANEL_HEADING_ATTR } from './panelHeading'

/** Neither caller's `onOpen` moves focus; the panel is lazy, so its first open
 * waits on a chunk, and later opens mount (or merely show) it in the commit the
 * caller's updates schedule. Look for its shown heading for up to a second and
 * land there. `from` is the element focused when the open was asked for: once
 * focus has left it (the user clicked the composer while the chunk loaded), the
 * search stops rather than yanking focus back. Body or nothing focused counts as
 * still waiting, since that is where focus rests after a click on a button. */
const FOCUS_BUDGET_MS = 1000
function focusOpenedPanel(from: Element | null, deadline = performance.now() + FOCUS_BUDGET_MS) {
  const active = document.activeElement
  if (active && active !== document.body && active !== from) return
  const heading = Array.from(document.querySelectorAll<HTMLElement>(`[${PANEL_HEADING_ATTR}]`)).find(el => !el.closest('[hidden]') && el.getClientRects().length > 0)
  if (heading) { heading.focus({ preventScroll: true }); return }
  if (performance.now() < deadline) requestAnimationFrame(() => focusOpenedPanel(from, deadline))
}


/** Three numbers above the composer, in the composer's own column: progress,
 * what is blocked, what waits on the user. A tile opens its own short list; the
 * side panel holds the full page and every answer/approval control, so this
 * surface never mounts a second copy of a draft. Each tile is a disclosure
 * button for its list (clicking the open one closes it), so the row carries two
 * actions (open the panel, hide). Hidden, it shrinks to one dot that lights only
 * when something needs the user. Gone once the task settles. */
function CommandCenterDock({ slot, onOpen }: { slot: string | null; onOpen: () => void }) {
  const { t } = useTranslation()
  // memo() bails out of the provider-level repaint; fmtNumber reads the language at call time.
  useLanguageGeneration()
  const data = useCommandCenter(slot)
  const [hidden, setHidden] = usePersistedBool('mc-task-dashboard-hidden', false)
  const [selected, setSelected] = useState<Tile | null>(null)
  const reducedMotion = useReducedMotion()
  const idBase = useId()
  const open = () => { const from = document.activeElement; onOpen(); focusOpenedPanel(from) }
  const attention = data.attention.length
  const transition = { duration: reducedMotion ? 0 : 0.24, ease: [0.34, 1.2, 0.64, 1] as const }
  const shown = data.relevant && !data.finished
  return (
    // `relative z-[2]` clears the transcript's bottom mask, as the sibling bars
    // do. The INPUT column, not the message column: the dock is the composer's
    // own status line and must share its edges exactly. The column stays
    // mounted so the settled dock can collapse out instead of cutting.
    <div className="px-4 mx-auto w-full relative z-[2]" style={{ maxWidth: 'var(--mc-input-width, 900px)' }}>
    <AnimatePresence initial={false}>
      {shown && <motion.div key="dock" exit={{ opacity: 0, height: 0 }} transition={transition} className={`mb-1 flex overflow-hidden ${hidden ? 'justify-end' : ''}`} data-testid="command-center-dock">
        {/* One element in both forms: the box morphs into the dot and back. */}
        <motion.div layout transition={transition} className={hidden ? 'inline-flex' : 'w-full min-w-0'}>
          {hidden
            ? <Btn aria-label={attention > 0 ? t('commandCenter.input_count', { countText: fmtNumber(attention) }) : t('commandCenter.show')} title={t('commandCenter.show')}
              onClick={() => setHidden(false)} className="rounded-full bg-card px-2 py-1 gap-1.5 min-h-7">
              <motion.span aria-hidden="true" className={`inline-block w-2 h-2 rounded-full ${attention > 0 ? 'bg-danger' : 'bg-muted'}`}
                animate={attention > 0 && !reducedMotion ? { opacity: [1, 0.35, 1] } : undefined} transition={{ duration: 1.4, repeat: 3 }} />
              {attention > 0 && <span className="font-mono tabular-nums text-[12px] text-danger">{fmtNumber(attention)}</span>}
            </Btn>
            : <div className="w-full min-w-0 space-y-1.5">
              <StatusTiles data={data} selected={selected} idBase={idBase} onSelect={tile => setSelected(current => current === tile ? null : tile)}
                controls={<div className="flex items-center gap-1">
                  {/* Labelled in the cell: an icon alone does not say where the full page is. */}
                  <Btn aria-label={t('commandCenter.open_panel')} onClick={open} className="px-2 min-h-7 text-[12px] whitespace-nowrap"><PanelRightOpen size={14} />{t('commandCenter.open_panel')}</Btn>
                  <Btn aria-label={t('commandCenter.hide')} title={t('commandCenter.hide')} onClick={() => setHidden(true)} className="px-1.5 min-h-7"><EyeOff size={14} /></Btn>
                </div>} />
              {/* No hand-off: the adjacent chat composer and panel can hold unsent answer drafts. */}
              {data.stale && <ErrorNotice message={t('commandCenter.stale')} />}
              <motion.div initial={false} animate={{ height: selected ? 'auto' : 0, opacity: selected ? 1 : 0 }} transition={transition} aria-hidden={!selected} className="overflow-hidden">
                {selected && <div id={`${idBase}-${selected}`} role="region" aria-label={t(TILE_LABEL_KEYS[selected])} className="rounded-lg border border-border bg-card px-2.5 py-1.5">
                  <TileList tile={selected} data={data} onOpen={open} />
                </div>}
              </motion.div>
            </div>}
        </motion.div>
      </motion.div>}
    </AnimatePresence>
    </div>
  )
}

export default memo(CommandCenterDock)
