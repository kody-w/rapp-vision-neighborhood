#!/usr/bin/env bash
# install.sh - render and load the four launchd jobs for this neighborhood.
#
# NOTHING IS INSTALLED UNTIL YOU RUN THIS. It is written to be read first:
# `./install.sh --dry-run` renders every plist to /tmp, lints it, prints it,
# and loads nothing. That is the intended first invocation.
#
# Four jobs, two per twin:
#   com.rapp.vision-<twin>-producer   daily, writes the channel entry
#   com.rapp.vision-<twin>-sentinel   every 15 min, observes and chains
#
# The producers are staggered by an hour. They both walk the same nine
# channels over HTTP, and running them in the same minute doubles the request
# burst against a host that already answered one of our probes with a 503.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

# Pin the interpreter the code was tested against. rapp-sentinel's templates
# hardcode /usr/bin/python3; on this machine that is 3.9.6, not 3.11. The
# producers run on both, but the schedule should use what the tests used.
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.11}"
if [[ ! -x "$PYTHON" ]]; then
  echo "!! $PYTHON not executable. Set PYTHON=... and re-run." >&2
  exit 1
fi
echo "python:  $PYTHON  ($("$PYTHON" -c 'import sys;print(sys.version.split()[0])'))"
echo "dir:     $DIR"
echo "mode:    $([[ $DRY == 1 ]] && echo 'DRY RUN - nothing will be loaded' || echo 'INSTALL')"
echo

mkdir -p "$DIR/logs"

render() {                      # render <label> <hour> <minute>
  local label="$1" hour="${2:-0}" minute="${3:-0}"
  local tpl="$DIR/launchd/$label.plist.template"
  local out
  if [[ $DRY == 1 ]]; then out="/tmp/$label.plist"; else out="$HOME/Library/LaunchAgents/$label.plist"; fi
  sed -e "s|__DIR__|$DIR|g" -e "s|__HOME__|$HOME|g" -e "s|__PYTHON__|$PYTHON|g" \
      -e "s|__HOUR__|$hour|g" -e "s|__MINUTE__|$minute|g" "$tpl" > "$out"
  # Lint the RENDERED plist. The template cannot lint - it has placeholders
  # inside <integer> - so linting the template would be theatre.
  plutil -lint "$out" >/dev/null || { echo "!! $label rendered invalid"; exit 1; }
  echo "  rendered + lint OK: $out"
  [[ $DRY == 1 ]] && return 0
  launchctl unload "$out" 2>/dev/null || true
  launchctl load -w "$out"
  echo "  loaded: $label"
}

echo "producers (daily, staggered):"
render com.rapp.vision-tumbler-producer     9 20
render com.rapp.vision-fieldguide-producer 10 20
echo
echo "sentinels (every 15 min):"
render com.rapp.vision-tumbler-sentinel
render com.rapp.vision-fieldguide-sentinel
echo

if [[ $DRY == 1 ]]; then
  echo "DRY RUN complete. Nothing was loaded. Review /tmp/com.rapp.vision-*.plist,"
  echo "then run without --dry-run."
  exit 0
fi

cat <<EOM

loaded 4 jobs.
  logs:      $DIR/logs/
  verify:    launchctl list | grep com.rapp.vision-
  run once:  launchctl start com.rapp.vision-tumbler-producer
  uninstall: for L in com.rapp.vision-{tumbler,fieldguide}-{producer,sentinel}; do
               launchctl unload "\$HOME/Library/LaunchAgents/\$L.plist"
               rm "\$HOME/Library/LaunchAgents/\$L.plist"
             done

HEAD PUBLISHING IS NOT WIRED HERE, ON PURPOSE. A tick writes
public/<twin>-head.json locally; committing and pushing it is a separate
decision, because a job that force-pushes to a public repo every 15 minutes is
a job that will eventually push something you did not read. See README -
"Publishing the head" - for the throttled [skip ci] commit step.
EOM
