#!/usr/bin/env bash
# publish_heads.sh - commit the published heads, throttled, only when changed.
#
# NOT wired into launchd by install.sh. A job that writes to a public repo
# every 15 minutes will eventually ship something nobody read, so this is a
# separate script the orchestrator schedules on purpose.
#
# Two guards, and both matter:
#
#   CHANGED  a tick rewrites the head every 15 min with a fresh utc even when
#            seq did not move, so committing unconditionally produces 96
#            commits a day that say nothing.
#   THROTTLE at most once every THROTTLE_HOURS (default 2), tracked in a stamp
#            file. Even a real change does not need to reach Pages in 15 min -
#            the peer's staleness threshold is 90 minutes.
#
# [skip ci] on every commit: these are machine heartbeats and must never
# trigger a build.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
THROTTLE_HOURS="${THROTTLE_HOURS:-2}"
STAMP="$DIR/state/.last-head-push"

if [[ -f "$STAMP" ]]; then
  age=$(( ($(date +%s) - $(date -r "$STAMP" +%s)) / 60 ))
  if (( age < THROTTLE_HOURS * 60 )); then
    echo "throttled: last publish ${age}m ago, limit $((THROTTLE_HOURS*60))m"
    exit 0
  fi
fi

changed=0
git diff --quiet -- public/ 2>/dev/null || changed=1
[[ -n "$(git ls-files --others --exclude-standard -- public/ 2>/dev/null)" ]] && changed=1
if [[ $changed -eq 0 ]]; then
  echo "no head changed - nothing to publish"
  exit 0
fi

git add public/
git commit -q -m "heads: $(date -u +%Y-%m-%dT%H:%MZ) [skip ci]"
echo "committed heads at $(date -u +%Y-%m-%dT%H:%MZ)"
echo "NOTE: this script does not push. The orchestrator owns the remote."
touch "$STAMP"
