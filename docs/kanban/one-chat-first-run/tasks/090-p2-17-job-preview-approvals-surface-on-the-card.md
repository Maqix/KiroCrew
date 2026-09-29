---
id: 90
title: P2.17 Job preview approvals surface on the card
status: review
priority: medium
created: 2026-09-28T05:12:42.882182281Z
updated: 2026-09-29T04:29:33.385030441Z
tags:
    - phase-2
    - backend
    - frontend
parent: 17
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.384839376Z
class: standard
---

[[2026-09-28]] Mon 05:53
A cron preview's background approvals now show on the cron card while the preview runs (Allow once / Reject through POST /api/approvals/{id}/{action}); link = new provenance-only run_session (cron:<job id>) on the approval record, written by the gateway's cron approval callback. New owner-only GET /api/setup/cards/{id}/approvals, backed by setup_preview.ApprovalWatch (job id held in memory for the run). A success whose approval was rejected or unanswered becomes failure with reason approval_not_given; outcome.preview.approvals counts them; the card says the job will ask on every run, in Notifications, and how long a request waits. Failed preview keeps Run a preview now as primary. No auto-approval added. Tests: test/test_setup_preview.py, SetupCard.test.tsx (cron approvals block). Spec: first-run.md Job previews.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): A job preview's approval requests are answered on the cron card itself (CronPreviewApprovals.tsx, setup_preview.py). Tests: test_setup_preview.py, SetupCard.test.tsx.
