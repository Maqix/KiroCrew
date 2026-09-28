/**
 * SetupGuardrailNotice — the first run's stall and spent-allowance notices
 * (`setup_stalled`, `setup_quota`). Pins that the copy is keyed on the row's
 * meta (never its English content), that classic setup is always offered, that
 * only a failed kickoff offers Try again and that Try again posts the retry,
 * and that the transcript registry routes these rows here ahead of the plain
 * system notice.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupGuardrailNotice, { isSetupGuardrailRow } from '../components/setup/SetupGuardrailNotice'
import { mergeRenderers, resolveRenderer } from '../app-sdk/messageRenderers'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import { isSystemNoticeRow } from '../pages/chat/CompactionCard'
import type { ChatMessage } from '../types'

function row(meta: Record<string, unknown>): ChatMessage {
  // The content is the gateway's English fallback; the notice must not draw it.
  return { role: 'assistant', content: 'GATEWAY FALLBACK TEXT', ts: '2026-09-28T10:00:00Z', meta } as ChatMessage
}

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function renderNotice(message: ChatMessage) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route path="*" element={<><SetupGuardrailNotice message={message} /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SetupGuardrailNotice', () => {
  it('is a system notice the registry routes to its own renderer', () => {
    const stalled = row({ kind: 'setup_stalled', reason: 'no_output', secs: 90 })
    const quota = row({ kind: 'setup_quota' })
    for (const m of [stalled, quota]) {
      expect(isSystemNoticeRow(m)).toBe(true)
      expect(isSetupGuardrailRow(m)).toBe(true)
      const registry = mergeRenderers(createTranscriptRenderers({ slot: 's1' }))
      expect(resolveRenderer(m, registry)?.id).toBe('setup_guardrail')
    }
    expect(isSetupGuardrailRow(row({ kind: 'main_chat' }))).toBe(false)
  })

  it('draws the stall copy with the elapsed time and offers classic setup only', async () => {
    renderNotice(row({ kind: 'setup_stalled', reason: 'no_output', secs: 90 }))
    expect(screen.getByText(/No reply in 1m 30s/)).toBeInTheDocument()
    expect(screen.queryByText('GATEWAY FALLBACK TEXT')).toBeNull()
    expect(screen.queryByTestId('setup-guardrail-retry')).toBeNull()
    await userEvent.click(screen.getByTestId('setup-guardrail-classic'))
    expect(screen.getByTestId('location')).toHaveTextContent('/onboarding')
  })

  it('draws the quota copy', () => {
    renderNotice(row({ kind: 'setup_quota' }))
    expect(screen.getByText(/model allowance has run out/)).toBeInTheDocument()
    expect(screen.queryByTestId('setup-guardrail-retry')).toBeNull()
    expect(screen.getByTestId('setup-guardrail-classic')).toBeInTheDocument()
  })

  it('a failed kickoff posts the retry and hides the button once it is sent', async () => {
    let posts = 0
    server.use(
      http.post('/api/setup/first-run/retry', () => {
        posts += 1
        return HttpResponse.json({ slot: 'chat-1-1' })
      }),
    )
    renderNotice(row({ kind: 'setup_stalled', reason: 'kickoff_failed' }))
    expect(screen.getByText(/Setup didn’t start/)).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('setup-guardrail-retry'))
    await waitFor(() => expect(posts).toBe(1))
    await waitFor(() => expect(screen.queryByTestId('setup-guardrail-retry')).toBeNull())
  })

  it('a refused retry says why', async () => {
    server.use(
      http.post('/api/setup/first-run/retry', () =>
        HttpResponse.json({ error: 'setup already started in this chat', code: 'kickoff_answered' }, { status: 409 }),
      ),
    )
    renderNotice(row({ kind: 'setup_stalled', reason: 'kickoff_failed' }))
    await userEvent.click(screen.getByTestId('setup-guardrail-retry'))
    expect(await screen.findByText('Setup has already started in this chat.')).toBeInTheDocument()
  })
})
