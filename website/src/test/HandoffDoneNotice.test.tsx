/**
 * HandoffDoneNotice — the main chat's note that a chat it handed work to has
 * finished (`handoff_done`, RFC §6.9 MC.9). Pins that the copy is keyed on the
 * row's meta (never its English content), that Open hands the finished chat's
 * key to the host and Ask sends the question as the user exactly once, that a
 * surface wiring neither gets the plain notice, and that the transcript registry
 * routes these rows here ahead of the plain system notice.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import HandoffDoneNotice, { handoffDoneOf, isHandoffDoneRow } from '../pages/chat/HandoffDoneNotice'
import { defaultMessageRenderers, mergeRenderers, resolveRenderer, type MessageRenderContext } from '../app-sdk/messageRenderers'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import { SystemNoticeRow, isSystemNoticeRow } from '../pages/chat/CompactionCard'
import type { ChatMessage } from '../types'

function row(meta: Record<string, unknown>): ChatMessage {
  // The content is the gateway's English fallback; the notice must not draw it.
  return { role: 'assistant', content: 'GATEWAY FALLBACK TEXT', ts: '2026-09-28T10:00:00Z', meta } as ChatMessage
}

const DONE = row({ kind: 'handoff_done', slot: 'chat-child-1', title: 'Flaky tests' })

const ctx = (over: Partial<MessageRenderContext> = {}): MessageRenderContext => ({
  index: 0,
  messages: [DONE],
  running: false,
  key: 'k0',
  hideCardOwnedOAuth: false,
  autoDeniedIds: new Set<string>(),
  wrapper: children => children,
  row: children => children,
  ...over,
})

describe('HandoffDoneNotice', () => {
  it('is a system notice the dashboard registry routes to its own renderer', () => {
    expect(isSystemNoticeRow(DONE)).toBe(true)
    expect(isHandoffDoneRow(DONE)).toBe(true)
    const registry = mergeRenderers(createTranscriptRenderers({ slot: 's1' }))
    expect(resolveRenderer(DONE, registry)?.id).toBe('handoff_done')
    // The store-free SDK registry still draws it as a system notice.
    expect(resolveRenderer(DONE, mergeRenderers([...defaultMessageRenderers]))?.id).toBe('system_notice')
    expect(isHandoffDoneRow(row({ kind: 'main_chat' }))).toBe(false)
    expect(isHandoffDoneRow(row({ kind: 'handoff_done' }))).toBe(false) // no chat named
    expect(isHandoffDoneRow({ ...DONE, role: 'user' })).toBe(false)
  })

  it('draws localized copy naming the chat, never the row text', () => {
    const { container } = render(<HandoffDoneNotice message={DONE} />)
    expect(screen.getByText('“Flaky tests” finished. Ask me here for what it found.')).toBeInTheDocument()
    expect(screen.queryByText('GATEWAY FALLBACK TEXT')).toBeNull()
    expect(container.querySelector('svg.lucide-circle-check')).not.toBeNull()
    // Nothing wired, nothing offered.
    expect(screen.queryByTestId('handoff-done-open')).toBeNull()
    expect(screen.queryByTestId('handoff-done-ask')).toBeNull()
  })

  it('Open hands the finished chat to the host', async () => {
    const onOpen = vi.fn()
    render(<HandoffDoneNotice message={DONE} onOpen={onOpen} />)
    const open = screen.getByTestId('handoff-done-open')
    expect(open).toHaveTextContent('Open “Flaky tests”')
    await userEvent.click(open)
    expect(onOpen).toHaveBeenCalledWith('chat-child-1')
  })

  it('Ask sends the question as the user, once', async () => {
    const onAsk = vi.fn()
    render(<HandoffDoneNotice message={DONE} onAsk={onAsk} />)
    const ask = screen.getByTestId('handoff-done-ask')
    expect(ask).toHaveTextContent('Ask for the result')
    await userEvent.click(ask)
    await userEvent.click(ask)
    expect(onAsk).toHaveBeenCalledTimes(1)
    expect(onAsk).toHaveBeenCalledWith('What did “Flaky tests” find?')
    expect(ask).toBeDisabled()
  })

  it('the registry wires Open to the session hand-off and Ask to the composer send', async () => {
    const onSessionOpen = vi.fn()
    const onSendAsUser = vi.fn()
    const registry = mergeRenderers(createTranscriptRenderers({ slot: 's1', onSessionOpen, onSendAsUser }))
    const entry = resolveRenderer(DONE, registry)!
    render(<>{entry.render(DONE, ctx())}</>)
    await userEvent.click(screen.getByTestId('handoff-done-open'))
    await userEvent.click(screen.getByTestId('handoff-done-ask'))
    expect(onSessionOpen).toHaveBeenCalledWith('chat-child-1')
    expect(onSendAsUser).toHaveBeenCalledWith('What did “Flaky tests” find?')
  })

  it('an error outcome warns, keeps Open and offers no result to ask for', async () => {
    const failed = row({ kind: 'handoff_done', slot: 'chat-child-1', title: 'Flaky tests', outcome: 'error' })
    expect(handoffDoneOf(failed)?.outcome).toBe('error')
    const onOpen = vi.fn()
    const onAsk = vi.fn()
    const { container } = render(<HandoffDoneNotice message={failed} onOpen={onOpen} onAsk={onAsk} />)
    expect(screen.getByText('“Flaky tests” stopped with an error. Open it to see what happened.')).toBeInTheDocument()
    expect(screen.queryByText('GATEWAY FALLBACK TEXT')).toBeNull()
    expect(container.querySelector('[data-testid="notice-card"]')).toHaveAttribute('data-tone', 'warn')
    expect(container.querySelector('svg.lucide-circle-check')).toBeNull()
    expect(screen.queryByTestId('handoff-done-ask')).toBeNull()
    await userEvent.click(screen.getByTestId('handoff-done-open'))
    expect(onOpen).toHaveBeenCalledWith('chat-child-1')
    expect(onAsk).not.toHaveBeenCalled()
    // A row from before outcomes existed, or with an unknown one, reads as done.
    expect(handoffDoneOf(DONE)?.outcome).toBe('done')
    expect(handoffDoneOf(row({ kind: 'handoff_done', slot: 'chat-child-1', outcome: 'exploded' }))?.outcome).toBe('done')
  })

  it('SystemNoticeRow draws it without actions on surfaces without the registry', () => {
    render(<SystemNoticeRow message={DONE} />)
    expect(screen.getByTestId('handoff-done-notice')).toBeInTheDocument()
    expect(screen.getByText('“Flaky tests” finished. Ask me here for what it found.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('flattens and bounds a title the transcript carries', () => {
    const long = row({ kind: 'handoff_done', slot: 'chat-child-1', title: `Line one\n  two ${'x'.repeat(200)}` })
    const done = handoffDoneOf(long)!
    expect(done.title.startsWith('Line one two ')).toBe(true)
    expect(Array.from(done.title)).toHaveLength(60)
    // An untitled row falls back to the chat's key.
    expect(handoffDoneOf(row({ kind: 'handoff_done', slot: 'chat-child-1' }))?.title).toBe('chat-child-1')
  })
})
