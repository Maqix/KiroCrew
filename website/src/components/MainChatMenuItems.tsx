/**
 * The session menu's two main-chat entries (RFC §6.9), split out of
 * SessionActionsMenu so that file keeps to its canonical list:
 *
 * - `MakeMainChatItem` — "Make this my main chat", for an eligible dashboard
 *   chat that is not already the main chat. Selecting it keeps the menu open so
 *   the outcome is said where the click happened: a confirmation line on
 *   success (the sidebar's Main chip moves at the same time), or the gateway's
 *   refusal as a passive notice with the agent hand-off as its own menu item
 *   (the in-menu form `errors-use-error-notice` prescribes).
 * - `AskInMainChatItem` — "Ask about this chat in main chat": opens the main
 *   chat with `About the chat "<title>": ` pre-filled (not sent).
 */
import * as React from 'react'
import { Check, House, Loader2, MessageSquareShare } from 'lucide-react'

import { ApiError } from '../api/apiError'
import { parseErrorCode } from '../utils/errorReport'
import { i18nT } from '../i18n/t'
import { useDraftInMainChat, useMakeMainChat } from '../hooks/useMainChat'
import ErrorNotice, { ErrorNoticeMenuItem, type ErrorNoticeMenuItemComponent } from './ErrorNotice'

type MenuItem = ErrorNoticeMenuItemComponent

const MAKE_MAIN_ERROR_KEY = {
  slot_not_eligible: 'components.mainChatMenu.make_main_not_eligible',
  slot_not_found: 'components.mainChatMenu.make_main_not_found',
} as const

function makeMainErrorText(err: unknown): string {
  const code = err instanceof ApiError ? parseErrorCode(err.body) : undefined
  const key = code ? MAKE_MAIN_ERROR_KEY[code as keyof typeof MAKE_MAIN_ERROR_KEY] : undefined
  if (key) return i18nT(key)
  return (err instanceof Error && err.message) || i18nT('components.mainChatMenu.make_main_failed')
}

export function MakeMainChatItem({
  Item, slotKey, isMainChat,
}: { Item: MenuItem; slotKey: string; isMainChat: boolean }) {
  const make = useMakeMainChat()
  const errorId = React.useId()
  if (make.isSuccess) {
    return (
      <div className="flex items-center gap-2 px-2 py-1.5 text-[13px] text-text" role="status" data-testid="session-menu-main-chat-done">
        <Check size={13} className="shrink-0 text-ok" aria-hidden="true" />
        {i18nT('components.mainChatMenu.make_main_done')}
      </div>
    )
  }
  // Already the main chat (and not made so from this menu): nothing to offer.
  if (isMainChat) return null
  return (
    <>
      <Item
        disabled={make.isPending}
        onSelect={(event: Event) => {
          // Keep the menu open: it is where the outcome is reported.
          event.preventDefault()
          if (!make.isPending) make.mutate(slotKey)
        }}
      >
        {make.isPending
          ? <Loader2 size={13} className="shrink-0 text-muted animate-spin" aria-hidden="true" />
          : <House size={13} className="shrink-0 text-muted" aria-hidden="true" />}
        {i18nT('components.mainChatMenu.make_main')}
      </Item>
      {make.isError && (
        <>
          <div className="max-w-[300px] px-2 py-1.5">
            <ErrorNotice
              id={errorId}
              variant="inline"
              className="flex-wrap"
              message={makeMainErrorText(make.error)}
              testId="session-menu-main-chat-error"
            />
          </div>
          <ErrorNoticeMenuItem Item={Item} message={makeMainErrorText(make.error)} describedBy={errorId} />
        </>
      )}
    </>
  )
}

export function AskInMainChatItem({
  Item, mainSlot, title,
}: { Item: MenuItem; mainSlot: string; title: string }) {
  const draftInMainChat = useDraftInMainChat()
  return (
    <Item
      onSelect={() => {
        // `{{request}}` is where the user's own words go: empty here, so the
        // composer holds the lead-in and the cursor sits after it.
        draftInMainChat(mainSlot, i18nT('components.mainChatMenu.ask_about_chat_draft', { title, request: '' }))
      }}
    >
      <MessageSquareShare size={13} className="shrink-0 text-muted" aria-hidden="true" />
      {i18nT('components.mainChatMenu.ask_about_chat')}
    </Item>
  )
}
