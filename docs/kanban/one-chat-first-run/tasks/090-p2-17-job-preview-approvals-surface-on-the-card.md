---
id: 90
title: P2.17 Job preview approvals surface on the card
status: review
priority: medium
created: 2026-09-28T05:12:42.882182281Z
updated: 2026-09-28T05:53:48.102975728Z
tags:
    - phase-2
    - backend
    - frontend
parent: 17
class: standard
---

[[2026-09-28]] Mon 05:53
A cron preview's background approvals now show on the cron card while the preview runs (Allow once / Reject through POST /api/approvals/{id}/{action}); link = new provenance-only run_session (cron:<job id>) on the approval record, written by the gateway's cron approval callback. New owner-only GET /api/setup/cards/{id}/approvals, backed by setup_preview.ApprovalWatch (job id held in memory for the run). A success whose approval was rejected or unanswered becomes failure with reason approval_not_given; outcome.preview.approvals counts them; the card says the job will ask on every run, in Notifications, and how long a request waits. Failed preview keeps Run a preview now as primary. No auto-approval added. Tests: test/test_setup_preview.py, SetupCard.test.tsx (cron approvals block). Spec: first-run.md Job previews.
