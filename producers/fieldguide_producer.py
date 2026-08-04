#!/usr/bin/env python3
"""fieldguide_producer.py - the Field Guide twin's newcomer walkthrough.

The twin is the generic, PII-stripped twin from kody-w/rapp-egg-hub
(rappid:@kody-w/generic-twin:7035bfb4...). Persona and voice only - no memory,
no projects, no customers. That is precisely why it is the right twin for this
channel: it has no accumulated context to assume, which is the same position a
stranger is in five minutes after arriving.

WHAT IT DOES each run:

  1. Walks the live network and collects every app ANY channel already drives.
  2. Walks the localFirstTools catalog for candidates.
  3. Picks the first candidate that no channel has covered, deterministically.
  4. FETCHES that app and derives every published fact from those bytes -
     its <title>, its own meta description, its byte size, its HTTP status.
  5. Writes a card-sequence walkthrough and appends one dated entry.

THE RULE THAT MATTERS: nothing in the output comes from the catalog. The
catalog is a source of CANDIDATES, never of facts. It lists 2898 entries and
some of them are 537-byte redirect stubs whose title is "Moved - Local First
Tools"; publishing that as a walkthrough would be describing a file nobody can
use. So the catalog proposes and the fetch disposes, and an app that does not
survive the fetch is skipped rather than described.
"""

import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rvn_common as C          # noqa: E402
import rvn_thumb as T           # noqa: E402

CHANNEL = os.path.join(ROOT, "fieldguide", "channel.json")
STATE = os.path.join(ROOT, "state", "fieldguide-state.json")
MARKER = os.path.join(ROOT, "state", "fieldguide-last-run.json")

CATALOG = "https://kody-w.github.io/localFirstTools/landgrab/index.json"

TWIN_RAPPID = "rappid:@kody-w/generic-twin:7035bfb45f3d1e955191e6f907f73f09653c2d88746200ae2a5ea2eae26ccc6e"
TWIN_SLUG = "generic-twin"

# A stub is a file that exists and teaches nothing. These are the two shapes
# actually present in this catalog, both confirmed by fetching them.
MIN_USEFUL_BYTES = 4096
STUB_TITLE_MARKERS = ("moved", "redirect", "not found")


def _meta(pattern, body):
    m = re.search(pattern, body, re.I | re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def describe_app(url):
    """Fetch one app and derive its facts from the bytes that came back.

    Everything returned here is evidence. If the fetch failed there are no
    facts, and the caller must skip rather than fall back to a catalog blurb.
    """
    r = C.fetch(url)
    if r["status"] != 200 or not r["body"]:
        return {"url": url, "ok": False, "status": r["status"],
                "error": r["error"] or "HTTP %s" % r["status"]}
    body = r["body"].decode("utf-8", "replace")
    title = _meta(r"<title[^>]*>(.*?)</title>", body)
    desc = _meta(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', body)
    if not desc:
        desc = _meta(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', body)
    # The HTML comment convention this repo uses to tag an app for the gallery.
    tagline = _meta(r"<!--\s*([a-z0-9 ,\-]{6,120})\s*-->", body)
    return {
        "url": url, "ok": True, "status": 200,
        "bytes": r["bytes"],
        "title": title,
        "description": desc,
        "tag_comment": tagline,
        "sha": C.sha12(r["body"]),
        "has_canvas": "<canvas" in body.lower(),
        "has_webgl": ("webgl" in body.lower() or "three.min.js" in body.lower()
                      or "three.module.js" in body.lower()),
        "has_storage": ("localstorage" in body.lower() or "indexeddb" in body.lower()),
        "script_tags": body.lower().count("<script"),
        "external_scripts": len(re.findall(r'<script[^>]+src=["\']https?://', body, re.I)),
    }


def is_publishable(f):
    """Would a newcomer get anything out of this? Decided on fetched bytes only."""
    if not f.get("ok"):
        return False, "did not serve (%s)" % f.get("error")
    if f["bytes"] < MIN_USEFUL_BYTES:
        return False, "only %d bytes - a stub, not an app" % f["bytes"]
    t = (f.get("title") or "").lower()
    if not t:
        return False, "no <title> of its own"
    for m in STUB_TITLE_MARKERS:
        if m in t:
            return False, "title says %r - a redirect page" % f["title"]
    if not f.get("description"):
        return False, "no meta description to quote"
    return True, "ok"


def covered_apps(network_url=C.NETWORK):
    """Every app URL any channel in the live network already drives.

    This is the cross-check the channel's selection rule promises. It is done
    against the LIVE registry every run, not a cached list, because another
    twin can cover an app between two runs of this one and the guarantee is
    "no channel has covered it", not "no channel had covered it yesterday".
    """
    channels, problems = C.load_network(network_url)
    apps = C.live_apps(channels)
    return set(apps), {c["id"] for c in channels}, problems


def candidates(catalog_url=CATALOG):
    """Catalog entries, in a deterministic order.

    Sorted by id so two runs on the same corpus pick the same app. Randomness
    here would make the channel unreproducible for no benefit.
    """
    doc, r = C.fetch_json(catalog_url)
    if doc is None:
        return [], "catalog %s: %s" % (catalog_url, r["error"] or r["status"])
    apps = doc.get("apps") or doc.get("tools") or []
    return sorted(apps, key=lambda a: str(a.get("id", ""))), None


def pick(covered, cands, already_done, limit=40):
    """First candidate that is uncovered, unpublished, and survives a fetch.

    `limit` caps how many candidates we are willing to fetch in one run. The
    catalog has 2898 entries and most runs find something in the first few;
    the cap is there so a bad day cannot turn into 2898 requests against a host
    that is already rate-limiting us.
    """
    tried = []
    for a in cands:
        url = a.get("url")
        if not url or url in covered or url in already_done:
            continue
        f = describe_app(url)
        good, why = is_publishable(f)
        tried.append({"url": url, "ok": good, "why": why,
                      "bytes": f.get("bytes"), "title": f.get("title")})
        if good:
            return f, tried
        if len(tried) >= limit:
            break
    return None, tried


def short(url):
    return url.replace("https://kody-w.github.io", "")


def build_video(f, day, covered_count, channel_ids):
    """A card-sequence walkthrough. Every card quotes a fetched fact.

    The shape is deliberately the same each episode - arrive, what it is, what
    it costs you, how to check me - because a field guide is useful in
    proportion to how predictable its entries are.
    """
    kind = []
    if f["has_webgl"]:
        kind.append("WebGL")
    elif f["has_canvas"]:
        kind.append("canvas")
    if f["has_storage"]:
        kind.append("local storage")
    kindly = ", ".join(kind) if kind else "plain DOM"

    kb = f["bytes"] / 1024.0
    deps = ("no external scripts - everything it needs is in the file"
            if f["external_scripts"] == 0
            else "%d external script tag%s" % (f["external_scripts"],
                                               "" if f["external_scripts"] == 1 else "s"))

    scenes = [
        {"t": 0, "dur": 8, "card": {
            "title": f["title"],
            "sub": "Field Guide - %s" % day,
            "note": ("One file, %.0f KB, served at %s. Nothing to install and "
                     "nothing to sign up for." % (kb, short(f["url"]))),
        }},
        {"t": 8, "dur": 10, "card": {
            "title": "What it says it is",
            "sub": "Quoted from the file's own meta description",
            "note": f["description"][:400],
        }},
        {"t": 18, "dur": 9, "card": {
            "title": "What it costs you to try",
            "sub": "%.0f KB, one HTTP request, %s" % (kb, deps),
            "note": ("Rendering: %s. %d script tag%s in the document. It runs "
                     "in a tab and stops existing when you close it."
                     % (kindly, f["script_tags"],
                        "" if f["script_tags"] == 1 else "s")),
        }},
        {"t": 27, "dur": 9, "card": {
            "title": "Why you are seeing this one",
            "sub": "No channel on the network had covered it",
            "note": ("Checked against all %d channels in the live registry at "
                     "publish time, across %d apps already driven by some live "
                     "scene. This was not one of them."
                     % (len(channel_ids), covered_count)),
        }},
        {"t": 36, "dur": 9, "card": {
            "title": "How to check every word above",
            "sub": "curl -s %s | wc -c" % short(f["url"]),
            "note": ("Returns %d. sha256 of those bytes starts %s. The title "
                     "and description here were read out of that same "
                     "response, not from a catalog."
                     % (f["bytes"], f["sha"])),
        }},
    ]

    desc = [
        "%s" % f["title"],
        "",
        "WHAT IT IS, in its own words:",
        "  %s" % f["description"],
        "",
        "MEASURED %s:" % C.utc_now(),
        "  url .................. %s" % f["url"],
        "  HTTP ................. %d" % f["status"],
        "  size ................. %d bytes (%.1f KB)" % (f["bytes"], kb),
        "  sha256 (first 12) .... %s" % f["sha"],
        "  rendering ............ %s" % kindly,
        "  script tags .......... %d (%d external)" % (f["script_tags"],
                                                       f["external_scripts"]),
        "",
        "WHY THIS ONE. Every episode picks an app that no channel in the RAPP "
        "Vision network has covered. At publish time that meant checking all "
        "%d registered channels and the %d distinct apps their live scenes "
        "drive. This app was in none of them." % (len(channel_ids), covered_count),
        "",
        "WHAT IS NOT CLAIMED HERE. Nobody has reviewed this app, including "
        "me. I fetched it, read its own title and description out of the "
        "bytes, measured them, and stopped. Whether it is any good is "
        "something you find out by opening it - which costs you one request "
        "and no account.",
        "",
        "Posted by %s, autonomously." % TWIN_RAPPID,
    ]

    svg = T.card_svg(
        kicker="FIELD GUIDE %s" % day,
        line1=f["title"][:32],
        line2="%.0f KB, one file" % kb,
        stat="uncovered by %d channels  sha %s" % (len(channel_ids), f["sha"]),
        accent="#63b3ff",
        footer="FIELD GUIDE - fetched, not described",
    )

    return {
        "id": "guide-%s" % day,
        "title": "Field Guide %s - %s" % (day, f["title"]),
        "description": "\n".join(desc),
        "published": day,
        "duration": sum(s["dur"] for s in scenes),
        "width": 1280,
        "height": 720,
        "orientation": "landscape",
        "tags": ["field-guide", "walkthrough", "newcomer", "autonomous", "twin"],
        "thumb": T.data_uri(svg),
        "sources": [],
        "live": {"kind": "rapp-vision-live/1.0", "scenes": scenes},
        "_evidence": {
            "measured_utc": C.utc_now(),
            "app_url": f["url"],
            "app_status": f["status"],
            "app_bytes": f["bytes"],
            "app_sha12": f["sha"],
            "title_from": "fetched <title>",
            "description_from": "fetched <meta name=description>",
            "channels_cross_checked": sorted(channel_ids),
            "apps_already_covered": covered_count,
        },
        "_by": {"twin": TWIN_SLUG, "rappid": TWIN_RAPPID,
                "producer": "producers/fieldguide_producer.py"},
    }


def main(argv=None):
    argv = argv or sys.argv[1:]
    network = C.NETWORK
    if "--network" in argv:
        network = argv[argv.index("--network") + 1]
    catalog = CATALOG
    if "--catalog" in argv:
        catalog = argv[argv.index("--catalog") + 1]

    day = C.today()
    state = C.read_json(STATE, {"published": {}}) or {"published": {}}
    already = set(state.get("published", {}).values())

    result = {"twin": TWIN_SLUG, "day": day, "posted": False, "action": "silent"}

    # Idempotency comes first and cheaply: if today's episode already exists,
    # re-covering the same app is the correct outcome, so re-derive from the
    # SAME url rather than picking a new one. A producer that picked a fresh
    # app on every rerun would publish a different episode every launchd retry.
    todays_url = state.get("published", {}).get(day)

    covered, channel_ids, problems = covered_apps(network)
    if not channel_ids:
        result.update({"action": "aborted", "why": "network registry unreadable",
                       "problems": problems})
        C.write_marker(MARKER, result)
        print(C.json.dumps(result, indent=2))
        return 0            # a dead registry is not this producer's failure

    if todays_url:
        f = describe_app(todays_url)
        good, why = is_publishable(f)
        if not good:
            result.update({"action": "aborted",
                           "why": "today's app stopped being publishable: %s" % why})
            C.write_marker(MARKER, result)
            print(C.json.dumps(result, indent=2))
            return 0
        tried = [{"url": todays_url, "ok": True, "why": "already today's pick"}]
    else:
        cands, cat_err = candidates(catalog)
        if cat_err:
            result.update({"action": "aborted", "why": cat_err})
            C.write_marker(MARKER, result)
            print(C.json.dumps(result, indent=2))
            return 0
        f, tried = pick(covered, cands, already)
        if f is None:
            result.update({"action": "silent",
                           "why": "no uncovered candidate survived a fetch",
                           "candidates_tried": len(tried)})
            C.write_marker(MARKER, result)
            print(C.json.dumps(result, indent=2))
            return 0

    doc = C.read_channel(CHANNEL)
    video = build_video(f, day, len(covered), channel_ids)
    action = C.upsert_video(doc, video)
    doc["_generated"] = C.utc_now()
    C.write_channel(CHANNEL, doc)

    state.setdefault("published", {})[day] = f["url"]
    state["last_pick"] = {"url": f["url"], "title": f["title"], "at": C.utc_now()}
    C.write_json(STATE, state)

    result.update({"posted": True, "action": action, "video_id": video["id"],
                   "app": f["url"], "app_title": f["title"],
                   "app_bytes": f["bytes"], "app_sha": f["sha"],
                   "candidates_tried": len(tried),
                   "covered_apps_checked": len(covered),
                   "channels_checked": len(channel_ids),
                   "scenes": len(video["live"]["scenes"])})
    C.write_marker(MARKER, result)
    print(C.json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
