/**
 * One-chat first run owns onboarding while its session exists.
 *
 * `useTheme().firstRunSlot` (from `/api/theme/boot`'s `first_run_slot`) set
 * means the first-run chat is doing the chapters' job, so none of the four
 * first-run chapters opens on its own — not Import, not Privacy, not the tour.
 * Their manual entry points must keep working unchanged: `mc-start-import`
 * (Settings → Import, and a setup card's "Use classic setup") still opens the
 * Import chapter.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'

/** Mutable so a test can set the flags before render. */
const themeState = {
  colorTheme: 'kiro',
  theme: 'dark' as const,
  mode: 'dark' as const,
  onboarded: false,
  importOnboarded: false,
  privacyAcked: false,
  crewmatesOnboarded: true,
  themeBootReady: true,
  firstRunSlot: null as string | null,
  themes: [],
  markOnboarded: vi.fn(),
  markImportOnboarded: vi.fn(),
  markPrivacyAcked: vi.fn(),
  markCrewmatesOnboarded: vi.fn(),
  setColorTheme: vi.fn(),
  setMode: vi.fn(),
}

vi.mock('../hooks/useTheme', () => ({
  useTheme: () => themeState,
  ThemeProvider: ({ children }: { children: ReactNode }) => children,
}))

vi.mock('../pages/ChatPage', () => ({ default: () => <div data-testid="chat-page">ChatPage</div> }))
vi.mock('../pages/SystemPage', () => ({ default: () => null }))
vi.mock('../pages/ProjectsPage', () => ({ default: () => null }))
vi.mock('../pages/LogsPage', () => ({ default: () => null }))
vi.mock('../pages/KiroCrewAgentsPage', () => ({ default: () => null }))

import { renderWithProviders } from './helpers'
import App from '../App'

/** The Import chapter renders nothing at all while closed. */
const importChapterOpen = () => screen.queryByText(/bring your crew with you/i) !== null
/** The Privacy chapter's heading. */
const privacyChapterOpen = () => screen.queryByRole('heading', { name: 'Privacy' }) !== null

beforeEach(() => {
  themeState.themeBootReady = true
  themeState.onboarded = false
  themeState.importOnboarded = false
  themeState.privacyAcked = false
  themeState.firstRunSlot = null
})

describe('first-run chapters under a one-chat first-run session', () => {
  it('control: without a first-run slot the Import chapter opens on its own', async () => {
    renderWithProviders(<App />, { route: '/chat' })
    await waitFor(() => expect(importChapterOpen()).toBe(true))
  })

  it('does not auto-open the Import chapter while the first-run chat owns onboarding', async () => {
    themeState.firstRunSlot = 'chat-1-1790000000'
    renderWithProviders(<App />, { route: '/chat' })
    await screen.findByTestId('chat-page')
    // Settle the chapter effect before asserting a negative.
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(importChapterOpen()).toBe(false)
  })

  it('does not auto-open the Privacy chapter either', async () => {
    themeState.firstRunSlot = 'chat-1-1790000000'
    themeState.importOnboarded = true
    renderWithProviders(<App />, { route: '/chat' })
    await screen.findByTestId('chat-page')
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(privacyChapterOpen()).toBe(false)
    expect(importChapterOpen()).toBe(false)
  })

  it('still opens the Import chapter on mc-start-import (Settings → Import, classic setup)', async () => {
    themeState.firstRunSlot = 'chat-1-1790000000'
    renderWithProviders(<App />, { route: '/chat' })
    await screen.findByTestId('chat-page')
    expect(importChapterOpen()).toBe(false)
    act(() => {
      window.dispatchEvent(new CustomEvent('mc-start-import', { detail: { continueOnboarding: true } }))
    })
    await waitFor(() => expect(importChapterOpen()).toBe(true))
  })
})
