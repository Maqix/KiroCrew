/**
 * Assistant-role system notices the backend injects into the feed -- status
 * reports (auto-compaction, session reload confirmations, the note that pasted
 * secrets were moved into the vault, the first run's graduation into the main
 * chat, the first run's stall and spent-allowance guardrails, the first week's
 * daily tip, the main chat's note that a handed-off chat finished, the note that
 * a home in the cloud waits for one click to sign in to Kiro), not real turns.
 *
 * Every scan that walks backward for "the assistant's last word" (follow-up
 * [OPTIONS:] derivation, the continuable-thread checks) must skip these, or a
 * trailing notice hides the buttons / state of the genuine turn before it.
 * One predicate shared by all scan sites so a new notice kind cannot be added
 * to one scan and forgotten in another.
 */
// Twin of SYSTEM_NOTICE_KINDS in src/kiro_crew/dashboard/system_notices.py;
// test_system_notice_kinds_backend_frontend_parity reads this literal.
const SYSTEM_NOTICE_KINDS: ReadonlySet<string> = new Set([
  'compaction',
  'session_reload',
  'secret_captured',
  'main_chat',
  'setup_stalled',
  'setup_quota',
  'first_week_tip',
  'handoff_done',
  'home_signin',
])

export function isSystemNoticeKind(kind: string | undefined): boolean {
  return !!kind && SYSTEM_NOTICE_KINDS.has(kind)
}

/**
 * A notice the gateway writes BEFORE the user row it describes. The captured-
 * secret note is the one: `dashboard/secret_capture.py` swaps a pasted credential
 * for a vault reference before the message is appended, and posts its "moved 1
 * pasted secret" row as it does, so the note lands directly above the prompt it
 * is about. It belongs to that prompt's exchange, not to the turn above it: the
 * transcript groups it as the prompt's lead-in (`groupDisplayItems`), and the
 * incoming prompt's lead-in is what pushes the pinned-prompt banner out
 * (`pushRowIdx` in utils/pinnedPrompt.ts), so the banner never parks over it.
 */
export function isPromptLeadInNotice(msg: { role: string; kind?: string; meta?: Record<string, unknown> }): boolean {
  return msg.role === 'assistant' && (msg.kind ?? (msg.meta?.kind as string | undefined)) === 'secret_captured'
}
