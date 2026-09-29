/**
 * Screenshots for the Crewmate DM's follow-to-bottom on messages that land
 * while nothing is running, captured on the Crewmates page (the ChatPane host
 * the defect was reported against). Every frame asserts its state before it is
 * written, so a frame IS the evidence of the behaviour it is named for:
 *   01-idle-reply-follows    the reader is at the end and has wheeled there (an
 *                            input that moved nothing); a crewmate's COMPLETE
 *                            reply lands with the turn over -- the transcript
 *                            follows it, no jump pill
 *   02-streaming-follows     a live reply streams delta by delta -- followed
 *   03-reading-kept          the reader scrolled up to read; a reply lands --
 *                            they are left where they are, the pill offers the
 *                            way back
 *   04-return-follows        the reader scrolls back down to the end by hand;
 *                            the next reply follows again
 *   05-send-follows          the reader scrolls up and SENDS from there -- the
 *                            send lands them at the bottom on their own bubble
 *   01-idle-reply-follows-light   light-theme parity of frame 01
 *
 * Usage:
 *   npx vite --host 127.0.0.1 --port 6831 --strictPort   # in another shell
 *   node scripts/capture-chatpane-idle-follow.mjs http://127.0.0.1:6831 ../temp-screenshots/chatpane-idle-follow
 *
 * Pass a third argument `video` to also record the whole dark sequence as one
 * WebM under the output directory (converted by the caller if a GIF is wanted).
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:6831'
const OUT = process.argv[3] || '../temp-screenshots/chatpane-idle-follow'
const VIDEO = process.argv[4] === 'video'
mkdirSync(OUT, { recursive: true })

const MEMBERS = [
  { name: 'radar', slug: 'radar', bound: true, slot_key: 'member-radar', running: false, kiro_agent: 'kirocrew-autofix', workspace: 'autofix', memory_store: 'default', model: '', last_active_ts: 1000, last_message: 'Six new issues: four covered by open PRs.' },
  { name: 'fixer', slug: 'fixer', bound: true, slot_key: 'member-fixer', running: false, kiro_agent: 'kirocrew', workspace: 'default', memory_store: 'default', model: '', last_active_ts: 900, last_message: 'Two PRs opened for the queue.' },
]

// A transcript tall enough to scroll at 820px (so the follow has somewhere to
// go and "left where they are" is a real distance) but SHORT enough that every
// row stays mounted in the virtualizer's window: with rows unmounting above as
// the tail mounts, an append can SHRINK scrollHeight (estimate-priced spacers
// replacing measured rows) and the engine's clamp then lands the reader at the
// bottom by coincidence, which would photograph as a follow that never
// happened. Eight turns keep the whole thread in the window, so only the
// follow policy can put the reader at the end.
const LONG_THREAD = Array.from({ length: 8 }, (_, i) => ([
  { role: 'user', content: `Status check #${i + 1}: anything new in the queue?`, ts: `2026-09-28T01:${String(10 + i).padStart(2, '0')}:00Z`, meta: { mid: `u-${i}` } },
  { role: 'assistant', content: `Sweep ${i + 1} done.\n\n- two issues triaged as duplicates\n- one PR moved to review-ready\n- CI green on the retry`, ts: `2026-09-28T01:${String(10 + i).padStart(2, '0')}:30Z`, meta: { mid: `a-${i}` } },
])).flat()

const REPLY_1 = 'Worker report: PR #14801 is green — 82 checks passed, PR Readiness success. Nothing left for you to do on it.'
const REPLY_2 = 'Heads-up: main moved under #14590; I have the rebase ready and am waiting for the push slot.'
const REPLY_3 = 'Back on it — the second lane re-ran clean, so the round is terminal.'
const STREAM = ['Sweep 9 ', 'done.\n\n- three ', 'issues triaged\n- ', 'one PR opened for ', 'the gate\n- CI ', 'green on the first try']

const browser = await chromium.launch()
let failed = false

function check(name, ok, detail) {
  console.log(`${name}: ${ok ? 'OK' : 'MISMATCH'} ${detail}`)
  if (!ok) failed = true
  return ok
}

const SCROLLER = '[data-chat-pane] .chat-container'

async function geom(page) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el) return null
    return { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight }
  }, SCROLLER)
}

const atBottom = (g) => !!g && g.height > g.client && Math.abs(g.top - (g.height - g.client)) <= 2
const fmt = (g) => `top=${g?.top} bottom=${g ? g.height - g.client : '-'} (content ${g?.height}px in ${g?.client}px viewport)`

/** Is this text on screen inside the transcript viewport (not merely mounted)?
 *  The transcript is virtualized, so `scrollHeight` is not monotonic across an
 *  append (rows above the window unmount as the tail mounts); "the reply is in
 *  view at the bottom" is the claim, and this is how it is read. */
async function inView(page, text) {
  return page.evaluate(({ sel, text }) => {
    const el = document.querySelector(sel)
    const node = [...el.querySelectorAll('[data-display-index]')].find((r) => r.textContent.includes(text))
    if (!node) return { found: false }
    const box = el.getBoundingClientRect()
    const r = node.getBoundingClientRect()
    return { found: true, visible: r.bottom <= box.bottom + 2 && r.bottom > box.top, bottomGap: Math.round(box.bottom - r.bottom) }
  }, { sel: SCROLLER, text })
}

/** The first row fully inside the viewport and where its top sits: the reader's
 *  visual anchor, compared before/after an append to prove nothing moved. */
async function anchor(page) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    const box = el.getBoundingClientRect()
    const rows = [...el.querySelectorAll('[data-display-index]')].map((r) => ({ index: r.getAttribute('data-display-index'), top: Math.round(r.getBoundingClientRect().top - box.top) }))
    return rows.find((r) => r.top >= 0) ?? null
  }, SCROLLER)
}

/** Fire a store frame into the page, the way the WS would. */
async function frame(page, detail) {
  await page.evaluate((d) => { window.dispatchEvent(new CustomEvent('capture:frame', { detail: d })) }, detail)
}

/** A REAL wheel over the transcript: the hardware input the follow guard
 *  stamps, dispatched by the browser, not synthesised on the element. */
async function wheelOverTranscript(page, deltaY) {
  const box = await page.locator(SCROLLER).boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.wheel(0, deltaY)
  await page.waitForTimeout(250)
}

async function newPage(context, theme) {
  const page = await context.newPage()
  await page.route(u => new URL(u).pathname.startsWith('/api/'), route => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    if (path === '/api/members') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ members: MEMBERS, default_agent: 'kirocrew' }) })
    }
    const thread = path.match(/^\/api\/members\/([^/]+)\/thread$/)
    if (thread) {
      const slug = decodeURIComponent(thread[1])
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slot_key: `member-${slug}`, slug, member: slug, created: false }) })
    }
    if (/^\/api\/members\/[^/]+\/activity$/.test(path)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slug: 'radar', member: 'radar', capped: false, entries: [] }) })
    }
    if (/^\/api\/chat\/slots\/[^/]+$/.test(path)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ key: 'member-radar', title: 'radar', running: false, messages: LONG_THREAD }) })
    }
    // The composer's send: a 202 receipt is all the pane needs to keep its
    // optimistic bubble; nothing streams back in this gateway-free harness.
    if (path === '/api/chat' && req.method() === 'POST') {
      return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ ok: true, mid: `cap-send-${Date.now().toString(36)}` }) })
    }
    if (path === '/api/crons') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ jobs: [] }) })
    if (path === '/api/webhooks') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tokens: [] }) })
    if (path === '/api/agents') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ agents: [], default_agent: 'kirocrew' }) })
    const isList = /commands|skills|agents|sessions|files|history|models|artifacts|folders/.test(path)
    return route.fulfill({ status: 200, contentType: 'application/json', body: isList ? '[]' : '{}' })
  })
  // `idle=1`: radar's slot frame says `running: false`, so the pane's follow
  // sees a genuinely idle DM -- the state the defect lived in.
  await page.goto(`${BASE}/capture/members-page.html?theme=${theme}&idle=1`)
  await page.waitForSelector('[data-capture-root]')
  await page.getByText('radar', { exact: true }).first().click()
  await page.getByText('Status check #8: anything new in the queue?').waitFor()
  // Give the RO-driven initial pin a frame to land before reading geometry.
  await page.waitForTimeout(300)
  return page
}

async function run(theme, context, tag) {
  const page = await newPage(context, theme)
  const g0 = await geom(page)
  check(`${tag} 00 auto-pinned to bottom on open`, atBottom(g0), fmt(g0))

  // 01 -- wheel at the end (moves nothing), then a COMPLETE reply lands idle.
  await wheelOverTranscript(page, 240)
  const g1a = await geom(page)
  check(`${tag} 01 wheel at the end moved nothing`, atBottom(g1a), fmt(g1a))
  await frame(page, { kind: 'reply', slot: 'member-radar', text: REPLY_1, sendId: 'reply-1' })
  await page.getByText(REPLY_1).waitFor()
  await page.waitForTimeout(400)
  const g1 = await geom(page)
  const v1 = await inView(page, 'Worker report: PR #14801')
  // The content must have GROWN: a clamp after a shrink is not a follow.
  check(`${tag} 01 idle reply followed to the bottom`, atBottom(g1) && g1.height > g1a.height && v1.found && v1.visible, `${fmt(g1)} reply=${JSON.stringify(v1)}`)
  const pill1 = await page.getByLabel('Scroll to bottom').count()
  check(`${tag} 01 no jump pill`, pill1 === 0, `pills=${pill1}`)
  await page.screenshot({ path: `${OUT}/01-idle-reply-follows-${theme}.png` })

  if (theme !== 'dark') { await page.close(); return }

  // 02 -- a live reply streams in delta by delta.
  for (let i = 0; i < STREAM.length; i++) {
    await frame(page, { kind: 'chunk', slot: 'member-radar', text: STREAM[i], seq: i + 1 })
    await page.waitForTimeout(90)
  }
  // The streaming renderer reveals text progressively; let it catch up so the
  // frame shows the whole reply, then read the geometry.
  await page.waitForTimeout(1200)
  const g2 = await geom(page)
  const v2 = await inView(page, 'Sweep 9')
  check('dark 02 streaming reply followed', atBottom(g2) && v2.found && v2.visible, `${fmt(g2)} tail=${JSON.stringify(v2)}`)
  await page.screenshot({ path: `${OUT}/02-streaming-follows-dark.png` })
  await frame(page, { kind: 'done', slot: 'member-radar' })
  await page.waitForTimeout(200)

  // 03 -- the reader scrolls up to read; a reply lands; nothing moves them.
  await wheelOverTranscript(page, -700)
  const parked = await geom(page)
  const a3 = await anchor(page)
  check('dark 03 reader parked above the bottom', !!parked && !atBottom(parked) && parked.top < parked.height - parked.client - 300, fmt(parked))
  await frame(page, { kind: 'reply', slot: 'member-radar', text: REPLY_2, sendId: 'reply-2' })
  await page.waitForTimeout(500)
  const g3 = await geom(page)
  const a3b = await anchor(page)
  check('dark 03 reading position kept (row under the reader did not move)', !!a3 && !!a3b && a3.index === a3b.index && Math.abs(a3.top - a3b.top) <= 2 && !atBottom(g3), `${fmt(g3)} anchor before=${JSON.stringify(a3)} after=${JSON.stringify(a3b)}`)
  await page.getByLabel('Scroll to bottom').waitFor()
  check('dark 03 jump pill offered', true, 'jump-to-bottom rendered')
  await page.screenshot({ path: `${OUT}/03-reading-kept-dark.png` })

  // 04 -- the reader scrolls back down to the end by hand; the next reply follows.
  await wheelOverTranscript(page, 4000)
  const g4a = await geom(page)
  check('dark 04 returned to the bottom by hand', atBottom(g4a), fmt(g4a))
  await page.waitForTimeout(300)
  await frame(page, { kind: 'reply', slot: 'member-radar', text: REPLY_3, sendId: 'reply-3' })
  await page.getByText(REPLY_3).waitFor()
  await page.waitForTimeout(400)
  const g4 = await geom(page)
  const v4 = await inView(page, 'the round is terminal')
  check('dark 04 reply after the return followed', atBottom(g4) && v4.found && v4.visible, `${fmt(g4)} reply=${JSON.stringify(v4)}`)
  const pill4 = await page.getByLabel('Scroll to bottom').count()
  check('dark 04 no jump pill', pill4 === 0, `pills=${pill4}`)
  await page.screenshot({ path: `${OUT}/04-return-follows-dark.png` })

  // 05 -- the reader scrolls up and SENDS from there.
  await wheelOverTranscript(page, -700)
  const g5a = await geom(page)
  check('dark 05 reader parked above the bottom', !!g5a && !atBottom(g5a), fmt(g5a))
  const box = page.getByLabel('Message input').first()
  await box.waitFor()
  await box.fill('Thanks — merge it when the human review lands.')
  await page.waitForTimeout(200)
  await box.press('Enter')
  await page.getByText('Thanks — merge it when the human review lands.').waitFor()
  await page.waitForTimeout(500)
  const g5 = await geom(page)
  check('dark 05 send landed the reader at the bottom', atBottom(g5), fmt(g5))
  const pill5 = await page.getByLabel('Scroll to bottom').count()
  check('dark 05 no jump pill after send', pill5 === 0, `pills=${pill5}`)
  await page.screenshot({ path: `${OUT}/05-send-follows-dark.png` })
  await page.close()
}

{
  const context = await browser.newContext({
    viewport: { width: 1280, height: 820 },
    deviceScaleFactor: 1,
    ...(VIDEO ? { recordVideo: { dir: OUT, size: { width: 1280, height: 820 } } } : {}),
  })
  await run('dark', context, 'dark')
  await context.close()
}
{
  const context = await browser.newContext({ viewport: { width: 1280, height: 820 }, deviceScaleFactor: 1 })
  await run('light', context, 'light')
  await context.close()
}

await browser.close()
if (failed) {
  console.error('CAPTURE FAILED: at least one frame did not match its asserted state')
  process.exit(1)
}
console.log('all frames verified')
