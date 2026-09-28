/**
 * HomeSigninNotice — the note that a home in the cloud waits for one click to
 * sign in to Kiro (`home_signin`, RFC §6.8 rule 5). Pins that it is a system
 * notice the transcript draws through `SystemNoticeRow`, that the copy is keyed
 * on `meta.opened` (never the row's English content), and that it offers no
 * action: the sign-in lives on the home card.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { mergeRenderers, resolveRenderer } from '../app-sdk/messageRenderers'
import { isSystemNoticeKind } from '../lib/systemNotice'
import { SystemNoticeRow, isSystemNoticeRow } from '../pages/chat/CompactionCard'
import { homeSigninOf } from '../pages/chat/HomeSigninNotice'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import type { ChatMessage } from '../types'

function row(meta: Record<string, unknown>): ChatMessage {
  // The content is the gateway's English fallback; the notice must not draw it.
  return { role: 'assistant', content: 'GATEWAY FALLBACK TEXT', ts: '2026-09-28T10:00:00Z', meta } as ChatMessage
}

const OPENED = row({ kind: 'home_signin', opened: true, card: 'sc-0123456789abcdef' })
const ON_CARD = row({ kind: 'home_signin', opened: false, card: 'sc-0123456789abcdef' })

describe('HomeSigninNotice', () => {
  it('is a system notice, drawn by the system-notice renderer, never as a reply', () => {
    expect(isSystemNoticeKind('home_signin')).toBe(true)
    expect(isSystemNoticeRow(OPENED)).toBe(true)
    const registry = mergeRenderers(createTranscriptRenderers({ slot: 's1' }))
    expect(resolveRenderer(OPENED, registry)?.id).toBe('system_notice')
    expect(homeSigninOf({ ...OPENED, role: 'user' })).toBeNull()
    expect(homeSigninOf(row({ kind: 'main_chat' }))).toBeNull()
  })

  it('says the page opened in this browser when the gateway opened it', () => {
    render(<SystemNoticeRow message={OPENED} />)
    const notice = screen.getByTestId('home-signin-notice')
    expect(notice).toHaveAttribute('data-opened', 'true')
    expect(notice).toHaveTextContent(
      'Your home is waiting for one click to sign in to Kiro. The sign-in page opened in your browser.',
    )
    expect(screen.queryByText('GATEWAY FALLBACK TEXT')).toBeNull()
    expect(screen.getByTestId('notice-card')).toHaveAttribute('data-emphasis', 'true')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('points at the card when the page was not opened here', () => {
    render(<SystemNoticeRow message={ON_CARD} />)
    expect(screen.getByTestId('home-signin-notice')).toHaveTextContent(
      'Your home is waiting for one click to sign in to Kiro. Open the sign-in link on its card.',
    )
  })

  it('reads a row without `opened` as not opened', () => {
    render(<SystemNoticeRow message={row({ kind: 'home_signin' })} />)
    expect(screen.getByTestId('home-signin-notice')).toHaveAttribute('data-opened', 'false')
  })
})
