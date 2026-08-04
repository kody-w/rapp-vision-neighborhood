#!/usr/bin/env python3
"""checks.py - what this twin's sentinel actually asks, every 15 minutes.

The failure being watched is NOT "is the channel down". A channel that is down
gets noticed in an hour by anyone who opens it. The failure that goes unnoticed
for nineteen days is the one rapp-sentinel's README names: it is up, it serves
200, and it quietly stopped saying anything. An autonomous channel fails that
way by default, because nothing about a dead producer changes the HTTP status
of the file it stopped writing.

So freshness is the invariant here, and every check below is chosen to fail
BEFORE a human would notice, not after:

  a. channel_serving_and_fresh - 200, parses, and the newest entry is < 48h old
  b. producer_ran_recently ..... the run marker is < 26h old
  c. still_registered .......... the network registry still lists this channel
  d. peer_head_advancing ....... the other twin's published head is moving

(a) and (b) are deliberately both here and deliberately different. (a) is the
outcome a viewer sees; (b) is the machinery behind it. A producer can run
faithfully every day and still publish nothing - that is legal, silence is an
honest outcome for the tumbler - so (b) passing while (a) ages is the specific
signature of "working as intended, nothing to report". Both failing together is
a dead job. Only (a) failing means the producer is running and its output is
not landing, which is a different bug with a different fix. Collapsing them
into one check would throw away the ability to tell those three apart.

48h and 26h are not round numbers for their own sake. The producers run daily,
so 26h is one missed run plus two hours of slack for a laptop that was asleep;
48h is two missed runs, the point where a human would want to know.

LEVEL 0. This trial grants observation only - no repair arm, no autonomous
issue filing. A check here reports, and a person decides.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 25
CRITICAL, WARN = "critical", "warn"

_REGISTRY = []

HERE = os.path.dirname(os.path.abspath(__file__))
# sentinel/<twin>/ -> the repo root two levels up. Derived rather than
# hardcoded so an instance can be moved without editing its checks.
REPO = os.path.dirname(os.path.dirname(HERE))

# ---- filled in per instance -------------------------------------------------
TWIN = "__TWIN__"
CHANNEL_ID = "__CHANNEL_ID__"
CHANNEL_PATH = os.path.join(REPO, "__CHANNEL_DIR__", "channel.json")
MARKER_PATH = os.path.join(REPO, "state", "__MARKER__")
CHANNEL_URL = "__CHANNEL_URL__"
NETWORK_URL = "https://kody-w.github.io/rapp-vision/channels.json"
PEER_SLUG = "__PEER_SLUG__"

MAX_ENTRY_AGE_H = 48.0
MAX_PRODUCER_AGE_H = 26.0


def check(fn):
    _REGISTRY.append(fn)
    return fn


def all_checks():
    return list(_REGISTRY)


def ok(cid, detail=""):
    return {"id": cid, "ok": True, "severity": WARN, "detail": detail}


def fail(cid, detail="", critical=True):
    return {"id": cid, "ok": False,
            "severity": CRITICAL if critical else WARN, "detail": detail}


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "rapp-vision-sentinel"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def hours_since(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() / 3600.0
    except Exception:
        return None


def _newest_entry_age_h(doc):
    """Age of the newest video, by the freshest signal the file carries.

    `published` is a date with no time, so a same-day entry reads as up to 24h
    old and the check would flap against a 48h threshold for no reason. The
    producer stamps `_generated` with the real write time, so prefer it and
    fall back to the date only when it is absent.
    """
    gen = hours_since(doc.get("_generated"))
    if gen is not None:
        return gen, "_generated"
    ages = [hours_since(v.get("published")) for v in doc.get("videos", [])]
    ages = [a for a in ages if a is not None]
    return (min(ages), "published") if ages else (None, "none")


# -- (a) the outcome a viewer sees -------------------------------------------

@check
def channel_serving_and_fresh():
    """200 + parses + newest entry younger than 48h.

    All three in one check on purpose: they are the same question asked of the
    same bytes, and splitting them would mean fetching the file three times.
    """
    status, body = _get(CHANNEL_URL)
    if status != 200:
        return fail("channel_serving", "HTTP %s - %s" % (status, CHANNEL_URL))
    try:
        doc = json.loads(body.decode("utf-8"))
    except Exception as e:
        return fail("channel_serving",
                    "200 but does not parse (%s) - invisible to every viewer "
                    "while looking fine in git" % type(e).__name__)
    n = len(doc.get("videos", []))
    if n == 0:
        return fail("channel_fresh", "channel serves and parses but has no entries",
                    critical=False)
    age, src = _newest_entry_age_h(doc)
    if age is None:
        return fail("channel_fresh", "%d entries, none carry a usable date" % n)
    if age < MAX_ENTRY_AGE_H:
        return ok("channel_fresh", "%d entries, newest %.1fh old (by %s)" % (n, age, src))
    return fail("channel_fresh",
                "UP AND SILENT: serving 200, parses, %d entries, newest is %.1fh "
                "old (limit %.0fh). This is the failure that hides - nothing "
                "about it changes the HTTP status." % (n, age, MAX_ENTRY_AGE_H))


# -- (b) the machinery behind it ---------------------------------------------

@check
def producer_ran_recently():
    """The run marker is written on EVERY run, including silent ones.

    That is what makes this check able to tell "chose not to post" from "died".
    A producer that only wrote a marker when it published would be indis-
    tinguishable from a dead one on a quiet day.
    """
    if not os.path.exists(MARKER_PATH):
        return fail("producer_ran", "no run marker at %s - producer has never run"
                    % os.path.basename(MARKER_PATH))
    try:
        with open(MARKER_PATH, "r", encoding="utf-8") as fh:
            m = json.load(fh)
    except Exception as e:
        return fail("producer_ran", "run marker unreadable: %s" % type(e).__name__)
    age = hours_since(m.get("at"))
    if age is None:
        return fail("producer_ran", "run marker has no usable timestamp")
    what = "posted %s" % m.get("action") if m.get("posted") else "ran, chose silence"
    if age < MAX_PRODUCER_AGE_H:
        return ok("producer_ran", "last run %.1fh ago (%s)" % (age, what))
    return fail("producer_ran",
                "last run %.1fh ago (limit %.0fh) - the job behind this channel "
                "has stopped" % (age, MAX_PRODUCER_AGE_H))


# -- (c) still on the network ------------------------------------------------

@check
def still_registered():
    """A channel nobody lists is a channel nobody can find.

    Worth its own check because it fails silently and from the outside: this
    repo can be perfectly healthy while an edit to somebody else's registry
    drops it off the network.
    """
    status, body = _get(NETWORK_URL)
    if status != 200:
        return fail("still_registered", "registry HTTP %s" % status, critical=False)
    try:
        reg = json.loads(body.decode("utf-8"))
    except Exception:
        return fail("still_registered", "registry does not parse", critical=False)
    ids = [c.get("id") for c in reg.get("channels", [])]
    if CHANNEL_ID in ids:
        return ok("still_registered", "listed among %d channels" % len(ids))
    return fail("still_registered",
                "NOT LISTED in the network registry (%d channels: %s)"
                % (len(ids), _id_list(ids)))



def _id_list(ids, cap=8):
    """Render an id list without ever implying a miscount.

    Printing 8 names after the words "9 channels" reads as a bug in the
    counter. If the list is truncated, say so.
    """
    shown = [str(i) for i in ids[:cap]]
    if len(ids) > cap:
        shown.append("+%d more" % (len(ids) - cap))
    return ", ".join(shown)



def _peer_status(url):
    """The HTTP status behind an unreachable peer.

    Upstream's fetch_peer reports only type(e).__name__, so every failure
    arrives as the bare word "HTTPError". A 404 (peer never published, or the
    URL is wrong) and a 503 (peer's host is having a bad minute) demand
    opposite responses, and the alert as written cannot tell them apart.
    This re-probes only to label the failure; peer_roll_call still decides
    reachable / valid / advancing, so upstream semantics are untouched.
    """
    if not url:
        return "no url"
    import urllib.request, urllib.error
    try:
        rq = urllib.request.Request(url, headers={"User-Agent": "rapp-sentinel"})
        with urllib.request.urlopen(rq, timeout=10) as r:
            return "HTTP %s" % r.status
    except urllib.error.HTTPError as e:
        return "HTTP %s" % e.code
    except Exception as e:
        return "no response: %s" % type(e).__name__


# -- (d) the peer ------------------------------------------------------------

@check
def peer_head_advancing():
    """Ask the OTHER twin's sentinel whether it is still moving.

    Delegated to neighborhood.py's own peer_roll_call so the definitions of
    reachable, valid, alive and advancing are the upstream ones and not a
    second opinion that drifts from them.

    Warn-level, not critical: a peer stalling is news about the peer, and this
    sentinel escalating on it would mean two twins waking a human for one
    outage.
    """
    sys.path.insert(0, HERE)
    try:
        import neighborhood as NB
    except Exception as e:
        return fail("peer_head", "neighborhood.py unimportable: %s" % e, critical=False)

    if not NB.peers():
        return ok("peer_head", "no peers configured yet")
    try:
        roll = NB.peer_roll_call()
    except Exception as e:
        return fail("peer_head", "peer roll call raised %s" % type(e).__name__,
                    critical=False)

    bad = []
    for slug, info in roll.items():
        if not info.get("reachable"):
            bad.append("%s unreachable (%s, %s)"
                       % (slug, info.get("detail"), _peer_status(info.get("url"))))
        elif not info.get("valid"):
            bad.append("%s invalid (%s)" % (slug, info.get("detail")))
        elif info.get("advancing") is False:
            bad.append("%s head has not moved since last check" % slug)
    if bad:
        return fail("peer_head", "; ".join(bad), critical=False)
    seen = ", ".join("%s seq-ok age %sm" % (s, i.get("age_minutes"))
                     for s, i in roll.items())
    return ok("peer_head", seen or "no peers")
