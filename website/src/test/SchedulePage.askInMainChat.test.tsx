/**
 * MC.6 on the jobs page: once there is a main chat, a job's actions menu offers
 * "Ask in main chat", which opens the main chat with `Change the job "<name>": `
 * pre-filled (never sent) so the user finishes the request and the agent
 * proposes the change through the usual card. No main chat, no entry.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor, fireEvent } from '@testing-library/react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { renderWithProviders } from './helpers'
import SchedulePage from '../pages/SchedulePage'
import { PREFILL_STORAGE_KEY } from '../utils/navIntent'
import type { CronJob } from '../types'

vi.mock('../api/client', () => ({
  api: {
    crons: vi.fn(),
    cronFolders: vi.fn().mockResolvedValue([]),
    deleteCron: vi.fn(),
    batchDeleteCron: vi.fn(),
    createCron: vi.fn().mockResolvedValue({}),
    models: vi.fn().mockResolvedValue([]),
    updateCron: vi.fn().mockResolvedValue({}),
    toggleCron: vi.fn().mockResolvedValue({}),
    runCron: vi.fn().mockResolvedValue({}),
    cronToChat: vi.fn().mockResolvedValue({}),
    cronHistoryAll: vi.fn().mockResolvedValue({ runs: [] }),
    kirocrewAgents: vi.fn().mockResolvedValue({ agents: [], default_agent: '' }),
    agentCatalog: vi.fn().mockResolvedValue({ agents: [], default_agent: '' }),
    // The page now SAYS when the default-agent read fails; an unmocked
    // `api.defaultAgent` would surface that notice in every case here.
    defaultAgent: vi.fn().mockResolvedValue({ default_agent: '' }),
    // The boot payload carries the main chat (RFC §6.9).
    themeBoot: vi.fn().mockResolvedValue({}),
  },
}))

const job = { id: 'job-1', name: 'Morning brief', schedule: 'every 1d', message: 'brief me', enabled: true } as CronJob

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname + loc.search}</div>
}

async function openJobMenu() {
  await waitFor(() => expect(screen.getByText('Morning brief')).toBeInTheDocument())
  fireEvent.keyDown(screen.getByLabelText('Actions'), { key: 'Enter' })
  // Wait for the menu content to mount (Strict lives only in the menu) before
  // asserting on its entries.
  await screen.findByText('Strict')
}

describe('SchedulePage — Ask in main chat', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    sessionStorage.clear()
    const { api } = await import('../api/client')
    vi.mocked(api).crons.mockResolvedValue({ jobs: [job] })
  })

  it('is not offered before there is a main chat', async () => {
    const { api } = await import('../api/client')
    vi.mocked(api).themeBoot.mockResolvedValue({ main_slot: null })
    renderWithProviders(<SchedulePage />, { route: '/schedule' })
    await openJobMenu()
    expect(screen.queryByTestId('cron-ask-in-main-chat')).toBeNull()
  })

  it('pre-fills the main chat with a change request for this job and opens it', async () => {
    const { api } = await import('../api/client')
    vi.mocked(api).themeBoot.mockResolvedValue({ main_slot: 'chat-main-1' })
    renderWithProviders(
      <Routes><Route path="*" element={<><SchedulePage /><LocationProbe /></>} /></Routes>,
      { route: '/schedule' },
    )
    await openJobMenu()
    fireEvent.click(await screen.findByTestId('cron-ask-in-main-chat'))
    const seeded = JSON.parse(sessionStorage.getItem(PREFILL_STORAGE_KEY) || '{}')
    expect(seeded).toMatchObject({ slotKey: 'chat-main-1', prompt: 'Change the job “Morning brief”: ' })
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/chat?sid=chat-main-1'))
    // Pre-filled only: nothing was sent and the job was not touched.
    expect(api.updateCron).not.toHaveBeenCalled()
  })
})
