/**
 * Copy and small pure helpers for SetupCard: titles, result details, error
 * wording, and the "Use classic setup" target check.
 *
 * Every lookup here runs in RENDER position (called from a component body), so
 * `i18nT` re-resolves on a language switch; nothing is memoized across one.
 */
import type React from 'react'

import { i18nT } from '../../i18n/t'
import HomeMovedDetail from './HomeMovedDetail'
import type { SetupCard, SetupCardClassic, SetupCardKind, SetupCardStatus } from '../../api/setupCards'

/** Kinds whose card holds an unsaved draft (a secret field, import checkboxes). */
export const DRAFT_KINDS: ReadonlySet<SetupCardKind> = new Set<SetupCardKind>(['credential', 'channel', 'import'])

/** Terminal status → its word on the result line. */
export const STATUS_KEY = {
  committed: 'components.setupCard.status_committed',
  declined: 'components.setupCard.status_declined',
  failed: 'components.setupCard.status_failed',
  expired: 'components.setupCard.status_expired',
} as const satisfies Partial<Record<SetupCardStatus, string>>

/** Machine error codes the gateway returns → a sentence the owner can act on. */
const ERROR_KEY = {
  card_not_pending: 'components.setupCard.error_card_not_pending',
  card_hash_mismatch: 'components.setupCard.error_card_hash_mismatch',
  owner_required: 'components.setupCard.error_owner_required',
  card_not_found: 'components.setupCard.error_card_not_found',
  credential_empty: 'components.setupCard.error_credential_empty',
  service_not_installed: 'components.setupCard.error_service_not_installed',
  aws_not_signed_in: 'components.setupCard.error_aws_not_signed_in',
  // The home card's "Sign in to AWS" (dashboard/setup_aws_signin.py).
  aws_signin_remote: 'components.setupCard.error_aws_signin_remote',
  aws_signin_timeout: 'components.setupCard.error_aws_signin_timeout',
  aws_signin_failed: 'components.setupCard.error_aws_signin_failed',
  aws_signin_busy: 'components.setupCard.error_aws_signin_busy',
  aws_signin_not_needed: 'components.setupCard.error_aws_signin_not_needed',
  aws_cli_missing: 'components.setupCard.error_aws_cli_missing',
  aws_cli_too_old: 'components.setupCard.error_aws_cli_too_old',
  aws_signin_profile_has_keys: 'components.setupCard.error_aws_signin_profile_has_keys',
  launch_already_running: 'components.setupCard.error_launch_already_running',
  // The home's build and its Kiro sign-in (setup_flow._commit_home, _sign_home_in).
  home_identity_region_unknown: 'components.setupCard.error_home_identity_region_unknown',
  home_signin_unavailable: 'components.setupCard.error_home_signin_unavailable',
  launch_job_not_found: 'components.setupCard.error_home_signin_unavailable',
  launch_has_no_instance: 'components.setupCard.error_home_signin_unavailable',
  login_target_unreadable: 'components.setupCard.error_login_target_unreadable',
  // The home's size and the account it is built in (setup_flow._chosen_home_size).
  home_size_not_offered: 'components.setupCard.error_home_size_not_offered',
  home_size_needs_paid_plan: 'components.setupCard.error_home_size_needs_paid_plan',
  home_vcpu_quota_low: 'components.setupCard.error_home_vcpu_quota_low',
  home_spend_limit: 'components.setupCard.error_home_spend_limit',
  // The live move-in's refusals (dashboard/setup_move_in.py). Each leaves the
  // card pending; the failed step's own detail on the card keeps the server's
  // specifics (the tunnel error, the home's refusal code).
  move_in_restarting: 'components.setupCard.error_move_in_restarting',
  move_in_instances_off: 'components.setupCard.error_move_in_instances_off',
  move_in_home_not_registered: 'components.setupCard.error_move_in_home_not_registered',
  move_in_home_unsupported: 'components.setupCard.error_move_in_home_unsupported',
  move_in_unreachable: 'components.setupCard.error_move_in_unreachable',
  move_in_jobs_busy: 'components.setupCard.error_move_in_jobs_busy',
  move_in_carry_failed: 'components.setupCard.error_move_in_carry_failed',
  move_in_carry_refused: 'components.setupCard.error_move_in_carry_refused',
  move_in_chat_missing: 'components.setupCard.error_move_in_chat_missing',
  move_in_chat_not_persistent: 'components.setupCard.error_move_in_chat_not_persistent',
  move_in_chat_busy: 'components.setupCard.error_move_in_chat_busy',
  move_in_chat_failed: 'components.setupCard.error_move_in_chat_failed',
  move_in_failed: 'components.setupCard.error_move_in_failed',
  move_in_interrupted: 'components.setupCard.error_move_in_interrupted',
  channel_token_empty: 'components.setupCard.error_channel_token_empty',
  channel_token_invalid: 'components.setupCard.error_channel_token_invalid',
  channel_token_rejected: 'components.setupCard.error_channel_token_rejected',
  pair_attempts: 'components.setupCard.error_pair_attempts',
} as const

/**
 * The words for a failed decide or a card's own `error`: a known code gets its
 * translated sentence, anything else keeps the server's human text, and a bare
 * failure with neither gets the generic retry line.
 */
export function errorText(code: string | undefined, serverMessage: string): string {
  const key = code ? ERROR_KEY[code as keyof typeof ERROR_KEY] : undefined
  if (key) return i18nT(key)
  return serverMessage || i18nT('components.setupCard.error_generic')
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/**
 * The first run's "Where should your crew live?" step: a home card the gateway
 * shows on its own (`payload.offer`), while it is still the question, i.e.
 * before anything is built. Once a build starts it is the home itself.
 */
export function isHomeOffer(card: SetupCard): boolean {
  if (card.kind !== 'home' || card.payload?.offer !== true) return false
  const o = card.outcome ?? {}
  return !o.job_id && o.ready !== true && o.needs_signin !== true && o.moved !== true
}

/** The word beside a decided card's title; a declined home step keeps the crew here. */
export function resultStatusKey(card: SetupCard): string | undefined {
  if (card.status === 'declined' && isHomeOffer(card)) return 'components.setupCard.home_offer_declined'
  return STATUS_KEY[card.status as keyof typeof STATUS_KEY]
}

/** The card's heading, per kind, from its payload. */
export function cardTitle(card: SetupCard): string {
  const p = card.payload ?? {}
  switch (card.kind) {
    case 'privacy':
      // The SAME title the Privacy chapter shows: a disclosure is not
      // paraphrased between its two surfaces.
      return i18nT('components.privacyChapter.title')
    case 'profile':
      return i18nT('components.setupCard.title_profile')
    case 'soul':
      return i18nT('components.setupCard.title_soul', { file: soulFileName(p.file) })
    case 'import':
      return i18nT('components.setupCard.title_import')
    case 'connect': {
      const provider = (p.provider ?? {}) as { name?: unknown }
      return i18nT('components.setupCard.title_connect', { name: str(provider.name) })
    }
    case 'credential':
      return i18nT('components.setupCard.title_credential', { name: str(p.name) })
    case 'channel':
      return i18nT('components.setupCard.title_channel', { label: str(p.label) || str(p.channel) })
    case 'cron':
      return i18nT('components.setupCard.title_cron')
    case 'service':
      return i18nT('components.setupCard.title_service')
    case 'home':
      return isHomeOffer(card)
        ? i18nT('components.setupCard.title_home_offer')
        : i18nT('components.setupCard.title_home')
    default:
      return i18nT('components.setupCard.title_generic')
  }
}

/** The extension of the two persona files (SOUL.md, USER.md); a file name, not copy. */
const PERSONA_FILE_EXT = '.md'

/** `SOUL` / `USER` → the file name the user edits. Anything else reads as SOUL.md. */
export function soulFileName(file: unknown): string {
  const stem = file === 'USER' ? 'USER' : 'SOUL'
  return stem + PERSONA_FILE_EXT
}

/** The optional second line under a committed card's result. */
export function committedDetail(card: SetupCard): React.ReactNode {
  const o = card.outcome ?? {}
  if (card.kind === 'import') {
    const imported = typeof o.imported_count === 'number' ? o.imported_count : null
    const jobs = typeof o.jobs_added_disabled === 'number' ? o.jobs_added_disabled : 0
    if (imported === null && !jobs) return null
    return (
      <div className="text-[12px] text-muted pl-5" data-testid="setup-card-result-detail">
        {imported !== null && <div>{i18nT('components.setupCard.import_result', { count: imported })}</div>}
        {jobs > 0 && <div>{i18nT('components.setupCard.import_jobs_result', { count: jobs })}</div>}
      </div>
    )
  }
  // A live move-in: what moved, what stayed, and what to set up again there.
  if (card.kind === 'home' && o.moved === true && o.simulated !== true) {
    return <HomeMovedDetail outcome={o} />
  }
  if (card.kind === 'home' && o.moved === true) {
    return (
      <div className="text-[12px] text-muted pl-5" data-testid="setup-card-result-detail">
        {i18nT('components.setupCard.home_result_simulated')}
      </div>
    )
  }
  if (card.kind === 'channel' && o.paired === true) {
    // `username` is the sender's @handle as the gateway narrowed it; an account
    // without one pairs just the same.
    const username = str(o.username)
    return (
      <div className="text-[12px] text-muted pl-5 min-w-0 break-words" data-testid="setup-card-result-detail">
        {username
          ? i18nT('components.setupCard.channel_paired', { username })
          : i18nT('components.setupCard.channel_paired_account')}
      </div>
    )
  }
  if (card.kind === 'credential' && typeof o.ref === 'string' && o.ref) {
    return (
      <div className="text-[12px] text-muted pl-5 min-w-0 break-words" data-testid="setup-card-result-detail">
        {i18nT('components.setupCard.credential_result')}{' '}
        <code className="font-mono text-[12px] text-text" translate="no">{o.ref}</code>
      </div>
    )
  }
  return null
}

/** A validated "Use classic setup" target, or null to hide the affordance. */
export function classicAction(classic: SetupCardClassic | null | undefined): SetupCardClassic | null {
  if (!classic || typeof classic.target !== 'string') return null
  if (classic.kind === 'route') {
    // Same-app paths only: the target is gateway-authored, but a navigation
    // this card performs must never leave the dashboard (`//host` is a
    // protocol-relative URL, a backslash is one on some parsers).
    const t = classic.target
    return t.startsWith('/') && !t.startsWith('//') && !t.includes('\\') ? classic : null
  }
  if (classic.kind === 'event') {
    // The dashboard's own window events (`mc-start-import`, ...), nothing else.
    return /^mc-[a-z0-9-]+$/.test(classic.target) ? classic : null
  }
  return null
}

/** Only an http(s) consent URL becomes a link; anything else is not rendered. */
export function safeConsentUrl(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null
  try {
    const u = new URL(raw)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null
  } catch {
    return null
  }
}
