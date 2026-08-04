#!/usr/bin/env python3
"""tick.py - one observation. Run me every 15 minutes.

What one tick does, in order:

  1. run every check in checks.py (no model, no network writes)
  2. append ONE rapp/1 frame recording the verdict to this twin's chain
  3. publish the head, and copy it where the peer looks

Step 2 is what makes this more than a cron job printing JSON. The frame is
hash-chained and content-addressed, so the record of what this sentinel saw
cannot be quietly rewritten later to look healthier than it was. That is the
whole reason rapp-sentinel uses rapp/1 frames rather than appending to a log.

Step 3 is deliberately a copy rather than a redirect. publish_head() writes
public/sentinel-head.json next to the code, and that path is baked into the
function. Rather than fork it - the schema it writes IS the contract a peer
verifies - the tick lets it write where it wants, then places the bytes where
the neighborhood can read them under a twin-qualified name, so two instances
can publish into one repo without colliding.

Exit code is always 0. The verdict is the payload, not the status: a non-zero
exit would make launchd treat a DEGRADED platform as a FAILED job, and the
point is that the job succeeded in noticing.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import neighborhood as NB       # noqa: E402

TWIN = "fieldguide"
STATE = os.path.join(HERE, "state")
PUBLIC = os.path.join(REPO, "public")


def run_health():
    """Shell out to health.py exactly as sentinel.py does, and for the same
    reason: a check that hangs or dies takes its own process down, not this
    one's."""
    r = subprocess.run([sys.executable, os.path.join(HERE, "health.py")],
                       capture_output=True, text=True, timeout=300, cwd=HERE)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "unknown", "checks": [], "failed": ["health.py"],
                "critical": [],
                "summary": "no parseable verdict: %s" % (r.stderr or "")[:300]}


def main():
    os.makedirs(STATE, exist_ok=True)
    os.makedirs(PUBLIC, exist_ok=True)
    verdict = run_health()

    # One frame per tick. `kind` follows the rapp/1 grammar: two
    # lowercase-hyphen segments joined by a dot.
    frame = NB.emit(TWIN, "sentinel.tick", {
        "status": verdict.get("status"),
        "failed": verdict.get("failed", []),
        "critical": verdict.get("critical", []),
        "summary": verdict.get("summary", "")[:500],
    })

    NB.publish_head()
    origin = os.path.join(HERE, "public", "sentinel-head.json")
    target = os.path.join(PUBLIC, "%s-head.json" % TWIN)
    if os.path.exists(origin):
        shutil.copyfile(origin, target)

    # The freshness beacon health.py's own w_sentinel_fresh check reads back.
    stamp = datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    with open(os.path.join(STATE, "last_run.json"), "w", encoding="utf-8") as fh:
        json.dump({"at": stamp, "status": verdict.get("status"),
                   "seq": frame["seq"]}, fh, indent=2)
        fh.write("\n")

    out = {
        "twin": TWIN,
        "status": verdict.get("status"),
        "seq": frame["seq"],
        "frame_hash": frame["frame_hash"][:12],
        "head_published": os.path.relpath(target, REPO),
        "checks": [{"id": c["id"], "ok": c["ok"], "detail": c["detail"]}
                   for c in verdict.get("checks", [])],
        "failed": verdict.get("failed", []),
        "critical": verdict.get("critical", []),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
