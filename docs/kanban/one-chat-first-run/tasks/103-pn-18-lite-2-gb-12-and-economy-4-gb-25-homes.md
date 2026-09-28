---
id: 103
title: PN.18 Lite (2 GB, ~$12) and Economy (4 GB, ~$25) homes
status: todo
priority: high
created: 2026-09-28T19:39:39.753952385Z
updated: 2026-09-28T19:39:39.753952385Z
tags:
    - parallel-nest
    - aws
    - home
    - perf
class: standard
---

Spike 2026-09-28 (measured, isolated homes; notes /tmp/kc-sizing/REPORT.md). Idle base 1.3 GB = gateway 936 MB (embedding runtime ~450 MB + model mmap 301 MB + ~180 MB rest) + background kiro-cli session ~270 MB + helpers ~70 MB. A chat ~390 MB (kiro-cli-chat ~230 MB, mcp-core ~80, mcp-cron ~78). Slim (embeddings off): gateway 180 MB, idle 546 MB, one chat turn 1.68 GB. Lite t4g.small 2 GB: ~3 things at once; the user gives up meaning-based memory search and local speech-to-text, and the first reply after 10–15 min idle is ~8 s slower. On a new account the Free plan's 6 months cost ~$72 of the $100 credit. Economy t4g.medium 4 GB: ~6 things at once, embeddings on. Changes: (1) PN.17 prebuilt dashboard (required); (2) embeddings off on Lite (KIROCREW_SKIP_MODEL_DOWNLOAD in the home unit, better a real config switch); (3) start the background runtime lazily (drop the boot _ensure_background in session_pool.start_pool, -0.25 GB); (4) no eager spawn of an untouched landing chat on small homes (session.eager_spawn=false, -0.39 GB); (5) session.timeout_secs 600–900 on Lite, 1800 on Economy; (6) the t4g.small and t4g.medium tiers in sizes.py, and the card says memory search is keyword-only on Lite; (7) a memory-sized cap on live chat sessions. Not verified: a real 2/4 GB boot, arm64 figures, burst credits, whether the voice extra has an arm64 wheel.
