/**
 * The note that a home in the cloud waits for one click to sign in to Kiro (RFC
 * one-chat first run §6.8 rule 5): a gateway-authored `home_signin` row that
 * `dashboard/home_signin.py` posts once in the chat that owns the home card. The
 * home keeps its own Kiro sign-in; `meta.opened` says whether the gateway opened
 * its sign-in page in this computer's browser. When it did not, the link and the
 * code are on the home card.
 *
 * The copy is keyed on `meta.kind` and `meta.opened`, never on the row's English
 * content, which is the fallback for readers without the catalog. It runs no
 * model turn and carries no action: the card is where the sign-in is.
 */
import { LogIn } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { ChatMessage } from '../../types'
import NoticeCard from './NoticeCard'

/** Whether a `home_signin` row's page opened here, or null for any other row. */
export function homeSigninOf(m: Pick<ChatMessage, 'role' | 'kind' | 'meta'>): { opened: boolean; resolved: boolean } | null {
  if (m.role !== 'assistant') return null
  const meta = (m.meta ?? {}) as { kind?: unknown; opened?: unknown; resolved?: unknown }
  if ((m.kind ?? meta.kind) !== 'home_signin') return null
  return { opened: meta.opened === true, resolved: meta.resolved === true }
}

export default function HomeSigninNotice({ message }: { message: ChatMessage }) {
  const { t } = useTranslation()
  const signin = homeSigninOf(message)
  if (!signin) return null
  return (
    <div className="w-full min-w-0" data-testid="home-signin-notice" data-opened={signin.opened}>
      <NoticeCard
        content={signin.resolved ? t('pages.chat.homeSigninNotice.resolved') : signin.opened ? t('pages.chat.homeSigninNotice.opened') : t('pages.chat.homeSigninNotice.on_card')}
        icon={LogIn}
        emphasis
      />
    </div>
  )
}
