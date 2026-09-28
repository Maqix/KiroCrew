/**
 * The home card's size, chosen by the owner (setup_flow._home_payload's
 * `size_options`, `size_default` and `plan`): one radio per option with what it
 * runs and what it costs, a "Needs the paid plan" mark (and the upgrade link on
 * the Free plan), and the chosen size posted with Build. Plus the copy of the
 * size, quota and spend-limit refusals.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import { AWS_PLAN_UPGRADE_URL, AWS_VCPU_QUOTA_URL } from '../components/setup/SetupCardBodies'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'

const HASH = '2'.repeat(64)
const STARTER = {
  key: 'starter', label: 'Starter', note: 'free_plan_credits', instance_type: 'm7i-flex.large', vcpu: 2, ram_gb: 8,
  monthly_usd: 72, free_plan_ok: true,
}
const SMALL = {
  key: 'small', label: 'Small', note: 'few_chats', instance_type: 't4g.large', vcpu: 2, ram_gb: 8,
  monthly_usd: 51, free_plan_ok: false,
}
const STANDARD = {
  key: 'light', label: 'Standard', note: 'many_chats', instance_type: 't4g.xlarge', vcpu: 4, ram_gb: 16,
  monthly_usd: 101, free_plan_ok: false,
}
const LITE = {
  key: 'lite', label: 'Lite', note: 'lite_tradeoffs', instance_type: 't4g.small', vcpu: 2, ram_gb: 2,
  monthly_usd: 14, free_plan_ok: true,
}
const ECONOMY = {
  key: 'economy', label: 'Economy', note: 'all_on', instance_type: 't4g.medium', vcpu: 2, ram_gb: 4,
  monthly_usd: 26, free_plan_ok: false,
}
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-north-1', profile: 'default',
  size: { key: 'light', label: 'Light', instance_type: 't4g.xlarge', ram_gb: 16, vcpu: 4 },
  monthly_usd: 101, billed_by: 'AWS, to your own account', aws_signed_in: true, aws_account: '…1234',
  sign_in_commands: ['aws login'],
}

function home(payload: Record<string, unknown>, over: Partial<Card> = {}): Card {
  return {
    id: 'sc-sizes0123456789a', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: { ...PAYLOAD, ...payload }, outcome: null, error: null, created_ts: 1790000000,
    decided_ts: null, classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

const FREE = home({
  size_options: [{ ...STARTER, credits_usd: 187.5, credit_weeks: 11 }, STANDARD],
  size_default: 'starter',
  plan: { type: 'FREE', credits_usd: 187.5 },
})
const PAID = home({ size_options: [SMALL, STANDARD], size_default: 'small', plan: { type: 'PAID' } })

function serve(card: Card) {
  const gw = { bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)),
    http.post(`/api/setup/cards/${card.id}/decide`, async ({ request }) => {
      gw.bodies.push((await request.json()) as SetupDecideBody)
      return HttpResponse.json({ ...card, status: 'waiting', outcome: { job_id: 'job-1', steps: [], status: 'running', error: '' } })
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

const radio = (key: string) => within(screen.getByTestId(`setup-card-home-size-${key}`)).getByRole('radio')

describe('SetupCard — home: choosing the size', () => {
  it('on the Free plan: Starter first, with its credit, and Standard marked for the paid plan', async () => {
    const gw = serve(FREE)
    await renderHome(FREE)
    const starter = screen.getByTestId('setup-card-home-size-starter')
    expect(radio('starter')).toBeChecked()
    expect(starter).toHaveTextContent('Starter · about $72/month')
    expect(starter).toHaveTextContent('m7i-flex.large, 2 vCPU, 8GB of memory')
    // Cheapest first, as the gateway orders them.
    const order = screen.getAllByTestId(/^setup-card-home-size-(starter|light)$/).map(n => n.getAttribute('data-testid'))
    expect(order).toEqual(['setup-card-home-size-starter', 'setup-card-home-size-light'])
    expect(starter).toHaveTextContent('Works on a new AWS account’s Free plan, paid from its credits (about 11 weeks of $188 left).')
    expect(screen.queryByTestId('setup-card-home-size-starter-paid')).toBeNull()
    const standard = screen.getByTestId('setup-card-home-size-light')
    expect(standard).toHaveTextContent('Standard · about $101/month')
    expect(standard).toHaveTextContent('Room for many chats and helpers at once.')
    expect(screen.getByTestId('setup-card-home-size-light-paid')).toHaveTextContent('Needs the paid plan')
    const upgrade = screen.getByTestId('setup-card-home-upgrade')
    expect(upgrade).toHaveAttribute('href', AWS_PLAN_UPGRADE_URL)
    expect(upgrade).toHaveAttribute('target', '_blank')
    expect(upgrade).toHaveAttribute('rel', 'noopener noreferrer')
    // The cost line follows the choice.
    expect(screen.getByTestId('setup-card-home-cost')).toHaveTextContent('About $72/month')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { size: 'starter' } }]))
  })

  it('Lite says plainly what it gives up, and what the Free plan’s credit buys', async () => {
    const card = home({
      size_options: [{ ...LITE, credits_usd: 100, credit_weeks: 30 }, { ...STARTER, credits_usd: 100, credit_weeks: 6 }, STANDARD],
      size_default: 'starter',
      plan: { type: 'FREE', credits_usd: 100 },
    })
    const gw = serve(card)
    await renderHome(card)
    const lite = screen.getByTestId('setup-card-home-size-lite')
    expect(lite).toHaveTextContent('Lite · about $14/month')
    expect(lite).toHaveTextContent('t4g.small, 2 vCPU, 2GB of memory')
    expect(lite).toHaveTextContent('Works on a new AWS account’s Free plan, paid from its credits (about 30 weeks of $100 left).')
    expect(lite).toHaveTextContent(
      'The smallest home, with trade-offs: memory uses keyword search only, there is no dictation, '
      + 'the first reply after a quiet spell is slower, and it runs only a few things at once.',
    )
    expect(screen.queryByTestId('setup-card-home-size-lite-paid')).toBeNull()
    await userEvent.click(radio('lite'))
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { size: 'lite' } }]))
  })

  it('Economy is everything on, for the paid plan', async () => {
    const card = home({ size_options: [LITE, ECONOMY, SMALL, STANDARD], size_default: 'small', plan: { type: 'PAID' } })
    serve(card)
    await renderHome(card)
    const economy = screen.getByTestId('setup-card-home-size-economy')
    expect(economy).toHaveTextContent('Economy · about $26/month')
    expect(economy).toHaveTextContent('Everything on. Enough for your main chat, another chat or two and scheduled jobs.')
    expect(screen.getByTestId('setup-card-home-size-lite')).toHaveTextContent('The smallest home, with trade-offs')
    const order = screen.getAllByTestId(/^setup-card-home-size-(lite|economy|small|light)$/).map(n => n.getAttribute('data-testid'))
    expect(order).toEqual(['setup-card-home-size-lite', 'setup-card-home-size-economy', 'setup-card-home-size-small', 'setup-card-home-size-light'])
    expect(radio('small')).toBeChecked()
  })

  it('the owner’s pick is what Build posts', async () => {
    const gw = serve(FREE)
    await renderHome(FREE)
    await userEvent.click(radio('light'))
    expect(radio('light')).toBeChecked()
    expect(screen.getByTestId('setup-card-home-cost')).toHaveTextContent('About $101/month')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { size: 'light' } }]))
  })

  it('on the paid plan: Small leads, preselected, and nothing is marked', async () => {
    const gw = serve(PAID)
    await renderHome(PAID)
    expect(radio('small')).toBeChecked()
    const small = screen.getByTestId('setup-card-home-size-small')
    expect(small).toHaveTextContent('Small · about $51/month')
    expect(small).toHaveTextContent('Enough for your main chat, a few other chats and scheduled jobs.')
    expect(small).toHaveTextContent('t4g.large, 2 vCPU, 8GB of memory')
    expect(screen.queryByTestId('setup-card-home-size-small-paid')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-size-light-paid')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-size-starter')).toBeNull()
    expect(screen.getByTestId('setup-card-home-cost')).toHaveTextContent('About $51/month')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { size: 'small' } }]))
  })

  it('signed out: both sizes, the Free plan note, and no upgrade link yet', async () => {
    const out = home({
      aws_signed_in: false, aws_account: '', size_options: [STARTER, STANDARD], size_default: 'starter',
      signup_url: 'https://signin.aws.amazon.com/signup?request_type=register', aws_cli_installed: true,
    })
    serve(out)
    await renderHome(out)
    expect(radio('starter')).toBeChecked()
    expect(screen.getByTestId('setup-card-home-free-plan-note')).toHaveTextContent(
      'A brand-new AWS account starts on the Free plan, where Starter works.',
    )
    expect(screen.getByTestId('setup-card-home-size-light-paid')).toHaveTextContent('Needs the paid plan')
    expect(screen.queryByTestId('setup-card-home-upgrade')).toBeNull()
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
  })

  it('a paid-plan size refused on the Free plan: the reason and the upgrade page', async () => {
    const refused = { ...FREE, error: { code: 'home_size_needs_paid_plan', message: 'needs paid' } }
    serve(refused)
    await renderHome(refused)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent(
      'This size needs the AWS paid plan, and this account is on the Free plan. Choose Starter, or upgrade the account on AWS first.',
    )
    expect(screen.getByTestId('setup-card-home-upgrade-after-error')).toHaveAttribute('href', AWS_PLAN_UPGRADE_URL)
  })

  it('a vCPU quota too low: the reason and the Service Quotas page', async () => {
    const refused = { ...PAID, error: { code: 'home_vcpu_quota_low', message: 'quota' } }
    serve(refused)
    await renderHome(refused)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent('This AWS account’s vCPU quota is too low for this size.')
    const quota = screen.getByTestId('setup-card-home-quota')
    expect(quota).toHaveTextContent('Open Service Quotas')
    expect(quota).toHaveAttribute('href', AWS_VCPU_QUOTA_URL)
    expect(quota).toHaveAttribute('target', '_blank')
  })

  it('a build stopped by the spend limit says so on the failed card', async () => {
    const failed = { ...PAID, status: 'failed' as const, error: { code: 'home_spend_limit', message: 'spend limit' } }
    serve(failed)
    await renderHome(failed)
    expect(screen.getByTestId('setup-card-failed-error')).toHaveTextContent(
      'AWS stopped the build because this account reached its spend limit.',
    )
  })

  it('a build the card lost track of says it was stopped and is being removed', async () => {
    const failed = {
      ...PAID, status: 'failed' as const,
      outcome: { job_id: 'job-1', stopped: true },
      error: { code: 'home_build_untracked', message: 'the home’s build could not be followed any more' },
    }
    serve(failed)
    await renderHome(failed)
    expect(screen.getByTestId('setup-card-failed-error')).toHaveTextContent(
      'Kiro Crew lost track of this home’s build, so it stopped it; anything it had created in AWS is being removed. You can build again.',
    )
  })

  it('a card without options keeps its single size and posts no size', async () => {
    const legacy = home({})
    const gw = serve(legacy)
    await renderHome(legacy)
    expect(screen.queryByTestId('setup-card-home-sizes')).toBeNull()
    expect(screen.getByTestId('setup-card-home-size')).toHaveTextContent('Light (t4g.xlarge)')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH }]))
  })
})
