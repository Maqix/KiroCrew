/**
 * The home card's "Sign in to AWS" (dashboard/setup_aws_signin.py): the AWS CLI's
 * own browser sign-in, started from the card instead of a terminal.
 *
 * - not signed in: a primary "Sign in to AWS" that posts `aws_signin`, and the
 *   usual "Not now";
 * - signing in (`waiting`, `outcome.aws_signin.state === 'waiting'`): the
 *   finish-in-the-browser line and a Cancel that posts `aws_signin` with
 *   `cancel: true`, and no build progress list;
 * - signed in from the card (`outcome.aws_signed_in`, the payload still says
 *   false): the normal Build button and the account;
 * - a gateway the browser is not on: the terminal command it sent, then Build;
 * - a sign-in the gateway lost (past its time, still waiting): Try again.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'

const HASH = 'd'.repeat(64)
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-west-1', profile: 'work',
  size: { key: 'balanced', label: 'Development', instance_type: 't3.large', ram_gb: 8, vcpu: 2 },
  monthly_usd: 61, billed_by: 'AWS, to your own account', aws_signed_in: false, aws_account: '',
  sign_in_commands: ['aws login'],
}

function home(over: Partial<Card> = {}): Card {
  return {
    id: 'sc-aws0123456789abc', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: PAYLOAD, outcome: null, error: null, created_ts: 1790000000, decided_ts: null,
    classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

/** A one-card gateway; `decide` computes the card the POST returns. */
function serve(initial: Card, decide?: (body: SetupDecideBody, current: Card) => Card) {
  const gw = { card: initial, bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${initial.id}`, () => HttpResponse.json(gw.card)),
    http.post(`/api/setup/cards/${initial.id}/decide`, async ({ request }) => {
      const body = (await request.json()) as SetupDecideBody
      gw.bodies.push(body)
      gw.card = decide ? decide(body, gw.card) : gw.card
      return HttpResponse.json(gw.card)
    }),
  )
  return gw
}

function renderHome(card: Card) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat']}>
        <SetupCard cardId={card.id} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return screen.findByTestId('setup-card')
}

const WAITING = { aws_signin: { state: 'waiting', expires_ts: Date.now() / 1000 + 300 } }

describe('SetupCard — home: signing in to AWS from the card', () => {
  it('not signed in: "Sign in to AWS" posts aws_signin with the hash, and no terminal command', async () => {
    const gw = serve(home(), (_b, c) => ({ ...c, status: 'waiting', outcome: WAITING }))
    await renderHome(home())
    const block = screen.getByTestId('setup-card-home-aws-signin')
    expect(block).toHaveTextContent('The AWS sign-in page opens in your browser. Kiro Crew never sees your password or keys.')
    // The terminal asks nothing: no command to copy on a machine the browser is on.
    expect(screen.queryByTestId('setup-card-home-aws-command')).toBeNull()
    expect(screen.getByTestId('setup-card-decline')).toHaveTextContent('Not now')
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('Sign in to AWS')
    await userEvent.click(primary)
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'aws_signin', hash: HASH }))
    // The response is the waiting card: the finish-in-the-browser line, not a build.
    expect(await screen.findByTestId('setup-card-home-aws-waiting')).toHaveTextContent(
      'Finish signing in in the browser tab that opened…',
    )
  })

  it('signing in: a status line and Cancel, which posts cancel; no build steps, no Build', async () => {
    const gw = serve(home({ status: 'waiting', outcome: WAITING }), (_b, c) => ({ ...c, status: 'pending', outcome: {} }))
    const el = await renderHome(home({ status: 'waiting', outcome: WAITING }))
    expect(el).toHaveAttribute('data-status', 'waiting')
    expect(within(el).getByRole('status')).toHaveTextContent('Finish signing in in the browser tab that opened…')
    expect(screen.queryByTestId('setup-card-steps')).toBeNull()
    expect(screen.queryByTestId('setup-card-primary')).toBeNull()
    expect(screen.queryByTestId('setup-card-decline')).toBeNull()
    const cancel = screen.getByTestId('setup-card-secondary')
    expect(cancel).toHaveTextContent('Cancel sign-in')
    await userEvent.click(cancel)
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'aws_signin', hash: HASH, input: { cancel: true } }))
    expect(await screen.findByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
  })

  it('signed in from the card: the outcome says so, so Build shows with the account', async () => {
    const signedIn = home({ outcome: { aws_signed_in: true, aws_account: '…9012', aws_signin: { state: 'done' } } })
    const gw = serve(signedIn)
    await renderHome(signedIn)
    expect(screen.queryByTestId('setup-card-home-aws-signin')).toBeNull()
    expect(screen.getByTestId('setup-card-home-meta')).toHaveTextContent('AWS: signed in ✓ …9012')
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('Build my home')
    await userEvent.click(primary)
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH }))
  })

  it('a gateway the browser is not on: the refusal, the terminal command it sent, then Build', async () => {
    const remote = home({
      outcome: { aws_signin: { state: 'remote', command: 'aws login --remote --profile work' } },
      error: { code: 'aws_signin_remote', message: 'cannot open the sign-in page there' },
    })
    const gw = serve(remote)
    await renderHome(remote)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent(
      'Kiro Crew can’t open the AWS sign-in page on the computer it runs on.',
    )
    expect(screen.getByTestId('setup-card-home-aws-signin')).toHaveTextContent(
      'In a terminal on the computer Kiro Crew runs on, sign in with this command.',
    )
    expect(screen.getByTestId('setup-card-home-aws-command')).toHaveTextContent('aws login --remote --profile work')
    expect(screen.getByTestId('setup-card-home-aws-command-copy')).toHaveAccessibleName('Copy command')
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('I signed in — build my home')
    await userEvent.click(primary)
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH }))
  })

  it('a sign-in that ran out of time is shown in the owner’s words', async () => {
    const timedOut = home({ error: { code: 'aws_signin_timeout', message: 'the AWS sign-in was not finished in time' } })
    serve(timedOut)
    await renderHome(timedOut)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent('The AWS sign-in wasn’t finished in time.')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
  })

  it('a profile that holds access keys is told to ask for a home under a new profile', async () => {
    const withKeys = home({
      error: { code: 'aws_signin_profile_has_keys', message: 'this AWS profile holds access keys' },
    })
    serve(withKeys)
    await renderHome(withKeys)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent(
      'This AWS profile holds access keys, so it can’t use a browser sign-in. Ask in the chat for a home under a new profile name, such as “kirocrew”.',
    )
  })

  it('the build’s Kiro sign-in says it is the home’s own, and one click for a signed-in browser', async () => {
    const building = home({
      payload: { ...PAYLOAD, aws_signed_in: true },
      status: 'waiting',
      outcome: {
        job_id: 'job-1', status: 'awaiting_signin', error: '',
        steps: [{ key: 'signin', label: 'Sign in to Kiro', state: 'active', detail: '' }],
        signin: { url: 'https://view.awsapps.com/start/#/device?user_code=ABCD-EFGH', code: 'ABCD-EFGH' },
      },
    })
    serve(building)
    await renderHome(building)
    expect(screen.getByTestId('setup-card-home-signin')).toHaveTextContent(
      'This is your home’s own Kiro sign-in; each machine keeps its own. Open the page and confirm this code. If your browser is already signed in to Kiro, that’s one click.',
    )
    expect(screen.getByTestId('setup-card-home-signin-code')).toHaveTextContent('ABCD-EFGH')
  })

  it('a sign-in the gateway lost (past its time, still waiting) offers Try again', async () => {
    const lost = home({ status: 'waiting', outcome: { aws_signin: { state: 'waiting', expires_ts: 1790000000 } } })
    const gw = serve(lost, (_b, c) => ({ ...c, outcome: WAITING }))
    await renderHome(lost)
    expect(screen.getByTestId('setup-card-home-aws-stalled')).toHaveTextContent('This sign-in stopped before it finished.')
    const retry = screen.getByTestId('setup-card-primary')
    expect(retry).toHaveTextContent('Try again')
    expect(screen.getByTestId('setup-card-secondary')).toHaveTextContent('Cancel sign-in')
    await userEvent.click(retry)
    await waitFor(() => expect(gw.bodies[0]).toEqual({ decision: 'aws_signin', hash: HASH }))
  })
})
