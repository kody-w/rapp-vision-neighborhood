#!/usr/bin/env python3
"""rvn_common.py - the shared floor under both twin producers.

Everything here serves one rule: a twin may only publish a fact it fetched.
There is no template that "describes" an app and no counter incremented on
faith. If the network did not answer, the producer says so or says nothing -
silence is a legal outcome and a lie is not.

Two things are load-bearing and worth the words:

  * fetch() returns the BYTES it actually received alongside the status, so
    every downstream fact (a title, a size, a hash) is derived from the same
    response that proved the URL was up. Deriving size from a catalog entry
    and liveness from a separate HEAD is how you publish a size for a file
    that 404s.

  * upsert_video() is keyed on the video id, and the ids are datestamped.
    That is the whole idempotency story: a second run the same day rewrites
    the day's entry in place. It is deliberately NOT "append if the last
    entry is older than 24h" - clocks, reruns and retries all break that, and
    a channel that grows a duplicate every time launchd double-fires is a
    channel nobody trusts.

Stdlib only, and written to run on 3.9 as well as 3.11: rapp-sentinel's
launchd templates hardcode /usr/bin/python3, which on this machine is 3.9.6.
Code that only runs under the interpreter you tested by hand dies at 3am.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "rapp-vision-neighborhood/1.0 (+twin producer)"}
NETWORK = "https://kody-w.github.io/rapp-vision/channels.json"
TIMEOUT = 30


# -- time --------------------------------------------------------------------

def utc_now():
    """Fixed-form UTC - millisecond precision, Z suffix.

    Matched to rapp/1 exactly, because these strings end up next to frames the
    sentinel wrote and a mixed-format timeline is a debugging tax forever.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def hours_since(iso):
    """Age in hours of an ISO timestamp, tolerant of both forms we emit."""
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() / 3600.0
    except Exception:
        return None


# -- http --------------------------------------------------------------------

def fetch(url, timeout=TIMEOUT, retries=2, headers=None):
    """GET a URL. Returns {url, status, body, bytes, error, ctype}.

    Never raises: a producer that dies on one dead link publishes nothing at
    all, which is worse than publishing the rest and naming the dead link.
    Retries exist because this estate is measured from a laptop routinely at
    load average 300, and a single timeout is not evidence.
    """
    h = dict(UA)
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return {"url": url, "status": r.status, "body": raw,
                        "bytes": len(raw), "error": None,
                        "ctype": r.headers.get("Content-Type", "")}
        except urllib.error.HTTPError as e:
            # An HTTP error is an answer, not a failure to reach. Don't retry.
            return {"url": url, "status": e.code, "body": b"", "bytes": 0,
                    "error": "HTTP %d" % e.code, "ctype": ""}
        except Exception as e:
            last = type(e).__name__
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return {"url": url, "status": 0, "body": b"", "bytes": 0,
            "error": last or "unreachable", "ctype": ""}


def fetch_json(url, **kw):
    r = fetch(url, **kw)
    if r["status"] != 200:
        return None, r
    try:
        return json.loads(r["body"].decode("utf-8")), r
    except Exception as e:
        r["error"] = "unparseable JSON: %s" % type(e).__name__
        return None, r


# Statuses that mean "the host is having a moment", not "this file is broken".
# A twin that publishes an accusation off one sample is exactly the failure the
# rock-tumbler method exists to prevent - and it caught itself doing it: the
# first two runs of this producer flagged rock-tumbler-reel.mp4 as unseekable
# off a single 503, and five manual probes immediately after returned 206 every
# time. One sample is not evidence. It never was.
TRANSIENT = (429, 500, 502, 503, 504, 0)


def range_probe(url, timeout=TIMEOUT, retries=2):
    """Ask for the first KB and report the status.

    206 is the claim worth making about a media file: the host will serve a
    range, which is what lets a viewer seek instead of downloading the whole
    file first. A 200 here is NOT a pass - it means the server ignored the
    Range header and handed over everything.

    Retries on transient statuses only. A 404 is an answer and returns
    immediately; a 503 is the host declining to answer and is asked again,
    because publishing "this file will not seek" off one rate-limited response
    is a false accusation with a real reader.
    """
    req = urllib.request.Request(url, headers=dict(UA, **{"Range": "bytes=0-1023"}))
    attempts = []
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                attempts.append(r.status)
                if r.status not in TRANSIENT or attempt == retries:
                    return {"url": url, "status": r.status,
                            "content_range": r.headers.get("Content-Range", ""),
                            "error": None, "attempts": attempts}
        except urllib.error.HTTPError as e:
            attempts.append(e.code)
            if e.code not in TRANSIENT or attempt == retries:
                return {"url": url, "status": e.code, "content_range": "",
                        "error": "HTTP %d" % e.code, "attempts": attempts}
        except Exception as e:
            attempts.append(0)
            if attempt == retries:
                return {"url": url, "status": 0, "content_range": "",
                        "error": type(e).__name__, "attempts": attempts}
        time.sleep(1.5 * (attempt + 1))
    return {"url": url, "status": attempts[-1] if attempts else 0,
            "content_range": "", "error": "exhausted", "attempts": attempts}


def sha12(data):
    """First 12 hex of sha256 - the width daily_shred.py already uses.

    Twelve hex is 48 bits. Collisions are not the threat model; the question
    is only "did these bytes change since yesterday", and a short digest keeps
    the state file readable by a human at 3am.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]


# -- the network -------------------------------------------------------------

def load_network(network_url=NETWORK):
    """Walk channels.json -> every channel.json. Returns (channels, problems).

    Problems are returned rather than raised for the same reason fetch() does
    not throw: one unreachable channel must not stop a report about the other
    eight, and "this channel did not answer" is itself a publishable fact.
    """
    reg, r = fetch_json(network_url)
    if reg is None:
        return [], ["registry %s: %s" % (network_url, r["error"] or r["status"])]
    out, problems = [], []
    for entry in reg.get("channels", []):
        cu = urllib.parse.urljoin(network_url, entry["url"])
        doc, rr = fetch_json(cu)
        if doc is None:
            problems.append("%s: %s" % (entry.get("id", "?"),
                                        rr["error"] or "HTTP %s" % rr["status"]))
            continue
        out.append({"id": entry.get("id"), "name": entry.get("name"),
                    "url": cu, "doc": doc})
    return out, problems


def live_apps(channels):
    """Every distinct app URL any live scene in the network drives.

    Absolute-ised against the channel.json that named it, because a scene's
    `app` is relative to its own channel file - the same rule the player uses.
    Getting this wrong silently produces a set of apps that do not exist.
    """
    apps = {}
    for ch in channels:
        for v in ch["doc"].get("videos", []):
            for sc in v.get("live", {}).get("scenes", []):
                a = sc.get("app")
                if not a:
                    continue
                url = urllib.parse.urljoin(ch["url"], a)
                apps.setdefault(url, []).append("%s/%s" % (ch["id"], v["id"]))
    return apps


def media_sources(channels):
    """Every distinct media file the network offers, with who offers it."""
    out = {}
    for ch in channels:
        for v in ch["doc"].get("videos", []):
            for s in v.get("sources", []):
                if not s.get("src"):
                    continue
                url = urllib.parse.urljoin(ch["url"], s["src"])
                out.setdefault(url, []).append("%s/%s" % (ch["id"], v["id"]))
    return out


# -- channel.json read / write -----------------------------------------------

def read_channel(path):
    with open(str(path), "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_channel(path, doc):
    """Write a channel.json in the platform's own style.

    2-space indent and a trailing newline, matching rapp-vision/template.
    ensure_ascii=False so an em-dash stays an em-dash: these files are read by
    people, and an escaped codepoint in a description is a small daily insult.
    """
    write_json(path, doc)


def upsert_video(doc, video):
    """Insert or replace one video by id. Newest first.

    Returns 'added' | 'updated' | 'unchanged'. Newest-first because that is the
    order every other channel in this network uses and the player renders them
    in file order.
    """
    vids = doc.setdefault("videos", [])
    for i, v in enumerate(vids):
        if v.get("id") == video["id"]:
            if v == video:
                return "unchanged"
            vids[i] = video
            return "updated"
    vids.insert(0, video)
    return "added"


# -- run markers -------------------------------------------------------------
# The sentinel's job is to notice a producer that stopped. It cannot read the
# producer's mind, so the producer leaves a timestamp behind on EVERY run -
# including runs where it decided to publish nothing. A silent day and a dead
# job look identical from outside unless the silence is recorded.

def read_json(path, default=None):
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def write_json(path, doc):
    """Atomic write. A half-written channel.json is a dead channel."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, str(path))


def write_marker(path, payload):
    payload = dict(payload)
    payload["at"] = utc_now()
    write_json(path, payload)
    return payload
