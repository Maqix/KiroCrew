import { describe, expect, it } from 'vitest'
import { PILL_ACTIVITY_MAX_CHARS, clampActivityText, resolvePillActivity, type PillActivityInput } from './pillActivity'

const base: PillActivityInput = {
  streamState: 'idle',
  lastRole: '',
  lastToolPurpose: '',
  lastToolName: '',
  lastToolDone: false,
  running: false,
  delegatedOnly: false,
}

describe('clampActivityText', () => {
  it('returns short text unchanged, whitespace collapsed', () => {
    expect(clampActivityText('  read   the\nfile ')).toBe('read the file')
  })

  it('cuts to the cap in code points and ends in one ellipsis', () => {
    const long = 'x'.repeat(PILL_ACTIVITY_MAX_CHARS + 10)
    const out = clampActivityText(long)
    expect(Array.from(out)).toHaveLength(PILL_ACTIVITY_MAX_CHARS)
    expect(out.endsWith('…')).toBe(true)
    expect(out.split('…')).toHaveLength(2)
  })

  it('does not cut through a surrogate pair or count one as two', () => {
    const emoji = '🔧'.repeat(PILL_ACTIVITY_MAX_CHARS)
    expect(clampActivityText(emoji)).toBe(emoji)
    const over = '🔧'.repeat(PILL_ACTIVITY_MAX_CHARS + 1)
    const cut = clampActivityText(over)
    expect(cut).toBe('🔧'.repeat(PILL_ACTIVITY_MAX_CHARS - 1) + '…')
  })

  it('a text exactly at the cap is not cut', () => {
    const exact = 'y'.repeat(PILL_ACTIVITY_MAX_CHARS)
    expect(clampActivityText(exact)).toBe(exact)
  })
})

describe('resolvePillActivity', () => {
  it('a running tool call shows its own purpose line, clamped', () => {
    const purpose = 'p'.repeat(PILL_ACTIVITY_MAX_CHARS + 5)
    const out = resolvePillActivity({ ...base, streamState: 'tool_running', lastToolPurpose: purpose, lastToolName: 'shell', running: true })
    expect(out.kind).toBe('tool')
    expect(out.text).toBe('p'.repeat(PILL_ACTIVITY_MAX_CHARS - 1) + '…')
  })

  it('a tool call with no purpose falls back to the tool name', () => {
    expect(resolvePillActivity({ ...base, streamState: 'tool_running', lastToolName: 'fs_read', running: true }))
      .toEqual({ kind: 'tool', text: 'fs_read' })
  })

  it('a returned tool call reads as thinking — the model is reading the result', () => {
    expect(resolvePillActivity({ ...base, streamState: 'tool_running', lastToolPurpose: 'x', lastToolName: 'shell', lastToolDone: true, running: true }))
      .toEqual({ kind: 'thinking' })
  })

  it('tool_running with an empty log is plain working', () => {
    expect(resolvePillActivity({ ...base, streamState: 'tool_running', running: true })).toEqual({ kind: 'working' })
  })

  it('streaming is thinking while the newest row is reasoning, writing otherwise', () => {
    expect(resolvePillActivity({ ...base, streamState: 'streaming', lastRole: 'thinking', running: true })).toEqual({ kind: 'thinking' })
    expect(resolvePillActivity({ ...base, streamState: 'streaming', lastRole: 'streaming', running: true })).toEqual({ kind: 'writing' })
  })

  it('compacting and stopping name themselves', () => {
    expect(resolvePillActivity({ ...base, streamState: 'compacting', running: true })).toEqual({ kind: 'compacting' })
    expect(resolvePillActivity({ ...base, streamState: 'stopping', running: true })).toEqual({ kind: 'stopping' })
  })

  it('a turn that opened but has not streamed is thinking', () => {
    expect(resolvePillActivity({ ...base, running: true, lastRole: 'thinking' })).toEqual({ kind: 'thinking' })
  })

  it('busy with no finer reading is working, or delegated when only sub-agents run', () => {
    expect(resolvePillActivity({ ...base, running: true })).toEqual({ kind: 'working' })
    expect(resolvePillActivity({ ...base, running: true, delegatedOnly: true })).toEqual({ kind: 'delegated' })
  })

  it('nothing running is idle, whatever the transcript ends in', () => {
    expect(resolvePillActivity({ ...base, lastRole: 'thinking' })).toEqual({ kind: 'idle' })
    expect(resolvePillActivity({ ...base, lastToolPurpose: 'old call', lastToolName: 'shell', lastToolDone: true })).toEqual({ kind: 'idle' })
  })
})
