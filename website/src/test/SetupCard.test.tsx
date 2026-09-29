/**
 * SetupCard — the rendered face of a server-side setup action (one-chat first
 * run). Driven through MSW at the network boundary, so every assertion is about
 * what the gateway was sent and what the owner sees:
 *
 *   - each kind renders from `GET /api/setup/cards/{id}` (never from row text);
 *   - every decide posts the card's `hash`;
 *   - a credential / bot token is cleared from its field, never rendered back,
 *     and never lands in a React Query cache (queries OR mutation variables);
 *   - high-stakes cards carry the distinct marker, low-stakes cards do not;
 *   - the cron card's preview → keep two-step;
 *   - "Use classic setup" routes or dispatches its window event;
 *   - terminal states collapse into a result line.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import type React from 'react'

import { server } from '../../integration/mocks/server'
import SetupCard, { SETUP_CARD_POLL_MS } from '../components/setup/SetupCard'
import { mergeRenderers, resolveRenderer, type MessageRenderContext } from '../app-sdk/messageRenderers'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'
import type { ChatMessage } from '../types'

const HASH = 'a'.repeat(64)

function card(over: Partial<Card> & Pick<Card, 'kind'>): Card {
  return {
    id: 'sc-0123456789abcdef',
    slot: 'chat-1-1790000000',
    status: 'pending',
    stakes: 'low',
    hash: HASH,
    payload: {},
    outcome: null,
    error: null,
    created_ts: 1790000000,
    decided_ts: null,
    classic: { kind: 'none', target: '' },
    ...over,
  }
}

/** A one-card in-memory gateway. `decide` computes the card the POST returns. */
function serveCard(initial: Card, decide?: (body: SetupDecideBody, current: Card) => Card | Response) {
  const state = { card: initial, gets: 0, bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${initial.id}`, () => {
      state.gets += 1
      return HttpResponse.json(state.card)
    }),
    http.post(`/api/setup/cards/${initial.id}/decide`, async ({ request }) => {
      const body = (await request.json()) as SetupDecideBody
      state.bodies.push(body)
      const next = decide ? decide(body, state.card) : { ...state.card, status: 'committed' as const }
      if (next instanceof Response) return next
      state.card = next
      return HttpResponse.json(next)
    }),
  )
  return state
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname}</div>
}

function renderCard(id = 'sc-0123456789abcdef', ui?: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route path="*" element={<>{ui ?? <SetupCard cardId={id} />}<LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { qc, ...utils }
}

const ready = () => screen.findByTestId('setup-card')

beforeEach(() => {
  // The privacy card's toggle reads the beacon status; answer it for every test.
  server.use(http.get('/api/telemetry/beacon', () => HttpResponse.json({ enabled: true, would_send: true })))
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SetupCard — renders each kind from the gateway', () => {
  it('privacy: the same disclosure the Privacy chapter shows, and Continue sends the toggle position', async () => {
    const gw = serveCard(card({ kind: 'privacy' }))
    renderCard()
    await ready()
    // Same components, same keys as PrivacyChapter.
    expect(screen.getByRole('heading', { name: 'Privacy' })).toBeInTheDocument()
    expect(screen.getByText('Anonymous daily heartbeat')).toBeInTheDocument()
    expect(screen.getByText('kirocrew telemetry disable')).toBeInTheDocument()
    // Mandatory: no "Not now".
    expect(screen.queryByTestId('setup-card-decline')).toBeNull()
    const cont = screen.getByTestId('setup-card-primary')
    await waitFor(() => expect(cont).toBeEnabled())
    await userEvent.click(cont)
    await waitFor(() => expect(gw.bodies).toHaveLength(1))
    expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH, input: { telemetry: true } })
  })

  it('profile: lists the proposed fields and saves with the hash', async () => {
    const gw = serveCard(card({
      kind: 'profile',
      payload: { fields: { bot_name: 'Pixel', timezone: 'Europe/Berlin', technical_level: 'codes' } },
    }))
    renderCard()
    const el = await ready()
    expect(within(el).getByText('Your profile')).toBeInTheDocument()
    expect(within(el).getByText('Pixel')).toBeInTheDocument()
    expect(within(el).getByText('Europe/Berlin')).toBeInTheDocument()
    expect(within(el).getByText('Writes code')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH }))
  })

  it('soul: shows the proposed text, and the current file behind a toggle', async () => {
    serveCard(card({ kind: 'soul', payload: { file: 'SOUL', content: 'Be brief.', previous: 'Be verbose.' } }))
    renderCard()
    const el = await ready()
    expect(within(el).getByText('Update SOUL.md')).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-soul-content')).toHaveTextContent('Be brief.')
    await userEvent.click(screen.getByRole('radio', { name: 'Current' }))
    expect(screen.getByTestId('setup-card-soul-content')).toHaveTextContent('Be verbose.')
  })

  it('import: every category checked by default, and an unchecked one is sent as an exclusion', async () => {
    const gw = serveCard(card({
      kind: 'import',
      payload: {
        sources: [{ id: 'hermes', name: 'Hermes', categories: [
          { id: 'memory', label: 'Memory', count: 3 },
          { id: 'skills', label: 'Skills', count: 2 },
        ] }],
        jobs: [{ name: 'Morning brief', schedule_human: 'every day at 8:00', note: 'delivery re-pointed' }],
      },
      classic: { kind: 'event', target: 'mc-start-import' },
    }))
    renderCard()
    await ready()
    const memory = screen.getByTestId('setup-card-import-hermes-memory') as HTMLInputElement
    const skills = screen.getByTestId('setup-card-import-hermes-skills') as HTMLInputElement
    expect(memory.checked).toBe(true)
    expect(skills.checked).toBe(true)
    expect(screen.getByText('Morning brief')).toBeInTheDocument()
    await userEvent.click(skills)
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toHaveLength(1))
    expect(gw.bodies[0]).toEqual({
      decision: 'commit',
      hash: HASH,
      input: { exclude: [{ source_id: 'hermes', category_id: 'skills' }] },
    })
  })

  it('connect: an explicit verb, then the consent link once the gateway is waiting', async () => {
    const gw = serveCard(
      card({ kind: 'connect', stakes: 'high', payload: { provider: { slug: 'github', name: 'GitHub', category: 'dev' }, needs_client_config: false } }),
      (_body, current) => ({ ...current, status: 'waiting', outcome: { state: 'waiting', oauth_url: 'https://github.com/login/oauth/authorize?x=1' } }),
    )
    renderCard()
    await ready()
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('Connect GitHub')
    await userEvent.click(primary)
    const link = await screen.findByTestId('setup-card-consent-link')
    expect(link).toHaveAttribute('href', 'https://github.com/login/oauth/authorize?x=1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH })
    // No decline once the card left `pending`: the gateway would refuse it.
    expect(screen.queryByTestId('setup-card-decline')).toBeNull()
  })

  it('connect: a non-http consent URL is never rendered as a link', async () => {
    serveCard(card({
      kind: 'connect', stakes: 'high', status: 'waiting',
      payload: { provider: { slug: 'x', name: 'X', category: 'dev' } },
      outcome: { state: 'waiting', oauth_url: 'javascript:alert(1)' },
    }))
    renderCard()
    await ready()
    expect(screen.queryByTestId('setup-card-consent-link')).toBeNull()
  })

  it('channel: token field, then the /pair instruction with a copy button', async () => {
    const gw = serveCard(
      card({ kind: 'channel', stakes: 'high', payload: { channel: 'telegram', label: 'Telegram' }, classic: { kind: 'route', target: '/settings' } }),
      (_body, current) => ({ ...current, status: 'waiting', outcome: { pair_code: '4821', bot_username: 'my_bot' } }),
    )
    renderCard()
    await ready()
    const field = screen.getByTestId('setup-card-secret') as HTMLInputElement
    expect(field.type).toBe('password')
    expect(field.getAttribute('autocomplete')).toBe('off')
    await userEvent.type(field, '123:ABC-token')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toHaveLength(1))
    expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH, input: { token: '123:ABC-token' } })
    expect(await screen.findByTestId('setup-card-pair')).toHaveTextContent('/pair 4821')
    expect(screen.getByText(/send the message below to @my_bot/)).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-pair-copy')).toHaveAccessibleName('Copy pairing message')
  })

  it('service: the command to copy when it needs a terminal, and a retryable "not installed yet"', async () => {
    const gw = serveCard(
      card({ kind: 'service', stakes: 'high', payload: { platform: 'linux', command: 'kirocrew service install', needs_terminal: true, installed: false } }),
      (_body, current) => ({ ...current, status: 'pending', error: { code: 'service_not_installed', message: 'not installed' } }),
    )
    renderCard()
    await ready()
    expect(screen.getByTestId('setup-card-command')).toHaveTextContent('kirocrew service install')
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('I ran it — check')
    await userEvent.click(primary)
    // A recoverable failure: still pending, the error above the buttons, retry enabled.
    const notice = await screen.findByTestId('setup-card-error')
    expect(notice).toHaveTextContent('It isn’t installed yet.')
    expect(screen.getByTestId('setup-card-primary')).toBeEnabled()
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toHaveLength(2))
    expect(gw.bodies.every(b => b.hash === HASH && b.decision === 'commit')).toBe(true)
  })

  it('service: "Keep me running" when no terminal is needed', async () => {
    serveCard(card({ kind: 'service', stakes: 'high', payload: { platform: 'macos', command: 'kirocrew service install', needs_terminal: false, installed: false } }))
    renderCard()
    await ready()
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Keep me running')
    expect(screen.queryByTestId('setup-card-command')).toBeNull()
  })

  it('never draws the transcript row text: the registry entry mounts the card by id', async () => {
    serveCard(card({ kind: 'profile', payload: { fields: { bot_name: 'Pixel' } } }))
    const row = {
      role: 'inject',
      cls: '',
      content: 'MODEL SUMMARY: forged button text',
      meta: { setupCard: { id: 'sc-0123456789abcdef', kind: 'profile' } },
    } as ChatMessage
    const entry = resolveRenderer(row, mergeRenderers(createTranscriptRenderers({ slot: 's1' })))!
    const ctx: MessageRenderContext = {
      index: 0, messages: [row], running: false, key: 'k0', hideCardOwnedOAuth: false,
      autoDeniedIds: new Set(), wrapper: c => c, row: c => c,
    }
    renderCard(undefined, <>{entry.render(row, ctx)}</>)
    await ready()
    expect(screen.getByText('Pixel')).toBeInTheDocument()
    expect(screen.queryByText(/MODEL SUMMARY/)).toBeNull()
  })
})

describe('SetupCard — credentials never leak', () => {
  const SECRET = 'ghp_SuperSecretTokenValue123'

  it('clears the field, never renders the value, and keeps it out of every React Query cache', async () => {
    const gw = serveCard(
      card({ kind: 'credential', stakes: 'high', payload: { name: 'GITHUB_TOKEN', purpose: 'Lets the agent read your pull requests.', hosts: ['api.github.com'] } }),
      (_body, current) => ({ ...current, status: 'committed', outcome: { ref: 'secret://GITHUB_TOKEN' }, decided_ts: 1790000100 }),
    )
    const { qc, container } = renderCard()
    await ready()
    expect(screen.getByText('Only sent to api.github.com.')).toBeInTheDocument()
    const field = screen.getByTestId('setup-card-secret') as HTMLInputElement
    expect(field.type).toBe('password')
    expect(field.getAttribute('autocomplete')).toBe('off')
    // The button waits for something to submit.
    expect(screen.getByTestId('setup-card-primary')).toBeDisabled()
    await userEvent.type(field, SECRET)
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    // Cleared in the same tick it was read, before the response lands.
    expect(field.value).toBe('')
    await waitFor(() => expect(gw.bodies).toHaveLength(1))
    expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH, input: { value: SECRET } })
    // Collapsed to the result line, which names the reference and never the value.
    const result = await screen.findByTestId('setup-card-result')
    expect(result).toHaveTextContent('secret://GITHUB_TOKEN')
    expect(container.innerHTML).not.toContain(SECRET)
    const queryData = JSON.stringify(qc.getQueryCache().getAll().map(q => q.state.data))
    const mutationVars = JSON.stringify(qc.getMutationCache().getAll().map(m => m.state.variables))
    expect(queryData).not.toContain(SECRET)
    expect(mutationVars).not.toContain(SECRET)
  })

  it('a refused store still clears the field and shows the error, not the value', async () => {
    serveCard(
      card({ kind: 'credential', stakes: 'high', payload: { name: 'API_KEY', purpose: 'x', hosts: [] } }),
      () => HttpResponse.json({ error: 'empty', code: 'credential_empty' }, { status: 422 }) as unknown as Response,
    )
    const { container } = renderCard()
    await ready()
    const field = screen.getByTestId('setup-card-secret') as HTMLInputElement
    await userEvent.type(field, SECRET)
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    expect(field.value).toBe('')
    expect(await screen.findByTestId('setup-card-error')).toHaveTextContent('Paste a value first.')
    expect(container.innerHTML).not.toContain(SECRET)
    // Back to pending: the owner can paste again.
    expect(screen.getByTestId('setup-card')).toHaveAttribute('data-status', 'pending')
  })
})

describe('SetupCard — stakes', () => {
  it('marks a high-stakes card distinctly: accent border, shield badge, explicit verb', async () => {
    serveCard(card({ id: 'sc-high', kind: 'credential', stakes: 'high', payload: { name: 'K', purpose: 'p', hosts: [] } }))
    renderCard('sc-high')
    const high = await ready()
    expect(high).toHaveAttribute('data-stakes', 'high')
    expect(within(high).getByTestId('setup-card-high-stakes')).toHaveTextContent('Needs your approval')
    expect(high.className).toContain('border-accent')
    // The explicit verb, never a generic "Next".
    expect(within(high).getByTestId('setup-card-primary')).toHaveTextContent('Store in vault')
  })

  it('a low-stakes card carries no approval marker', async () => {
    serveCard(card({ id: 'sc-low', kind: 'profile', payload: { fields: { role: 'SRE' } } }))
    renderCard('sc-low')
    const low = await ready()
    expect(low).toHaveAttribute('data-stakes', 'low')
    expect(within(low).queryByTestId('setup-card-high-stakes')).toBeNull()
    expect(low.className).not.toContain('border-accent')
  })
})

describe('SetupCard — cron: preview now, then keep it', () => {
  it('runs a preview first, then offers Keep it as the primary action', async () => {
    const gw = serveCard(
      card({ kind: 'cron', payload: { name: 'Morning brief', prompt_summary: 'Reviews waiting on you', schedule_human: 'every weekday at 8:00', timezone: 'Europe/Berlin' }, classic: { kind: 'route', target: '/schedule' } }),
      (body, current) => body.decision === 'preview'
        ? { ...current, status: 'pending', outcome: { preview: { status: 'success', text: '**3 reviews** are waiting.' } } }
        : { ...current, status: 'committed', outcome: { job_id: 'job-1' } },
    )
    renderCard()
    await ready()
    expect(screen.getByText('Runs every weekday at 8:00 (Europe/Berlin)')).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Run a preview now')
    expect(screen.getByTestId('setup-card-secondary')).toHaveTextContent('Keep it')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    const preview = await screen.findByTestId('setup-card-preview')
    expect(preview).toHaveAttribute('data-preview-status', 'success')
    // Markdown, through the chat's own renderer.
    expect(within(preview).getByText('3 reviews').tagName).toBe('STRONG')
    expect(gw.bodies[0]).toEqual({ decision: 'preview', hash: HASH })
    // Now Keep it leads, and a re-run is the secondary.
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Keep it')
    expect(screen.getByTestId('setup-card-secondary')).toHaveTextContent('Run it again')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await screen.findByTestId('setup-card-result')
    expect(gw.bodies[1]).toEqual({ decision: 'commit', hash: HASH })
  })

  it('shows a failed preview through the error surface', async () => {
    serveCard(card({ kind: 'cron', payload: { name: 'n', schedule_human: 'hourly' }, outcome: { preview: { status: 'timeout', text: 'no answer in 120s' } } }))
    renderCard()
    await ready()
    const preview = screen.getByTestId('setup-card-preview')
    expect(preview).toHaveAttribute('data-preview-status', 'timeout')
    expect(within(preview).getByRole('alert')).toHaveTextContent('The preview run timed out')
  })

  it('polls while the preview is working and paints the result when it lands', async () => {
    const gw = serveCard(card({ kind: 'cron', status: 'working', payload: { name: 'n', schedule_human: 'hourly' } }))
    renderCard()
    await ready()
    await waitFor(() => expect(gw.gets).toBe(1))
    gw.card = { ...gw.card, status: 'pending', outcome: { preview: { status: 'success', text: 'fresh output' } } }
    // One poll interval (SETUP_CARD_POLL_MS) is the wait; the extra second is
    // slack for the refetch round trip under a loaded runner.
    expect(await screen.findByText('fresh output', undefined, { timeout: SETUP_CARD_POLL_MS + 2000 })).toBeInTheDocument()
  }, 15_000)
})

describe('SetupCard — cron: the approvals a preview needs', () => {
  const WAITING = { id: 'req-1', tool: 'git log', tool_input: 'git log --oneline -5', tool_purpose: '', ts: 1790000001 }

  /** Serve the running preview's approvals and record every one-shot answer. */
  function serveApprovals(approvals: unknown[]) {
    const answered: string[] = []
    server.use(
      http.get('/api/setup/cards/:id/approvals', () => HttpResponse.json({ approvals })),
      http.post('/api/approvals/:id/:action', ({ params }) => {
        answered.push(`${params.id}/${params.action}`)
        return HttpResponse.json({ ok: true })
      }),
    )
    return answered
  }

  it('shows a running preview\'s approval on the card and answers it once', async () => {
    serveCard(card({ kind: 'cron', status: 'working', payload: { name: 'Dev brief', schedule_human: 'every weekday at 8:00' } }))
    const answered = serveApprovals([WAITING])
    renderCard()
    const row = await screen.findByTestId('setup-card-preview-approval')
    expect(within(row).getByText('git log')).toBeInTheDocument()
    expect(within(row).getByText('git log --oneline -5')).toBeInTheDocument()
    expect(screen.getByText('Waiting for your approval')).toBeInTheDocument()
    // One-shot verbs only: nothing on the card records a standing grant.
    expect(within(row).queryByText(/trust/i)).toBeNull()
    await userEvent.click(within(row).getByTestId('setup-card-approval-allow'))
    await waitFor(() => expect(answered).toEqual(['req-1/approve']))
  })

  it('rejects through the same one-shot path', async () => {
    serveCard(card({ kind: 'cron', status: 'working', payload: { name: 'n' } }))
    const answered = serveApprovals([WAITING])
    renderCard()
    await userEvent.click(await screen.findByTestId('setup-card-approval-reject'))
    await waitFor(() => expect(answered).toEqual(['req-1/reject']))
  })

  it('says a request answered elsewhere is gone', async () => {
    serveCard(card({ kind: 'cron', status: 'working', payload: { name: 'n' } }))
    serveApprovals([WAITING])
    server.use(http.post('/api/approvals/:id/:action', () =>
      HttpResponse.json({ error: 'not found or expired' }, { status: 404 })))
    renderCard()
    await userEvent.click(await screen.findByTestId('setup-card-approval-allow'))
    expect(await screen.findByText(/no longer waiting/)).toBeInTheDocument()
  })

  it('a preview whose approval was not given is a failure, and the next step is another preview', async () => {
    serveCard(card({
      kind: 'cron',
      payload: { name: 'Dev brief' },
      outcome: { preview: {
        status: 'failure',
        reason: 'approval_not_given',
        text: "The git commands were rejected, so I can't produce the brief.",
        approvals: { asked: 2, allowed: 0, rejected: 1, unanswered: 1, wait_secs: 180 },
      } },
    }))
    renderCard()
    await ready()
    const preview = screen.getByTestId('setup-card-preview')
    expect(preview).toHaveAttribute('data-preview-status', 'failure')
    expect(within(preview).getByRole('alert')).toHaveTextContent(
      '2 steps needed your approval and did not get it, so the job could not finish.')
    const asks = screen.getByTestId('setup-card-preview-asks')
    expect(asks).toHaveTextContent('asked for your approval 2 times during the preview, and it will ask again on every run')
    expect(asks).toHaveTextContent('Notifications')
    expect(asks).toHaveTextContent('within 3 minutes is declined')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Run a preview now')
    expect(screen.getByTestId('setup-card-secondary')).toHaveTextContent('Keep it')
  })

  it('a preview whose approvals were all allowed can be kept, and says it will ask again', async () => {
    serveCard(card({
      kind: 'cron',
      payload: { name: 'Dev brief' },
      outcome: { preview: {
        status: 'success',
        text: 'Two commits since yesterday.',
        approvals: { asked: 1, allowed: 1, rejected: 0, unanswered: 0, wait_secs: 180 },
      } },
    }))
    renderCard()
    await ready()
    expect(screen.getByText('Two commits since yesterday.')).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-preview-asks')).toHaveTextContent(
      'This job asked for your approval 1 time during the preview')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Keep it')
  })

  it('a preview that asked nothing says nothing about approvals', async () => {
    serveCard(card({ kind: 'cron', payload: { name: 'n' }, outcome: { preview: { status: 'success', text: 'ok' } } }))
    renderCard()
    await ready()
    expect(screen.queryByTestId('setup-card-preview-asks')).toBeNull()
    expect(screen.queryByTestId('setup-card-preview-approvals')).toBeNull()
  })
})

describe('SetupCard — classic setup and decline', () => {
  it('dispatches the classic event with continueOnboarding for the import card', async () => {
    serveCard(card({ kind: 'import', payload: { sources: [], jobs: [] }, classic: { kind: 'event', target: 'mc-start-import' } }))
    const seen: Array<{ continueOnboarding?: boolean }> = []
    const onEvent = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener('mc-start-import', onEvent)
    try {
      renderCard()
      await ready()
      await userEvent.click(screen.getByTestId('setup-card-classic'))
      expect(seen).toEqual([{ continueOnboarding: true }])
    } finally {
      window.removeEventListener('mc-start-import', onEvent)
    }
  })

  it('routes to the classic page', async () => {
    serveCard(card({ id: 'sc-route', kind: 'cron', payload: { name: 'n' }, classic: { kind: 'route', target: '/schedule' } }))
    renderCard('sc-route')
    await ready()
    await userEvent.click(screen.getByTestId('setup-card-classic'))
    expect(screen.getByTestId('location')).toHaveTextContent('/schedule')
  })

  it('offers no classic link when the gateway says none', async () => {
    serveCard(card({ id: 'sc-none', kind: 'profile', payload: { fields: {} }, classic: { kind: 'none', target: '' } }))
    renderCard('sc-none')
    await ready()
    expect(screen.queryByTestId('setup-card-classic')).toBeNull()
  })

  it('an off-app route target is dropped', async () => {
    serveCard(card({ id: 'sc-evil', kind: 'profile', payload: { fields: {} }, classic: { kind: 'route', target: '//evil.example/x' } }))
    renderCard('sc-evil')
    await ready()
    expect(screen.queryByTestId('setup-card-classic')).toBeNull()
  })

  it('"Not now" declines with the hash', async () => {
    const gw = serveCard(card({ kind: 'profile', payload: { fields: { role: 'SRE' } } }), (_b, c) => ({ ...c, status: 'declined' }))
    renderCard()
    await ready()
    await userEvent.click(screen.getByTestId('setup-card-decline'))
    await screen.findByTestId('setup-card-result')
    expect(gw.bodies[0]).toEqual({ decision: 'decline', hash: HASH })
  })
})

describe('SetupCard — terminal states', () => {
  it.each([
    ['committed', 'Done'],
    ['declined', 'Skipped'],
    ['expired', 'Expired'],
  ] as const)('%s collapses into a compact result line', async (status, word) => {
    serveCard(card({ kind: 'profile', status, payload: { fields: { role: 'SRE' } } }))
    renderCard()
    const el = await ready()
    expect(el).toHaveAttribute('data-status', status)
    const result = within(el).getByTestId('setup-card-result')
    expect(result).toHaveTextContent('Your profile')
    expect(result).toHaveTextContent(word)
    expect(within(el).queryByRole('button')).toBeNull()
  })

  it('a failed card names the failure through the error surface', async () => {
    serveCard(card({ kind: 'connect', stakes: 'high', status: 'failed', payload: { provider: { name: 'GitHub' } }, error: { code: 'oauth_denied', message: 'Access was denied on GitHub.' } }))
    renderCard()
    const el = await ready()
    expect(within(el).getByTestId('setup-card-result')).toHaveTextContent('Didn’t finish')
    expect(within(el).getByTestId('setup-card-failed-error')).toHaveTextContent('Access was denied on GitHub.')
  })

  it('an import result counts what came over', async () => {
    serveCard(card({ kind: 'import', status: 'committed', payload: { sources: [] }, outcome: { imported_count: 5, imported: { memory: 5 }, jobs_added_disabled: 1 } }))
    renderCard()
    await ready()
    const detail = screen.getByTestId('setup-card-result-detail')
    expect(detail).toHaveTextContent('Brought over 5 items')
    expect(detail).toHaveTextContent('1 scheduled job added, paused')
  })

  it('a 409 on a card already answered elsewhere refetches it', async () => {
    const gw = serveCard(card({ kind: 'profile', payload: { fields: { role: 'SRE' } } }), (_b, c) => {
      gw.card = { ...c, status: 'committed' }
      return HttpResponse.json({ error: 'not pending', code: 'card_not_pending' }, { status: 409 }) as unknown as Response
    })
    renderCard()
    await ready()
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(screen.getByTestId('setup-card')).toHaveAttribute('data-status', 'committed'))
  })
})

describe('SetupCard — home: a permanent home in the owner’s AWS account', () => {
  const PAYLOAD = {
    provider: { id: 'aws_ec2', label: 'Your AWS account' },
    simulated: true,
    region: 'eu-west-1',
    profile: 'default',
    size: { key: 'balanced', label: 'Development', instance_type: 't3.large', ram_gb: 8, vcpu: 2 },
    monthly_usd: 61,
    billed_by: 'AWS, to your own account',
    aws_signed_in: false,
    aws_account: '',
    sign_in_commands: ['aws login', 'aws configure sso'],
  }
  const home = (over: Partial<Card> = {}) =>
    card({ kind: 'home', stakes: 'high', payload: PAYLOAD, classic: { kind: 'route', target: '/settings' }, ...over })

  it('shows what the owner is agreeing to and a Simulated badge; a simulated home needs no AWS sign-in', async () => {
    const gw = serveCard(home())
    renderCard()
    const el = await ready()
    expect(el).toHaveAttribute('data-stakes', 'high')
    expect(within(el).getByText('Your home in the cloud')).toBeInTheDocument()
    // Cost through the locale's currency formatter, and who bills it.
    expect(screen.getByTestId('setup-card-home-cost')).toHaveTextContent('About $61/month, billed by AWS, to your own account')
    expect(screen.getByTestId('setup-card-home-size')).toHaveTextContent('Development (t3.large), vCPU: 2, memory: 8GB')
    expect(within(el).getByText('eu-west-1')).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-simulated')).toHaveTextContent('Simulated — no AWS resources are created')
    expect(screen.queryByTestId('setup-card-home-aws-signin')).toBeNull()
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('Build my home')
    await userEvent.click(primary)
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH }))
  })

  it('offers "Build my home" once AWS is signed in, and names the account', async () => {
    serveCard(home({ payload: { ...PAYLOAD, simulated: false, aws_signed_in: true, aws_account: '…1234' } }))
    renderCard()
    const el = await ready()
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Build my home')
    expect(screen.queryByTestId('setup-card-home-aws-signin')).toBeNull()
    expect(screen.queryByTestId('setup-card-simulated')).toBeNull()
    expect(screen.getByTestId('setup-card-home-meta')).toHaveTextContent('AWS: signed in ✓ …1234 · eu-west-1')
    expect(el).toBeInTheDocument()
  })

  it('renders the build as a progress list, with the Kiro sign-in link and code', async () => {
    serveCard(home({
      status: 'waiting',
      outcome: {
        job_id: 'job-1', status: 'running', error: null,
        steps: [
          { key: 'stack', label: 'Create the server', state: 'done', detail: '' },
          { key: 'boot', label: 'Start Kiro Crew', state: 'active', detail: 'about a minute' },
          { key: 'tunnel', label: 'Connect it', state: 'pending', detail: '' },
        ],
        signin: { url: 'https://view.awsapps.com/start/#/device', code: 'ABCD-EFGH' },
      },
    }))
    renderCard()
    await ready()
    expect(screen.getByText(/You can keep chatting meanwhile/)).toBeInTheDocument()
    const steps = within(screen.getByTestId('setup-card-steps')).getAllByRole('listitem')
    expect(steps.map(li => li.getAttribute('data-state'))).toEqual(['done', 'active', 'pending'])
    // The state is spoken, not only drawn.
    expect(steps[0]).toHaveTextContent('Finished Create the server')
    const link = screen.getByTestId('setup-card-home-signin-link')
    expect(link).toHaveAttribute('href', 'https://view.awsapps.com/start/#/device')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByTestId('setup-card-home-signin-code')).toHaveTextContent('ABCD-EFGH')
    // Nothing to click while it builds; the gateway would refuse a decide.
    expect(screen.queryByTestId('setup-card-primary')).toBeNull()
    expect(screen.queryByTestId('setup-card-decline')).toBeNull()
  })

  it('moves in once ready: move steps while working, then a simulated result line', async () => {
    const gw = serveCard(
      home({ outcome: { job_id: 'job-1', status: 'ready', ready: true, steps: [{ key: 'stack', label: 'Create the server', state: 'done' }] } }),
      (_b, current) => ({
        ...current,
        status: 'working',
        outcome: { ...current.outcome, move_steps: [{ key: 'copy', label: 'Copy your crew', state: 'active' }] },
      }),
    )
    renderCard()
    await ready()
    expect(screen.getByText('Your home is ready. Move your crew and this chat there?')).toBeInTheDocument()
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('Move in')
    await userEvent.click(primary)
    const move = await screen.findByTestId('setup-card-move-steps')
    expect(within(move).getByText('Copy your crew')).toBeInTheDocument()
    expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH })
  })

  it('a finished simulated move says nothing was created', async () => {
    serveCard(home({ status: 'committed', outcome: { moved: true, simulated: true } }))
    renderCard()
    await ready()
    expect(screen.getByTestId('setup-card-result')).toHaveTextContent('Done')
    expect(screen.getByTestId('setup-card-result-detail')).toHaveTextContent('Simulated move — nothing was created in AWS.')
  })

  it('renders a recoverable refusal through the error surface, still pending', async () => {
    serveCard(home({ error: { code: 'aws_not_signed_in', message: 'not signed in' } }))
    renderCard()
    await ready()
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent('You aren’t signed in to AWS yet.')
    expect(screen.getByTestId('setup-card-primary')).toBeEnabled()
  })
})

describe('SetupCard — a kind this build does not know', () => {
  it('renders the generic title with Approve and Not now, and nothing from the payload', async () => {
    const gw = serveCard(card({
      // A kind the gateway registered before this dashboard build learned it.
      kind: 'teleport' as Card['kind'],
      payload: { name: '<img src=x onerror=alert(1)>', note: 'Beam the crew to Mars' },
    }))
    const { container } = renderCard()
    const el = await ready()
    expect(within(el).getByRole('heading', { name: 'Setup step' })).toBeInTheDocument()
    expect(el).not.toHaveTextContent('Beam the crew to Mars')
    expect(el).not.toHaveTextContent('onerror')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('setup-card-decline')).toHaveTextContent('Not now')
    const approve = screen.getByTestId('setup-card-primary')
    expect(approve).toHaveTextContent('Approve')
    await userEvent.click(approve)
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH }))
  })

  it('declines with the hash like any card', async () => {
    const gw = serveCard(card({ kind: 'teleport' as Card['kind'] }))
    renderCard()
    await ready()
    await userEvent.click(screen.getByTestId('setup-card-decline'))
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'decline', hash: HASH }))
  })
})
