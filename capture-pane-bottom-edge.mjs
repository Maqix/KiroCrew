/**
 * One frame of the Crewmate DM's bottom edge: the last transcript line meeting
 * the composer. Run against a dev server on the branch under test:
 *   node capture-pane-bottom-edge.mjs http://127.0.0.1:6833 out.png [label]
 * Asserts whether a bottom fade element is present and prints it, so the frame
 * name can be trusted.
 */
import { chromium } from 'playwright'

const BASE = process.argv[2]
const OUT = process.argv[3]
const EXPECT_FADE = process.argv[4] === 'with-fade'

const MEMBERS = [
  { name: 'radar', slug: 'radar', bound: true, slot_key: 'member-radar', running: false, kiro_agent: 'kirocrew-autofix', workspace: 'autofix', memory_store: 'default', model: '', last_active_ts: 1000, last_message: 'Six new issues: four covered by open PRs.' },
]
const THREAD = Array.from({ length: 8 }, (_, i) => ([
  { role: 'user', content: `Status check #${i + 1}: anything new in the queue?`, ts: `2026-09-28T01:${String(10 + i).padStart(2, '0')}:00Z`, meta: { mid: `u-${i}` } },
  { role: 'assistant', content: `Sweep ${i + 1} done.\n\n- two issues triaged as duplicates\n- one PR moved to review-ready\n- CI green on the retry`, ts: `2026-09-28T01:${String(10 + i).padStart(2, '0')}:30Z`, meta: { mid: `a-${i}` } },
])).flat()

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, deviceScaleFactor: 2 })
await page.route(u => new URL(u).pathname.startsWith('/api/'), route => {
  const path = new URL(route.request().url()).pathname
  if (path === '/api/members') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ members: MEMBERS, default_agent: 'kirocrew' }) })
  if (/^\/api\/members\/[^/]+\/thread$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slot_key: 'member-radar', slug: 'radar', member: 'radar', created: false }) })
  if (/^\/api\/members\/[^/]+\/activity$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slug: 'radar', member: 'radar', capped: false, entries: [] }) })
  if (/^\/api\/chat\/slots\/[^/]+$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ key: 'member-radar', title: 'radar', running: false, messages: THREAD }) })
  const isList = /commands|skills|agents|sessions|files|history|models|artifacts|folders|crons|webhooks/.test(path)
  return route.fulfill({ status: 200, contentType: 'application/json', body: isList ? '[]' : '{}' })
})
await page.goto(`${BASE}/capture/members-page.html?theme=dark`)
await page.waitForSelector('[data-capture-root]')
await page.getByText('radar', { exact: true }).first().click()
await page.getByText('Status check #8: anything new in the queue?').waitFor()
await page.waitForTimeout(400)
const hasFade = await page.evaluate(() => !!document.querySelector('[data-chat-pane] .bg-gradient-to-t.from-bg'))
console.log(`bottom fade present: ${hasFade} (expected ${EXPECT_FADE})`)
if (hasFade !== EXPECT_FADE) { console.error('MISMATCH'); process.exit(1) }
const box = await page.locator('[data-chat-pane] .chat-container').boundingBox()
await page.screenshot({ path: OUT, clip: { x: box.x, y: box.y + box.height - 140, width: box.width, height: 260 } })
await browser.close()
console.log('ok', OUT)
