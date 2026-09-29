/**
 * The dashboard half of the setup-action registry: one entry per card kind the
 * gateway registers (`src/kiro_crew/setup_actions/`). SetupCard reads a kind's
 * body, title and flags from here instead of branching on the kind.
 *
 * `test/test_setup_action_parity.py` parses the keys of `SETUP_CARD_KINDS` and
 * `SETUP_CARD_TITLE_KEY` and fails when a gateway kind has no entry here, an
 * entry here has no gateway kind, or a title key is missing from the catalog.
 * A card whose kind this build has no entry for draws `FallbackBody`: the kind
 * works, minimally, before the dashboard catches up.
 */
import type React from 'react'
import type { ComponentType } from 'react'

import { i18nT } from '../../i18n/t'
import type { SetupCard, SetupCardKind } from '../../api/setupCards'
import {
  ChannelBody,
  ConnectBody,
  CredentialBody,
  CronBody,
  FallbackBody,
  HomeBody,
  ImportBody,
  PrivacyBody,
  ProfileBody,
  ServiceBody,
  SoulBody,
  type SetupBodyProps,
} from './SetupCardBodies'
import {
  channelResultDetail,
  credentialResultDetail,
  homeResultDetail,
  importResultDetail,
  isHomeOffer,
  soulFileName,
} from './setupCardCopy'

export interface SetupCardKindEntry {
  /** What the card's payload shows, then its actions, handed to the footer. */
  Body: ComponentType<SetupBodyProps>
  /** Interpolation values for the kind's title (`SETUP_CARD_TITLE_KEY`). */
  titleValues?: (card: SetupCard) => Record<string, string>
  /** A heading that replaces the kind's own for this card, or null to keep it. */
  titleOverride?: (card: SetupCard) => string | null
  /** The optional second line under a committed card's result. */
  resultDetail?: (card: SetupCard) => React.ReactNode
  /** The card holds an unsaved draft (a secret field, import checkboxes), which a
   *  hand-off's navigation would destroy, so its error notice offers none. */
  draft?: boolean
  /** A commit settles a boot flag server-side (`dashboard.privacy_acked`, the
   *  import stage), so the tab re-reads the boot flags after one. */
  refreshesBoot?: boolean
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/**
 * Each kind's title, by kind: a flat map of full literal keys indexed inline at
 * the `i18nT()` call, the shape `scripts/check-i18n-keys.mjs` resolves statically.
 */
export const SETUP_CARD_TITLE_KEY = {
  // The SAME title the Privacy chapter shows: a disclosure is not paraphrased
  // between its two surfaces.
  privacy: 'components.privacyChapter.title',
  profile: 'components.setupCard.title_profile',
  soul: 'components.setupCard.title_soul',
  import: 'components.setupCard.title_import',
  connect: 'components.setupCard.title_connect',
  credential: 'components.setupCard.title_credential',
  channel: 'components.setupCard.title_channel',
  cron: 'components.setupCard.title_cron',
  service: 'components.setupCard.title_service',
  home: 'components.setupCard.title_home',
} as const satisfies Record<SetupCardKind, string>

export const SETUP_CARD_KINDS = {
  privacy: { Body: PrivacyBody, refreshesBoot: true },
  profile: { Body: ProfileBody },
  soul: { Body: SoulBody, titleValues: card => ({ file: soulFileName(card.payload?.file) }) },
  import: { Body: ImportBody, resultDetail: importResultDetail, draft: true, refreshesBoot: true },
  connect: {
    Body: ConnectBody,
    titleValues: card => ({ name: str(((card.payload?.provider ?? {}) as { name?: unknown }).name) }),
  },
  credential: {
    Body: CredentialBody,
    titleValues: card => ({ name: str(card.payload?.name) }),
    resultDetail: credentialResultDetail,
    draft: true,
  },
  channel: {
    Body: ChannelBody,
    titleValues: card => ({ label: str(card.payload?.label) || str(card.payload?.channel) }),
    resultDetail: channelResultDetail,
    draft: true,
  },
  cron: { Body: CronBody },
  service: { Body: ServiceBody },
  home: {
    Body: HomeBody,
    // The first run's own "Where should your crew live?" step, while it is still the question.
    titleOverride: card => (isHomeOffer(card) ? i18nT('components.setupCard.title_home_offer') : null),
    resultDetail: homeResultDetail,
  },
} satisfies Record<SetupCardKind, SetupCardKindEntry>

/** The entry for *kind*, or null for a kind this build does not know. */
export function setupCardEntry(kind: string): SetupCardKindEntry | null {
  return Object.prototype.hasOwnProperty.call(SETUP_CARD_KINDS, kind)
    ? SETUP_CARD_KINDS[kind as SetupCardKind]
    : null
}

/** The card's heading, from its kind's entry; a kind this build does not know gets the generic one. */
export function cardTitle(card: SetupCard): string {
  const entry = setupCardEntry(card.kind)
  if (!entry) return i18nT('components.setupCard.title_generic')
  return entry.titleOverride?.(card) ?? i18nT(SETUP_CARD_TITLE_KEY[card.kind], entry.titleValues?.(card))
}

/** The optional second line under a committed card's result. */
export function committedDetail(card: SetupCard): React.ReactNode {
  return setupCardEntry(card.kind)?.resultDetail?.(card) ?? null
}

/** The body for the card's kind, or the fallback for a kind this build does not know. */
export function SetupCardBody(props: SetupBodyProps) {
  const Body = setupCardEntry(props.card.kind)?.Body ?? FallbackBody
  return <Body {...props} />
}
