/**
 * The second line of the crewmate DM header's identity pill: what the crewmate
 * is doing RIGHT NOW, in one short line under its name.
 *
 * The line is always present (the pill never changes height), and it is text
 * only — no dot, no glyph. Presence already has a home (the avatar's own
 * working state), so a second marker here would say the same thing twice.
 *
 * Every reading comes from state the dashboard already holds for the slot:
 *
 *   `SlotState`          | what the line says
 *   ---------------------|---------------------------------------------------
 *   `tool_running`       | the newest tool call's own purpose line (the
 *                        | model-written `__tool_use_purpose`), or the tool's
 *                        | name when the call carried none; once that call has
 *                        | returned, "Thinking…" — the model is reading it
 *   `streaming`          | "Thinking…" while the newest row is reasoning,
 *                        | "Writing…" once visible output flows
 *   `compacting`         | "Compacting…"
 *   `stopping`           | "Stopping…"
 *   `idle` but running   | "Thinking…" when the transcript ends in a reasoning
 *                        | placeholder (the turn opened, nothing streamed yet),
 *                        | else "Working" (the slots frame says busy before any
 *                        | finer reading arrived), or the delegated variant
 *                        | when only sub-agents are running
 *   `idle`               | "Idle · <time ago>", or just "Idle" with no history
 *
 * Purpose text is clamped to `PILL_ACTIVITY_MAX_CHARS` code points with an
 * ellipsis: the pill sits centred over the transcript and a long purpose
 * sentence would push it to the header's full width. The fixed labels never
 * reach the cap, so only the tool line is clamped.
 */
import type { SlotState } from '../../store/chat/state'

/** Longest activity line, in code points, before it is cut with an ellipsis. */
export const PILL_ACTIVITY_MAX_CHARS = 40

/** The kinds the line can name. `tool` is the only one carrying free text. */
export type PillActivityKind =
  | 'tool'
  | 'thinking'
  | 'writing'
  | 'compacting'
  | 'stopping'
  | 'working'
  | 'delegated'
  | 'idle'

export interface PillActivity {
  kind: PillActivityKind
  /** Present on `tool` only: the clamped purpose (or tool name). */
  text?: string
}

export interface PillActivityInput {
  /** The slot's live run state (`selectSlotStreamState`). */
  streamState: SlotState
  /** Role of the newest transcript row, '' when the transcript is empty. */
  lastRole: string
  /** Purpose line of the newest tool-log entry, '' when it carried none. */
  lastToolPurpose: string
  /** Name of the newest tool-log entry's tool, '' when the log is empty. */
  lastToolName: string
  /** The newest tool-log entry already carries its output. */
  lastToolDone: boolean
  /** The slots frame's own busy flag (main turn OR sub-agents). */
  running: boolean
  /** Sub-agents are running while the main turn is not. */
  delegatedOnly: boolean
}

/**
 * Cut `text` to at most `max` code points, ending in a single ellipsis when
 * anything was dropped. Code points, not UTF-16 units, so a CJK or emoji
 * purpose is never split through a surrogate pair. Whitespace is collapsed
 * first: a purpose written over two lines is one line here.
 */
export function clampActivityText(text: string, max: number = PILL_ACTIVITY_MAX_CHARS): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  const points = Array.from(flat)
  if (points.length <= max) return flat
  return points.slice(0, Math.max(0, max - 1)).join('').trimEnd() + '…'
}

/** Resolve the one line the pill shows from the slot's live readings. */
export function resolvePillActivity(input: PillActivityInput): PillActivity {
  const { streamState, lastRole, lastToolPurpose, lastToolName, lastToolDone, running, delegatedOnly } = input
  switch (streamState) {
    case 'tool_running': {
      if (lastToolDone) return { kind: 'thinking' }
      const raw = lastToolPurpose || lastToolName
      return raw ? { kind: 'tool', text: clampActivityText(raw) } : { kind: 'working' }
    }
    case 'streaming':
      return { kind: lastRole === 'thinking' ? 'thinking' : 'writing' }
    case 'compacting':
      return { kind: 'compacting' }
    case 'stopping':
      return { kind: 'stopping' }
    case 'idle':
      break
  }
  // A turn that has opened but not yet streamed: the transcript's trailing
  // reasoning placeholder is the only sign, and it means "thinking".
  if (running && lastRole === 'thinking') return { kind: 'thinking' }
  if (running) return { kind: delegatedOnly ? 'delegated' : 'working' }
  return { kind: 'idle' }
}
