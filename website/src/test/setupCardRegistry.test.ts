/**
 * The dashboard's setup-card registry covers every kind, and every kind's title
 * resolves to catalog copy rather than to its own key. Which kinds the gateway
 * registers is pinned from the backend side (`test/test_setup_action_parity.py`).
 */
import { describe, it, expect } from 'vitest'

import { i18nT } from '../i18n/t'
import { SETUP_CARD_KINDS, SETUP_CARD_TITLE_KEY, setupCardEntry } from '../components/setup/setupCardRegistry'

describe('setupCardRegistry', () => {
  it('titles every kind it draws, with a key the catalog holds', () => {
    expect(Object.keys(SETUP_CARD_TITLE_KEY).sort()).toEqual(Object.keys(SETUP_CARD_KINDS).sort())
    for (const key of Object.values(SETUP_CARD_TITLE_KEY)) {
      expect(i18nT(key), key).not.toBe(key)
    }
  })

  it('knows no kind it was not given, including the object prototype’s own names', () => {
    for (const kind of ['teleport', 'constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      expect(setupCardEntry(kind), kind).toBeNull()
    }
    expect(setupCardEntry('cron')).toBe(SETUP_CARD_KINDS.cron)
  })
})
