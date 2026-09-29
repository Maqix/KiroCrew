---
id: 124
title: D8 Demo video of user actions (no narration)
status: review
priority: medium
created: 2026-09-29T17:42:47.241473165Z
updated: 2026-09-29T20:03:02.179643456Z
tags:
    - demo
claimed_by: demo-video
claimed_at: 2026-09-29T20:03:02.179642664Z
class: standard
---

Record the current first run (feat/one-chat-first-run, 074af488d) as user actions only: no narration, a short caption per step, clicks and typing at real speed, model waits fast-forwarded and tagged.

[[2026-09-29]] Tue 17:43
Done: /tmp/kc-demo/release/share/kiro-crew-first-run-actions.mp4, 3:33, 1440x900, H.264, no audio track; decodes to the end. Take 3 of 3 (take 1 failed on my approval filter, take 2 had a caption slip). Real model and gateway; cloud home and move-in simulated. Waits at 4-8x with a fast-forward tag; clicks and typing at 1x. README section 'User actions only, no narration'. Seen while recording: the tray folded right after a size was picked (both takes); 'What's going on?' and the token reply said the home card was still undecided while the home was building (both takes); the demo script's 'What to show' list still puts the home at step 3.

[[2026-09-29]] Tue 18:31
Re-recorded at 1e81f5567: /tmp/kc-demo/release/share/kiro-crew-first-run-actions.mp4, 3:40, 1440x900, H.264, no audio track; decodes to the end. The first cut is kept as kiro-crew-first-run-actions-v1.mp4. One clean take (an earlier attempt was stopped for a driver stall, and one full take was set aside for stale caption flashes). The tray stayed open through the size choice (expanded=true after each click on the home card), and 'What's going on?' said the home was building and nothing was needed from the user. The tray opened folded for each new card (import, connect, job, home) because the agent had already written below it; one Show opened it each time, and nothing was scrolled to cause a fold. Waits play at 4-5x under a fast-forward tag. README section updated.

[[2026-09-29]] Tue 19:17
Final take at 4c9af006b: /tmp/kc-demo/release/share/kiro-crew-first-run-actions.mp4, 3:14, 1440x900, H.264, no audio track; decodes to the end. The previous cut is kept as kiro-crew-first-run-actions-v2.mp4. The import, connect and job cards opened in full; 1 Show click was still needed, for the home card. The gateway shows that card before the kept job's '[Setup card result]' inject, and the inject opens the agent's next turn, so the tray folds as the card appears. It did so in both takes. The tray stayed open through the size choice, and 'What's going on?' said the home was building and nothing was needed from the user. One earlier take was set aside because the transcript stopped following the status answer (a Scroll to bottom arrow showed and the answer sat under the fold). README section updated.

[[2026-09-29]] Tue 20:03
Final take at 9c07dfd9a: /tmp/kc-demo/release/share/kiro-crew-first-run-actions.mp4, 3:14, 1440x900, H.264, no audio track; decodes to the end. Earlier cuts are kept as -v1, -v2 and -v3. Show clicks: 0. Every card, the home card included, opened in full as it arrived. The home card's row now follows the kept job's result. The transcript kept following every streaming reply: the driver checked for the Scroll to bottom button every 1.5 s while a reply streamed, and it never showed, in this take or the one before it. It showed once at 4c9af006b, so it is intermittent and not shown to be fixed. Still off: the sticky user prompt at the top of the chat covers the vault notice row under it; emoji draw as empty boxes in headless Chromium; each demo chat left an empty .kiro skeleton in ~/workplace/kirocrew-workspace/dashboard_chat-1-<sid>, outside the demo folder (mine are removed). The model asked before offering the import and connect cards in 2 of 3 takes; the driver answered in plain words, which the published take did not need.
