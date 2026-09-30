/**
 * Screenshot harness for the Crew Members DM header pill's ACTIVITY LINE: the
 * one line under the crewmate's name that says what it is doing right now
 * (`pages/members/pillActivity.ts`). Against a REAL pod, not fixtures.
 *
 * What the evidence has to show, per frame:
 *   - resting: the line is present and reads "Idle · <time ago>" — text only,
 *     no dot or glyph, and the pill is the same height it will be while busy;
 *   - a live turn: the line follows the slot — "Thinking…" while the model
 *     reasons, the tool call's own purpose while a tool runs, "Writing…" once
 *     output streams — and a long purpose is cut at the cap with one ellipsis;
 *   - the line is not part of the button's accessible name.
 *
 * The live frames need the pod to actually run a turn, so the harness sends
 * one message through the real composer and photographs the header as the
 * `data-activity` attribute moves. A state that never shows up within the
 * budget is reported, not faked.
 *
 * Usage:
 *   kirocrew pod up <worktree> --json | tail -1 > "$KIROCREW_SCRATCH/pod-info.json"
 *   POD_INFO="$KIROCREW_SCRATCH/pod-info.json" \
 *     node scripts/capture-members-pill-activity.mjs ../temp-screenshots/members-pill-activity
 */
import { chromium } from 'playwright'
import { mkdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { check, podInfo, primeCrewPod } from './lib/crew-pod-harness.mjs'

const OUT = process.argv[2] || '../temp-screenshots/members-pill-activity'
const CREW = 'oncall'
/** Mirrors `PILL_ACTIVITY_MAX_CHARS`; the harness checks the cut, not the number. */
const PROMPT = 'Run the shell command `ls /` and then `cat /etc/hostname`, one tool call each, and describe each call in one long sentence before you make it. Then answer in two short sentences.'
const LIVE_BUDGET_MS = 90_000
mkdirSync(OUT, { recursive: true })

const { BASE, authed } = podInfo(readFileSync)

async function openMember(page) {
  await page.evaluate(async () => {
    localStorage.setItem('mc-crewmates-onboarded', '1')
    await fetch('/api/config/theme', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ crewmates_onboarded: true }) })
  })
  await page.goto(`${BASE}/members`, { waitUntil: 'domcontentloaded' })
  const notNow = page.getByTestId('meet-crewmates-not-now')
  if (await notNow.waitFor({ state: 'visible', timeout: 2500 }).then(() => true, () => false)) {
    await notNow.click()
    await notNow.waitFor({ state: 'hidden', timeout: 10000 })
  }
  const row = page.locator('#main-content li button', { hasText: CREW }).first()
  await row.waitFor({ state: 'visible', timeout: 20000 })
  await row.click()
  const pill = page.getByTestId('member-identity-pill')
  await pill.waitFor({ state: 'visible', timeout: 10000 })
  await page.waitForTimeout(600)
  check('no dialog is open over the page', (await page.getByRole('dialog').count()) === 0)
  return pill
}

async function shootHeader(page, path) {
  const box = await page.getByTestId('member-thread-header').boundingBox()
  await page.screenshot({ path, clip: { x: box.x, y: box.y, width: box.width, height: box.height + 72 } })
}

async function shootPill(page, path) {
  const box = await page.getByTestId('member-identity-pill').boundingBox()
  await page.screenshot({ path, clip: { x: box.x - 24, y: box.y - 12, width: box.width + 48, height: box.height + 24 } })
}

const activityOf = (line) => line.getAttribute('data-activity')

async function stills(browser, theme) {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 2 })
  const page = await context.newPage()
  await primeCrewPod(page, authed, CREW, theme)
  const pill = await openMember(page)
  const line = page.getByTestId('member-pill-activity')
  await page.mouse.move(5, 5)

  // 1: resting — line present, idle, text only, outside the accessible name.
  check(`[${theme}] the line sits inside the pill, under the title row`,
    await pill.evaluate(el => !!el.querySelector('[data-testid="member-pill-activity"]')
      && !el.querySelector('[data-testid="member-title-row"] [data-testid="member-pill-activity"]')))
  check(`[${theme}] resting line is idle text only`, (await activityOf(line)) === 'idle' && (await line.evaluate(el => el.children.length)) === 0, await line.innerText())
  check(`[${theme}] the line is out of the button's accessible name`, (await line.getAttribute('aria-hidden')) === 'true')
  const restHeight = (await pill.boundingBox()).height
  await page.screenshot({ path: join(OUT, `01-dm-idle-${theme}.png`) })
  await shootHeader(page, join(OUT, `01b-header-idle-${theme}.png`))
  await shootPill(page, join(OUT, `01c-pill-idle-${theme}.png`))

  // 2: a live turn — photograph each state the line passes through.
  const composer = page.getByRole('textbox').last()
  await composer.fill(PROMPT)
  await composer.press('Enter')
  const seen = new Map()
  const deadline = Date.now() + LIVE_BUDGET_MS
  let clampedShot = false
  while (Date.now() < deadline) {
    const kind = await activityOf(line)
    const text = (await line.innerText()).trim()
    if (kind && kind !== 'idle' && !seen.has(kind)) {
      seen.set(kind, text)
      await shootHeader(page, join(OUT, `02-header-${kind}-${theme}.png`))
      await shootPill(page, join(OUT, `02b-pill-${kind}-${theme}.png`))
      const h = (await pill.boundingBox()).height
      check(`[${theme}] pill height unchanged while ${kind} (${restHeight} -> ${h})`, Math.abs(h - restHeight) <= 1)
    }
    if (kind === 'tool' && text.endsWith('…') && !clampedShot) {
      clampedShot = true
      await shootPill(page, join(OUT, `02c-pill-tool-clamped-${theme}.png`))
      console.log('ok  ', `[${theme}] a long purpose is cut with an ellipsis:`, JSON.stringify(text))
    }
    if (seen.size >= 3 && kind === 'idle') break
    await page.waitForTimeout(120)
  }
  for (const [kind, text] of seen) console.log('ok  ', `[${theme}] saw ${kind}:`, JSON.stringify(text))
  for (const want of ['thinking', 'tool', 'writing']) {
    if (!seen.has(want)) console.log('warn', `[${theme}] never saw ${want} within ${LIVE_BUDGET_MS / 1000}s`)
  }
  check(`[${theme}] the live turn showed at least one working state`, seen.size > 0)
  await context.close()
}

async function main() {
  const browser = await chromium.launch()
  try {
    for (const theme of ['dark', 'light']) await stills(browser, theme)
  } finally {
    await browser.close()
  }
  console.log('wrote', OUT)
}

main().catch((err) => { console.error(err); process.exit(1) })
