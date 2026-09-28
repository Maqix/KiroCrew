/**
 * `useTheme().firstRunSlot` mirrors `/api/theme/boot`'s `first_run_slot` (the
 * one-chat first-run session), is null when the boot payload carries none, and
 * is latched: a boot re-read that stops carrying it does not flip it back, so
 * the chapters cannot open over the conversation that replaced them.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import type { ReactNode } from 'react'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { server } from '../../integration/mocks/server'
import { useTheme, ThemeProvider } from '../hooks/useTheme'

function renderThemeHook() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <ThemeProvider>{children}</ThemeProvider>
    </QueryClientProvider>
  )
  return { qc, ...renderHook(() => useTheme(), { wrapper }) }
}

function serveBoot(firstRunSlot: string | null, mainSlot: string | null = null) {
  const boot = { value: firstRunSlot, main: mainSlot }
  server.use(
    http.get('/api/theme/boot', () => HttpResponse.json({
      mode: 'dark', color: 'kiro', onboarded: false, import_onboarded: false,
      privacy_acked: false, crewmates_onboarded: false, first_run_slot: boot.value, main_slot: boot.main,
    })),
    http.get('/api/themes', () => HttpResponse.json({ themes: [] })),
  )
  return boot
}

beforeEach(() => { localStorage.clear() })

describe('useTheme firstRunSlot', () => {
  it('exposes the boot payload’s first-run slot once boot is ready', async () => {
    serveBoot('chat-1-1790000000')
    const { result } = renderThemeHook()
    await waitFor(() => expect(result.current.themeBootReady).toBe(true))
    expect(result.current.firstRunSlot).toBe('chat-1-1790000000')
  })

  it('is null when the gateway reports no first-run session', async () => {
    serveBoot(null)
    const { result } = renderThemeHook()
    await waitFor(() => expect(result.current.themeBootReady).toBe(true))
    expect(result.current.firstRunSlot).toBeNull()
  })

  it('stays latched when a later boot read no longer carries it', async () => {
    const boot = serveBoot('chat-1-1790000000')
    const { result, qc } = renderThemeHook()
    await waitFor(() => expect(result.current.firstRunSlot).toBe('chat-1-1790000000'))
    boot.value = null
    await act(async () => { await qc.refetchQueries({ queryKey: ['theme-boot'] }) })
    expect(result.current.firstRunSlot).toBe('chat-1-1790000000')
  })
})

describe('useTheme mainSlot', () => {
  it('is null until the first run graduates, then picks the main chat up on a boot re-read', async () => {
    const boot = serveBoot('chat-1-1790000000', null)
    const { result, qc } = renderThemeHook()
    await waitFor(() => expect(result.current.themeBootReady).toBe(true))
    expect(result.current.mainSlot).toBeNull()
    // Graduation happens mid-session; the gateway now reports the main chat.
    boot.main = 'chat-1-1790000000'
    await act(async () => { await qc.refetchQueries({ queryKey: ['theme-boot'] }) })
    await waitFor(() => expect(result.current.mainSlot).toBe('chat-1-1790000000'))
    // …and a later read that drops it does not unset it.
    boot.main = null
    await act(async () => { await qc.refetchQueries({ queryKey: ['theme-boot'] }) })
    expect(result.current.mainSlot).toBe('chat-1-1790000000')
  })
})
