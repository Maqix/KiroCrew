import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Btn } from '../../../components/ui'
import ErrorNotice from '../../../components/ErrorNotice'
import { fmtNumber } from '../../../i18n/format'
import { approvalTitle, runTitle, type AttentionItem, type CommandCenterModel, type RunNode } from './model'
import type { Tile } from './StatusTiles'

/** Rows one list shows before the rest is left to the side panel. */
const MAX_ROWS = 6
export const TILE_LABEL_KEYS: Record<Tile, string> = { progress: 'commandCenter.tile_progress', blocked: 'commandCenter.blocked', attention: 'commandCenter.attention_filter' }

type Data = Pick<CommandCenterModel, 'nodes' | 'workItems' | 'attention' | 'progress'>

/** The short list behind one tile: what runs, what is stuck, or what waits on
 * the user. A run's error renders through the shared error notice; a request
 * row names it and hands off to the panel (`onOpen`) rather than mounting a
 * second answer or approval control, so a draft has one home. */
export default function TileList({ tile, data, onOpen }: { tile: Tile; data: Data; onOpen?: () => void }) {
  const { t } = useTranslation()
  const openBtn = onOpen && <Btn onClick={onOpen} className="px-2 py-0.5 text-[12px] shrink-0">{t('commandCenter.open_panel')}</Btn>
  const nodeRow = (node: RunNode) => <li key={node.id} className="min-w-0 text-[12px] space-y-1">
    <div className="flex items-center gap-2 min-w-0">
      <span className="truncate">{runTitle(node)}</span>
      {node.detail && <span className="truncate text-muted min-w-0 flex-1">{node.detail}</span>}
      {node.state === 'blocked' && openBtn}
    </div>
    {/* No hand-off: the composer and panel beside this list can hold unsent answer drafts. */}
    <ErrorNotice message={node.error} />
  </li>
  let rows: ReactElement[] = []
  if (tile === 'progress') {
    // The list reads exactly the source selected for the tile's number by
    // `progress.source`; without work progress, it shows the runs still going.
    // Only running items: a blocked item is the Blocked tile's row, and a
    // waiting item is not progress until its question is answered, so no row
    // appears under two tiles and none claims motion it lacks.
    rows = data.progress?.source === 'work'
      ? data.workItems.filter(w => w.state === 'running').map(w => <li key={w.item_id} className="flex items-center gap-2 min-w-0 text-[12px]">
        <span className="truncate">{w.title}</span>{w.summary && <span className="truncate text-muted min-w-0 flex-1">{w.summary}</span>}
      </li>)
      : data.nodes.filter(n => n.state === 'running').map(nodeRow)
    if (!rows.length) rows = [<li key="empty" className="text-[12px] text-muted">{t('commandCenter.nothing_running')}</li>]
  } else if (tile === 'blocked') {
    rows = [
      ...data.nodes.filter(n => n.state === 'blocked').map(nodeRow),
      ...data.workItems.filter(w => w.state === 'blocked').map(w => <li key={w.item_id} className="flex items-center gap-2 min-w-0 text-[12px]">
        <span className="truncate">{w.title}</span>{w.summary && <span className="truncate text-muted min-w-0 flex-1">{w.summary}</span>}{openBtn}
      </li>),
    ]
    if (!rows.length) rows = [<li key="empty" className="text-[12px] text-muted">{t('commandCenter.no_blocked')}</li>]
  } else {
    rows = data.attention.map((item: AttentionItem) => {
      const node = data.nodes.find(n => n.id === `session:${item.slot}`)
      const kind = item.kind === 'approval' ? t('commandCenter.approvals') : item.kind === 'question' ? t('commandCenter.needs_input') : t('commandCenter.state_needs_input')
      const title = item.approval ? approvalTitle(item.approval) || t('commandCenter.approval_needed')
        : item.question?.questions[0]?.question || (node ? runTitle(node) : '')
      return <li key={item.id} className="flex items-center gap-2 min-w-0 text-[12px]">
        <span className="shrink-0 rounded-full bg-danger-subtle text-danger px-1.5 py-px text-[11px] font-medium">{kind}</span>
        <span className="truncate min-w-0 flex-1">{title}</span>
        {openBtn}
      </li>
    })
    if (!rows.length) rows = [<li key="empty" className="text-[12px] text-muted">{t('commandCenter.no_input')}</li>]
  }
  return <ul className="list-none m-0 p-0 space-y-1">
    {rows.slice(0, onOpen ? MAX_ROWS : rows.length)}
    {onOpen && rows.length > MAX_ROWS && <li><Btn onClick={onOpen} className="px-2 py-0.5 text-[12px]">{t('commandCenter.more_in_panel', { countText: fmtNumber(rows.length - MAX_ROWS) })}</Btn></li>}
  </ul>
}
