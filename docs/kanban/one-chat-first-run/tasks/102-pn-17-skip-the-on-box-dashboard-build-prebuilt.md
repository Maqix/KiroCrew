---
id: 102
title: PN.17 Skip the on-box dashboard build (prebuilt dashboard in the home)
status: backlog
priority: medium
created: 2026-09-28T19:12:39.682844255Z
updated: 2026-09-28T19:12:39.682844255Z
tags:
    - parallel-nest
    - installer
    - perf
class: standard
---

Sizing research 2026-09-28: the home's first boot installs Node and runs npm ci plus the vite build, which is the 2.6 GB peak and most of the first-boot time. Released versions: install the home with cli.sh (the signed prebuilt wheel start.sh uses) instead of a source build. Source launches: ship the locally built static/dist in the source tarball (today cloud/source.py drops dist and git-archive skips it because it is gitignored; ship it only when it is newer than website/src), and skip the frontend step in install.sh and the Node install in the template when it is present. This makes a 4 GB home workable and cuts first-boot time.
