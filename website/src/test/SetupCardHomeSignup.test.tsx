/**
 * The home card for someone with no AWS account yet (setup_flow._home_payload's
 * `signup_url`, `signup_builder_id`, `aws_cli_installed`):
 *
 * - beside "Sign in to AWS", a "Create an AWS account" link that opens the AWS
 *   sign-up in a new tab (never in the card), through the safe-URL helper;
 * - with `signup_builder_id`, the copy says to use the Builder ID they use for
 *   Kiro, with no new password;
 * - following the link switches the card to an untimed "creating your account"
 *   state whose primary runs the `aws_signin` decision and whose Back returns;
 * - `aws_cli_installed: false` adds one line linking the AWS CLI install page.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import { AWS_CLI_INSTALL_URL } from '../components/setup/SetupCardBodies'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'

const HASH = 'e'.repeat(64)
const SIGNUP = 'https://signin.aws.amazon.com/signup?request_type=register'
const BUILDER_ID_SIGNUP = 'https://signin.aws.amazon.com/signup?request_type=builderId'
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-west-1', profile: 'default',
  size: { key: 'light', label: 'Light', instance_type: 't3.small', ram_gb: 2, vcpu: 2 },
  monthly_usd: 17, billed_by: 'AWS, to your own account', aws_signed_in: false, aws_account: '',
  sign_in_commands: ['aws login'],
  signup_url: SIGNUP, signup_builder_id: false, aws_cli_installed: true,
}

function home(payload: Record<string, unknown> = {}): Card {
  return {
    id: 'sc-signup0123456789', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: { ...PAYLOAD, ...payload }, outcome: null, error: null, created_ts: 1790000000,
    decided_ts: null, classic: { kind: 'route', target: '/settings' },
  }
}

function serve(card: Card) {
  const gw = { bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)),
    http.post(`/api/setup/cards/${card.id}/decide`, async ({ request }) => {
      gw.bodies.push((await request.json()) as SetupDecideBody)
      return HttpResponse.json({ ...card, status: 'waiting', outcome: { aws_signin: { state: 'waiting', expires_ts: Date.now() / 1000 + 300 } } })
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

/** A link click in jsdom would try to navigate; the card's own handler still runs. */
function holdNavigation() {
  document.addEventListener('click', e => e.preventDefault(), { capture: true, once: true })
}

describe('SetupCard — home: creating an AWS account', () => {
  it('links the AWS sign-up in a new tab beside "Sign in to AWS"', async () => {
    serve(home())
    await renderHome(home())
    const link = screen.getByTestId('setup-card-home-aws-signup')
    expect(link).toHaveTextContent('Create an AWS account')
    expect(link).toHaveAttribute('href', SIGNUP)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByTestId('setup-card-home-aws-signup-block')).toHaveTextContent(
      'No AWS account yet? Creating one is free; your home then costs the estimate above.',
    )
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
    // The CLI is installed: no install line.
    expect(screen.queryByTestId('setup-card-home-aws-cli-missing')).toBeNull()
  })

  it('names the Builder ID when Kiro signs in with one', async () => {
    const card = home({ signup_url: BUILDER_ID_SIGNUP, signup_builder_id: true })
    serve(card)
    await renderHome(card)
    expect(screen.getByTestId('setup-card-home-aws-signup')).toHaveAttribute('href', BUILDER_ID_SIGNUP)
    expect(screen.getByTestId('setup-card-home-aws-signup-block')).toHaveTextContent(
      'Create one with the Builder ID you use for Kiro, with no new password.',
    )
  })

  it('following the link waits, untimed, for "I’ve created it — sign in", which runs the sign-in', async () => {
    const gw = serve(home())
    const el = await renderHome(home())
    holdNavigation()
    await userEvent.click(screen.getByTestId('setup-card-home-aws-signup'))
    expect(within(el).getByTestId('setup-card-home-aws-creating')).toHaveTextContent(
      'Finish creating your account in the tab that opened. When you’re done, sign in here.',
    )
    expect(screen.queryByTestId('setup-card-home-aws-signup')).toBeNull()
    // No request yet: only the owner knows when the account exists.
    expect(gw.bodies).toEqual([])
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('I’ve created it — sign in')
    await userEvent.click(primary)
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'aws_signin', hash: HASH }]))
  })

  it('Back leaves the creating state without a request', async () => {
    const gw = serve(home())
    await renderHome(home())
    holdNavigation()
    await userEvent.click(screen.getByTestId('setup-card-home-aws-signup'))
    const back = screen.getByTestId('setup-card-secondary')
    expect(back).toHaveTextContent('Back')
    await userEvent.click(back)
    expect(screen.queryByTestId('setup-card-home-aws-creating')).toBeNull()
    expect(screen.getByTestId('setup-card-home-aws-signup')).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
    expect(gw.bodies).toEqual([])
  })

  it('says the AWS CLI is needed, and links its install page, when it is missing', async () => {
    const card = home({ aws_cli_installed: false })
    serve(card)
    await renderHome(card)
    const line = screen.getByTestId('setup-card-home-aws-cli-missing')
    expect(line).toHaveTextContent('Signing in needs the AWS CLI 2.32 or newer on this computer.')
    const install = screen.getByTestId('setup-card-home-aws-cli-install')
    expect(install).toHaveTextContent('Install the AWS CLI')
    expect(install).toHaveAttribute('href', AWS_CLI_INSTALL_URL)
    expect(AWS_CLI_INSTALL_URL).toBe('https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html')
    expect(install).toHaveAttribute('target', '_blank')
    expect(install).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('draws no link for a sign-up URL the safe-URL helper refuses', async () => {
    const card = home({ signup_url: 'javascript:alert(1)' })
    serve(card)
    await renderHome(card)
    expect(screen.queryByTestId('setup-card-home-aws-signup')).toBeNull()
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
  })

  it('a signed-in card shows no sign-up at all', async () => {
    const card = home({ aws_signed_in: true, aws_account: '…1234' })
    serve(card)
    await renderHome(card)
    expect(screen.queryByTestId('setup-card-home-aws-signin')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-aws-signup')).toBeNull()
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Build my home')
  })
})
