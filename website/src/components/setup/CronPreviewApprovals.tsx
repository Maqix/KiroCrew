/**
 * The approvals a running job preview waits on, drawn on the job's own card.
 *
 * A scheduled job's tool call that needs a person is a background approval: it
 * belongs to no chat, so with Notifications alone a preview waits until each
 * request is declined unanswered. While the preview runs, the card
 * reads `GET /api/setup/cards/{id}/approvals` (that run's requests only) and
 * answers through the same one-shot `POST /api/approvals/{id}/{action}` the
 * Notifications feed uses: Allow once or Reject, never a standing grant.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ShieldQuestion } from 'lucide-react'

import { api } from '../../api/client'
import { ApiError } from '../../api/apiError'
import { setupCardApprovalsQueryKey } from '../../api/setupCards'
import { toApiDecision } from '../../utils/approvalDecision'
import ErrorNotice from '../ErrorNotice'
import { Btn } from '../ui'

/** How often a running preview re-reads the approvals it waits on. */
export const PREVIEW_APPROVALS_POLL_MS = 2000

interface Answer {
  id: string
  decision: 'approved' | 'rejected'
}

export default function CronPreviewApprovals({ cardId }: { cardId: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const queryKey = setupCardApprovalsQueryKey(cardId)
  const { data } = useQuery({
    queryKey,
    queryFn: () => api.setupCardApprovals(cardId),
    refetchInterval: PREVIEW_APPROVALS_POLL_MS,
  })
  const answer = useMutation({
    mutationFn: ({ id, decision }: Answer) => api.resolveApproval(id, toApiDecision(decision)),
    onSettled: () => qc.invalidateQueries({ queryKey }),
  })
  const approvals = data?.approvals ?? []
  const answerError = answer.error
  const errorMessage = !answerError
    ? ''
    : answerError instanceof ApiError && answerError.status === 404
      ? t('components.setupCard.cron_approval_gone')
      : answerError instanceof Error ? answerError.message : t('components.setupCard.error_generic')
  return (
    // Polite: a request that appears mid-run is announced without stealing focus.
    <div className="mt-3 min-w-0" aria-live="polite" data-testid="setup-card-preview-approvals">
      {approvals.length > 0 && (
        <>
          <div className="mb-1 inline-flex items-center gap-1.5 text-[12px] font-medium text-muted">
            <ShieldQuestion className="lucide-inline" aria-hidden="true" />
            {t('components.setupCard.cron_approvals_heading')}
          </div>
          <ul className="flex flex-col gap-2">
            {approvals.map(a => (
              <li
                key={a.id}
                className="rounded-md border border-border bg-bg px-3 py-2 min-w-0"
                data-testid="setup-card-preview-approval"
              >
                <div className="text-[13px] font-medium text-text break-words">
                  {a.tool || t('components.setupCard.cron_approval_untitled')}
                </div>
                {a.tool_input && (
                  <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] text-muted">
                    {a.tool_input}
                  </pre>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Btn
                    type="button"
                    onClick={() => answer.mutate({ id: a.id, decision: 'approved' })}
                    disabled={answer.isPending}
                    data-testid="setup-card-approval-allow"
                  >
                    {t('components.setupCard.cron_approval_allow_once')}
                  </Btn>
                  <Btn
                    type="button"
                    onClick={() => answer.mutate({ id: a.id, decision: 'rejected' })}
                    disabled={answer.isPending}
                    data-testid="setup-card-approval-reject"
                  >
                    {t('components.setupCard.cron_approval_reject')}
                  </Btn>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {/* The request lives on the gateway and this list holds no draft, so the
          hand-off loses nothing. */}
      <ErrorNotice message={errorMessage} askAgent className="mt-2" />
    </div>
  )
}
