/**
 * A page load that opens on the one-chat first run starts with the nav rail
 * collapsed to its icons. The collapse is a starting layout, not a stored
 * choice: `mc-nav` stays unwritten until the user toggles the rail, and from
 * then on that choice wins. Any other landing keeps today's expanded rail.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import type { ReactNode } from 'react'

const FIRST_RUN = 'chat-1-1790000000'

/** Mutable so a test can set the flags before render. */
const themeState = {
  colorTheme: 'kiro',
  theme: 'dark' as const,
  mode: 'dark' as const,
  onboarded: false,
  importOnboarded: true,
  privacyAcked: true,
  crewmatesOnboarded: true,
  themeBootReady: true,
  firstRunSlot: FIRST_RUN as string | null,
  mainSlot: null as string | null,
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
vi.mock('../pages/SchedulePage', () => ({ default: () => <div data-testid="schedule-page">SchedulePage</div> }))

import { renderWithProviders, createTestStore } from './helpers'
import { setActiveSlot } from '../store/chatSlice'
import { __resetFirstRunLayout } from '../hooks/useFirstRunLayout'
import App from '../App'

/** The brand button is the rail's collapse toggle; its name says what a click does. */
const railCollapsed = () => screen.queryByRole('button', { name: 'Expand sidebar' }) !== null
const railExpanded = () => screen.queryByRole('button', { name: 'Collapse sidebar' }) !== null

/** Renders the shell with `slot` as the chat on screen, as ChatPage's landing would set it. */
function openOn(slot: string | null, route = `/chat?sid=${slot ?? ''}`) {
  const store = createTestStore()
  if (slot) store.dispatch(setActiveSlot(slot))
  return { store, ...renderWithProviders(<App />, { route, store }) }
}

const settle = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

beforeEach(() => {
  localStorage.clear()
  __resetFirstRunLayout()
  themeState.themeBootReady = true
  themeState.onboarded = false
  themeState.firstRunSlot = FIRST_RUN
  themeState.mainSlot = null
})

describe('the first-run chat opens with the nav rail collapsed', () => {
  it('collapses the rail without writing the preference', async () => {
    openOn(FIRST_RUN)
    await screen.findByTestId('chat-page')
    await settle()
    expect(railCollapsed()).toBe(true)
    expect(localStorage.getItem('mc-nav')).toBeNull()
  })

  it('persists the user\'s expand and lets it win on the next load', async () => {
    const first = openOn(FIRST_RUN)
    await screen.findByTestId('chat-page')
    await settle()
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(railExpanded()).toBe(true)
    expect(localStorage.getItem('mc-nav')).toBe('0')

    // A reload is a new page: the verdict is decided afresh, the stored choice holds.
    first.unmount()
    __resetFirstRunLayout()
    openOn(FIRST_RUN)
    await screen.findByTestId('chat-page')
    await settle()
    expect(railExpanded()).toBe(true)
  })

  it('keeps a rail the user collapsed before', async () => {
    localStorage.setItem('mc-nav', '1')
    openOn(FIRST_RUN)
    await screen.findByTestId('chat-page')
    await settle()
    expect(railCollapsed()).toBe(true)
    expect(localStorage.getItem('mc-nav')).toBe('1')
  })
})

describe('every other landing keeps today\'s expanded rail', () => {
  it('a session that is not the first-run chat', async () => {
    openOn('chat-2-1790000100')
    await screen.findByTestId('chat-page')
    await settle()
    expect(railExpanded()).toBe(true)
  })

  it('an existing install with no first-run chat', async () => {
    themeState.firstRunSlot = null
    themeState.onboarded = true
    openOn('chat-2-1790000100')
    await screen.findByTestId('chat-page')
    await settle()
    expect(railExpanded()).toBe(true)
  })

  it('the first-run chat after it graduated into the main chat', async () => {
    themeState.mainSlot = FIRST_RUN
    openOn(FIRST_RUN)
    await screen.findByTestId('chat-page')
    await settle()
    expect(railExpanded()).toBe(true)
  })

  it('a page load that opened elsewhere, even once the first-run chat is shown', async () => {
    const { store } = openOn(null, '/schedule')
    await screen.findByTestId('schedule-page')
    await settle()
    act(() => { store.dispatch(setActiveSlot(FIRST_RUN)) })
    await settle()
    expect(railExpanded()).toBe(true)
  })
})
