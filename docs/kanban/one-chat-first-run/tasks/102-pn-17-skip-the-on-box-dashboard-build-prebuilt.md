---
id: 102
title: PN.17 Skip the on-box dashboard build (prebuilt dashboard in the home)
status: review
priority: medium
created: 2026-09-28T19:12:39.682844255Z
updated: 2026-09-28T21:10:23.703542409Z
tags:
    - parallel-nest
    - installer
    - perf
class: standard
---

Sizing research 2026-09-28: the home's first boot installs Node and runs npm ci plus the vite build, which is the 2.6 GB peak and most of the first-boot time. Released versions: install the home with cli.sh (the signed prebuilt wheel start.sh uses) instead of a source build. Source launches: ship the locally built static/dist in the source tarball (today cloud/source.py drops dist and git-archive skips it because it is gitignored; ship it only when it is newer than website/src), and skip the frontend step in install.sh and the Node install in the template when it is present. This makes a 4 GB home workable and cuts first-boot time.


Done (uncommitted, for review): source.py ships src/kiro_crew/static/dist in the tarball when index.html is present, not a symlink and no older than website/src and website/index.html (regular files only, credential-name filters apply). install.sh skips Node and the frontend build under KIROCREW_PREBUILT_FRONTEND=1 when static/dist/index.html is there. The template downloads the S3 tarball as root before the Node step, skips Node when the tarball lists static/dist/index.html, exports the flag to install.sh, and keeps the DIST_INDEX check. Worst-case UserData 14264 of 14336 bytes (comments moved to the rationale block). Tests: test_cloud_source.py::TestPrebuiltDashboard, test_cloud_ec2.py::TestPrebuiltDashboard. Spec: cloud.md 'The prebuilt dashboard'. Staged-tree check: a 145.6 MB tarball with 2692 dist entries, built in 57 s. Not yet run on a real home.
