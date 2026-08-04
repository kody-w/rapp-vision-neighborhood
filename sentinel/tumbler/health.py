#!/usr/bin/env python3
"""health.py — run every registered check, plus the watcher self-checks.

Costs nothing but API calls: no model is invoked here. The sentinel only
escalates when this reports something actually broken, which is what makes
running every 15 minutes forever affordable.

Domain checks live in checks.py — that is the file you edit. This file is the
runner and should rarely need changing.

Exit code is always 0; the verdict is the payload, not the status.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import checks as C

HOME = Path(__file__).resolve().parent


def probe_watchers():
    """The watchers watching the watchmen - retargeted for this neighborhood.

    UPSTREAM DIVERGENCE, and the reason is worth stating plainly. In
    kody-w/rapp-sentinel this function probes three things by name: a brainstem
    on localhost:7071, a launchd job called com.openrappter.daemon, and the
    sentinel's own freshness. Two of those three do not exist in this
    neighborhood. Left verbatim they fail on every tick forever, and a check
    that is always red is worse than no check - it is the thing that teaches
    you to ignore red.

    The upstream docs say checks.py is "the only file most people need to edit"
    and health.py "should rarely need changing". That is true right up until
    your estate is not that estate: the domain-specific probes live in the
    runner, not in the pluggable file. That is the finding, and the fix belongs
    upstream rather than in every fork.

    What is kept exactly: the self-freshness probe, including its 90-minute
    threshold and its warn-level severity, because a stalled loop genuinely
    cannot notice that it stalled and the next run has to judge it.
    """
    out = []

    beat = HOME / "state" / "last_run.json"
    age_m = None
    if beat.exists():
        try:
            prev = json.loads(beat.read_text(encoding="utf-8"))
            age_m = (datetime.now(timezone.utc) - datetime.fromisoformat(
                prev["at"].replace("Z", "+00:00"))).total_seconds() / 60
        except Exception:
            age_m = None
    out.append(C.ok("w_sentinel_fresh", "first run" if age_m is None
                    else "last tick %.0fm ago" % age_m)
               if age_m is None or age_m < 90
               else C.fail("w_sentinel_fresh", "last tick %.0fm ago" % age_m,
                           critical=False))
    return out


def main():
    results = []
    for fn in C.all_checks():
        try:
            r = fn()
            results.append(r if isinstance(r, dict) else
                           C.fail(fn.__name__, "check returned a non-result", critical=False))
        except Exception as e:
            # a check that throws is a broken check, not a broken platform
            results.append(C.fail(fn.__name__,
                                  f"check raised {type(e).__name__}: {e}", critical=False))
    results += probe_watchers()

    failed = [c for c in results if not c["ok"]]
    crit = [c for c in failed if c["severity"] == C.CRITICAL]

    print(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "critical" if crit else ("degraded" if failed else "healthy"),
        "checks": results,
        "failed": [c["id"] for c in failed],
        "critical": [c["id"] for c in crit],
        "summary": "; ".join(f"{c['id']}: {c['detail']}" for c in failed)
                   or "all checks passing",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
