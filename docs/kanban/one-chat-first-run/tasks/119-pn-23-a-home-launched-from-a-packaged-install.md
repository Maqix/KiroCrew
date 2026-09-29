---
id: 119
title: PN.23 A home launched from a packaged install ships a prebuilt dashboard too
status: todo
priority: medium
created: 2026-09-29T03:04:53.868540095Z
updated: 2026-09-29T03:04:53.868540095Z
tags:
    - parallel-nest
    - installer
    - perf
class: standard
---

Found by the docs sweep 2026-09-29: PN.17's prebuilt dashboard applies to source launches that carry a current static/dist. A home launched from a packaged install (pip or desktop) clones the source and builds the dashboard on the box, which is slow on Lite and Economy and peaks at 2.6 GB. Install the home from the signed release wheel (cli.sh) instead, or ship the install's own static/dist. RFC Phase 2b lists it.
