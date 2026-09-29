import SetupCard, { type SetupCardPlacement } from './SetupCard'
import { useSetupCardRowHighlighted } from './setupCardTray'

/**
 * A setup card's own transcript row. The wrapper carries the card id so the
 * tray's bar can find the row, and the highlight its "find it in the chat"
 * asks for (the same `animate-msg-highlight` a jumped-to message gets). The
 * highlight is an overlay, not the wrapper's own shadow: the card paints its
 * background over an inset shadow on its parent, which hid all but a sliver.
 */
export default function SetupCardRow({ cardId, placement }: { cardId: string; placement: SetupCardPlacement }) {
  const highlighted = useSetupCardRowHighlighted(cardId)
  return (
    <div data-setup-card-row={cardId} className="relative w-full max-w-2xl min-w-0">
      <SetupCard cardId={cardId} placement={placement} />
      {highlighted && (
        <span
          aria-hidden="true"
          data-testid="setup-card-row-highlight"
          className="pointer-events-none absolute inset-0 rounded-lg animate-msg-highlight"
        />
      )}
    </div>
  )
}
