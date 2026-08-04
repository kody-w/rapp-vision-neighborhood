#!/usr/bin/env python3
"""tumbler_producer.py - the Rock Tumbler twin's daily tumble report.

The twin is the harsh-critic loop from kody-w/rapp-egg-hub
(rappid:@kody-w/rock-tumbler:933e7eaa...). Its whole method is: an agent's
report of success is not evidence of success, so measure the thing from
outside and hand back a number instead of an adjective. This producer is that
method pointed at the RAPP Vision network itself, once a day.

WHAT IT MEASURES, and why each one earns its place:

  * app hash gate - every app any live scene drives, fetched and sha256'd,
    compared against yesterday. This is daily_shred.py's pattern (v1: the hash
    gate). A changed hash means the software under a published video moved,
    which is the moment a live replay silently starts demonstrating something
    other than what its description claims.
  * media range probes - every source file, asked for bytes=0-1023. 206 is the
    pass. A 200 here means the host ignored the Range header, which is the
    difference between a viewer seeking and a viewer downloading 27MB to watch
    ten seconds.
  * channel reachability - a channel listed in the registry that does not
    parse is invisible to every viewer while looking fine in git.

FIRE ON DIFFERENCE. On a day when nothing moved, this posts nothing. That is
not laziness, it is the honest signal: a daily report that always has content
trains you to stop reading it, and then the one day it matters you skim past
it. Silence is still RECORDED - the run marker is written every single run, so
the sentinel can tell "chose not to post" from "died".
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rvn_common as C          # noqa: E402
import rvn_thumb as T           # noqa: E402

CHANNEL = os.path.join(ROOT, "tumbler", "channel.json")
STATE = os.path.join(ROOT, "state", "tumbler-state.json")
MARKER = os.path.join(ROOT, "state", "tumbler-last-run.json")

# The twin's identity, verbatim from its egg-hub card. Not re-derived, not
# re-minted: this rappid is the twin's name and only the hub gets to say it.
TWIN_RAPPID = "rappid:@kody-w/rock-tumbler:933e7eaaa4111bc9b1285b382780102718d07b87c3ada322d2ea3a2a85c28504"
TWIN_SLUG = "rock-tumbler"


def measure(network_url=C.NETWORK):
    """Gather the whole day's evidence. Returns a plain dict of facts."""
    channels, problems = C.load_network(network_url)
    apps = C.live_apps(channels)
    media = C.media_sources(channels)

    app_rows = []
    for url in sorted(apps):
        r = C.fetch(url)
        app_rows.append({
            "url": url,
            "status": r["status"],
            "sha": C.sha12(r["body"]) if r["status"] == 200 else None,
            "bytes": r["bytes"],
            "driven_by": sorted(set(apps[url])),
        })

    media_rows = []
    for url in sorted(media):
        p = C.range_probe(url)
        media_rows.append({"url": url, "status": p["status"],
                           "content_range": p.get("content_range", ""),
                           "offered_by": sorted(set(media[url]))})

    videos = sum(len(c["doc"].get("videos", [])) for c in channels)
    live_videos = sum(1 for c in channels for v in c["doc"].get("videos", [])
                      if v.get("live"))
    return {
        "utc": C.utc_now(),
        "channels": len(channels),
        "channel_ids": [c["id"] for c in channels],
        "unreachable_channels": problems,
        "videos": videos,
        "live_videos": live_videos,
        "apps": app_rows,
        "media": media_rows,
    }


def diff_against_state(facts, state):
    """The gate. What actually moved since the last time we looked?"""
    prev_app = (state or {}).get("app_sha", {})
    prev_media = (state or {}).get("media_status", {})

    changed, new, vanished, dead = [], [], [], []
    for row in facts["apps"]:
        u = row["url"]
        if row["status"] != 200:
            dead.append(row)
            continue
        was = prev_app.get(u)
        if was is None:
            new.append(row)
        elif was != row["sha"]:
            changed.append(dict(row, was=was))
    for u in prev_app:
        if u not in {r["url"] for r in facts["apps"]}:
            vanished.append(u)

    media_not_206 = []
    for row in facts["media"]:
        was = prev_media.get(row["url"])
        if row["status"] != 206:
            media_not_206.append(dict(row, was=was))

    return {"changed": changed, "new": new, "vanished": vanished,
            "dead_apps": dead, "media_not_206": media_not_206}


def is_newsworthy(delta, first_run):
    """First run has no yesterday, so the baseline itself is the news.

    After that: only publish when something moved. `new` counts because an app
    appearing under a live scene is a real event - a channel started driving
    software this report had never hashed.
    """
    if first_run:
        return True
    return bool(delta["changed"] or delta["new"] or delta["vanished"]
                or delta["dead_apps"] or delta["media_not_206"])


def short(url):
    return url.replace("https://kody-w.github.io", "")


def build_video(facts, delta, first_run):
    """Turn the measurements into one card-only live video.

    Every sentence below is bound to a number that came out of `facts`. The
    voice is the twin's - blunt, numeric, no adjective doing work a figure
    should do - but the constraint is mechanical: if a claim cannot cite a
    field of `facts`, it does not go in.
    """
    day = C.today()
    ok_apps = [r for r in facts["apps"] if r["status"] == 200]
    ok_media = [r for r in facts["media"] if r["status"] == 206]
    bad_media = [r for r in facts["media"] if r["status"] != 206]

    headline = ("Baseline: %d apps hashed, %d media files probed"
                % (len(ok_apps), len(facts["media"])))
    if not first_run:
        bits = []
        if delta["changed"]:
            bits.append("%d app%s changed" % (len(delta["changed"]),
                                              "" if len(delta["changed"]) == 1 else "s"))
        if delta["new"]:
            bits.append("%d new" % len(delta["new"]))
        if delta["vanished"]:
            bits.append("%d gone" % len(delta["vanished"]))
        if delta["dead_apps"]:
            bits.append("%d not serving" % len(delta["dead_apps"]))
        if delta["media_not_206"]:
            bits.append("%d media not 206" % len(delta["media_not_206"]))
        headline = ", ".join(bits) if bits else "nothing moved"

    scenes = [{
        "t": 0, "dur": 8,
        "card": {
            "title": headline,
            "sub": "Tumble report - %s" % day,
            "note": ("%d channels, %d videos, %d of them live. Measured from "
                     "outside, over HTTP, just now."
                     % (facts["channels"], facts["videos"], facts["live_videos"])),
        },
    }]

    # -- what changed under the videos
    if delta["changed"]:
        lines = []
        for r in delta["changed"][:6]:
            lines.append("%s  %s -> %s" % (short(r["url"]), r["was"], r["sha"]))
        scenes.append({"t": 8, "dur": 10, "card": {
            "title": "The software moved under a published video",
            "sub": "%d app%s changed hash since the last tumble"
                   % (len(delta["changed"]), "" if len(delta["changed"]) == 1 else "s"),
            "note": " | ".join(lines),
        }})
    elif not first_run:
        scenes.append({"t": 8, "dur": 8, "card": {
            "title": "No app changed hash",
            "sub": "%d apps re-fetched and re-hashed" % len(ok_apps),
            "note": ("Every live scene still drives the same bytes it drove "
                     "yesterday. That is the claim, and it is the only one "
                     "this report is willing to make about them."),
        }})

    if delta["new"]:
        scenes.append({"t": len(scenes) * 9, "dur": 8, "card": {
            "title": "New software under a live scene",
            "sub": "%d app%s hashed for the first time"
                   % (len(delta["new"]), "" if len(delta["new"]) == 1 else "s"),
            "note": " | ".join(short(r["url"]) + "  " + str(r["sha"])
                               for r in delta["new"][:6]),
        }})

    # -- what is broken. The part of the report that has to hurt.
    dead = delta["dead_apps"]
    if dead:
        scenes.append({"t": len(scenes) * 9, "dur": 9, "card": {
            "title": "A live scene points at something that does not serve",
            "sub": "%d app%s did not return 200"
                   % (len(dead), "" if len(dead) == 1 else "s"),
            "note": " | ".join("%s HTTP %s (driven by %s)"
                               % (short(r["url"]), r["status"], ", ".join(r["driven_by"]))
                               for r in dead[:5]),
        }})

    if bad_media:
        scenes.append({"t": len(scenes) * 9, "dur": 9, "card": {
            "title": "Media that will not seek",
            "sub": "%d of %d source files did not answer 206"
                   % (len(bad_media), len(facts["media"])),
            "note": " | ".join("%s HTTP %s" % (short(r["url"]), r["status"])
                               for r in bad_media[:5]),
        }})
    else:
        scenes.append({"t": len(scenes) * 9, "dur": 8, "card": {
            "title": "Every media file answers a range request",
            "sub": "%d/%d returned 206 to bytes=0-1023" % (len(ok_media), len(facts["media"])),
            "note": ("206 is the pass, not 200. A 200 to a Range header means "
                     "the host handed over the whole file and the viewer "
                     "cannot seek."),
        }})

    if facts["unreachable_channels"]:
        scenes.append({"t": len(scenes) * 9, "dur": 8, "card": {
            "title": "A registered channel did not answer",
            "sub": "%d of %d channel.json files failed to load"
                   % (len(facts["unreachable_channels"]),
                      facts["channels"] + len(facts["unreachable_channels"])),
            "note": " | ".join(facts["unreachable_channels"][:5]),
        }})

    scenes.append({"t": len(scenes) * 9, "dur": 8, "card": {
        "title": "How to disbelieve this report",
        "sub": "Every number above is one curl away",
        "note": ("Fetch channels.json, walk it to each channel.json, sha256 "
                 "every app a live scene names, and ask each media file for "
                 "bytes=0-1023. If your numbers differ from these, mine are "
                 "wrong - say so."),
    }})

    total = sum(s["dur"] for s in scenes)
    # Rewrite the t offsets from the durations so they are contiguous. Computing
    # them inline above drifted the moment a section was conditional.
    t = 0
    for s in scenes:
        s["t"] = t
        t += s["dur"]

    desc = build_description(facts, delta, first_run, ok_apps, ok_media, bad_media)
    accent = "#ff6b6b" if (dead or bad_media or facts["unreachable_channels"]) else "#5ee0a0"
    svg = T.card_svg(
        kicker="TUMBLE %s" % day,
        line1=headline[:34],
        line2=("%d channels / %d videos" % (facts["channels"], facts["videos"])),
        stat="%d apps hashed  %d/%d media 206" % (len(ok_apps), len(ok_media), len(facts["media"])),
        accent=accent,
        footer="ROCK TUMBLER - measured, not reported",
    )

    return {
        "id": "tumble-%s" % day,
        "title": "Tumble %s - %s" % (day, headline),
        "description": desc,
        "published": day,
        "duration": total,
        "width": 1280,
        "height": 720,
        "orientation": "landscape",
        "tags": ["tumble", "measurement", "hash-gate", "autonomous", "twin"],
        "thumb": T.data_uri(svg),
        "sources": [],
        "live": {"kind": "rapp-vision-live/1.0", "scenes": scenes},
        "_evidence": {
            "measured_utc": facts["utc"],
            "apps_hashed": len(ok_apps),
            "apps_not_serving": len(dead),
            "media_206": len(ok_media),
            "media_total": len(facts["media"]),
            "channels_ok": facts["channels"],
            "channels_unreachable": facts["unreachable_channels"],
            "changed": [{"url": r["url"], "was": r["was"], "now": r["sha"]}
                        for r in delta["changed"]],
            "new": [r["url"] for r in delta["new"]],
            "vanished": delta["vanished"],
        },
        "_by": {"twin": TWIN_SLUG, "rappid": TWIN_RAPPID,
                "producer": "producers/tumbler_producer.py"},
    }


def build_description(facts, delta, first_run, ok_apps, ok_media, bad_media):
    """The text under the player. Numbers first, hedging never."""
    L = []
    if first_run:
        L.append("First tumble. There is no yesterday to compare against, so "
                 "this run establishes the baseline every later report is a "
                 "diff from - and says so rather than dressing a baseline up "
                 "as a finding.")
    L.append("MEASURED %s, from outside, over HTTP." % facts["utc"])
    L.append("")
    L.append("  channels reachable ......... %d" % facts["channels"])
    L.append("  videos published ........... %d (%d live)"
             % (facts["videos"], facts["live_videos"]))
    L.append("  apps behind live scenes .... %d hashed, %d not serving"
             % (len(ok_apps), len(facts["apps"]) - len(ok_apps)))
    L.append("  media files range-probed ... %d/%d answered 206"
             % (len(ok_media), len(facts["media"])))
    L.append("")

    if delta["changed"]:
        L.append("CHANGED SINCE THE LAST TUMBLE - the software under a "
                 "published video is not the software that was there:")
        for r in delta["changed"]:
            L.append("  %s" % short(r["url"]))
            L.append("      %s -> %s   (driven by %s)"
                     % (r["was"], r["sha"], ", ".join(r["driven_by"])))
        L.append("")
        L.append("A changed hash is not a defect. It is a notice: the live "
                 "replay above those entries now demonstrates different bytes "
                 "than the description was written against.")
        L.append("")
    elif not first_run:
        L.append("NOTHING CHANGED. %d apps re-fetched, every sha256 identical "
                 "to the previous tumble." % len(ok_apps))
        L.append("")

    if delta["new"]:
        L.append("NEW UNDER A LIVE SCENE (never hashed before):")
        for r in delta["new"]:
            L.append("  %s  %s  %d bytes" % (short(r["url"]), r["sha"], r["bytes"]))
        L.append("")

    if delta["vanished"]:
        L.append("NO LONGER DRIVEN BY ANY SCENE (was hashed before, is not now):")
        for u in delta["vanished"]:
            L.append("  %s" % short(u))
        L.append("")

    dead = delta["dead_apps"]
    if dead:
        L.append("NOT SERVING - a live scene points at this and it does not "
                 "return 200. Every viewer who reaches that entry sees an "
                 "empty frame:")
        for r in dead:
            L.append("  HTTP %-3s %s   (driven by %s)"
                     % (r["status"], short(r["url"]), ", ".join(r["driven_by"])))
        L.append("")

    if bad_media:
        L.append("WILL NOT SEEK - asked for bytes=0-1023 and did not answer 206:")
        for r in bad_media:
            L.append("  HTTP %-3s %s   (offered by %s)"
                     % (r["status"], short(r["url"]), ", ".join(r["offered_by"])))
        L.append("")

    if facts["unreachable_channels"]:
        L.append("REGISTERED BUT UNREADABLE - listed in channels.json and did "
                 "not parse. Invisible to every viewer while looking fine in git:")
        for p in facts["unreachable_channels"]:
            L.append("  %s" % p)
        L.append("")

    L.append("METHOD. Walk channels.json to every channel.json. For each live "
             "scene, fetch the app it drives and sha256 the bytes. For each "
             "source file, send Range: bytes=0-1023 and record the status. "
             "Compare against the previous run's state file. No model is "
             "involved in any of it - if deciding whether something broke "
             "needed one, the check would not be sharp enough yet.")
    L.append("")
    L.append("Posted by %s, autonomously. On a day nothing moves this channel "
             "publishes nothing at all." % TWIN_RAPPID)
    return "\n".join(L)


def main(argv=None):
    argv = argv or sys.argv[1:]
    network = C.NETWORK
    if "--network" in argv:
        network = argv[argv.index("--network") + 1]

    state = C.read_json(STATE)
    first_run = state is None

    facts = measure(network)
    delta = diff_against_state(facts, state)
    newsworthy = is_newsworthy(delta, first_run)

    # The marker is written on EVERY path, including the silent one. A day the
    # twin chose not to speak and a day the twin was dead are indistinguishable
    # from outside unless the choice is recorded.
    result = {"twin": TWIN_SLUG, "day": C.today(), "posted": False,
              "action": "silent", "newsworthy": newsworthy,
              "channels": facts["channels"], "apps": len(facts["apps"]),
              "media": len(facts["media"]),
              "changed": len(delta["changed"]), "new": len(delta["new"]),
              "dead_apps": len(delta["dead_apps"]),
              "media_not_206": len(delta["media_not_206"])}

    if newsworthy:
        doc = C.read_channel(CHANNEL)
        video = build_video(facts, delta, first_run)
        action = C.upsert_video(doc, video)
        doc["_generated"] = C.utc_now()
        C.write_channel(CHANNEL, doc)
        result.update({"posted": True, "action": action, "video_id": video["id"],
                       "scenes": len(video["live"]["scenes"]),
                       "duration": video["duration"]})

    # Advance the baseline only after the entry has landed, exactly as
    # daily_shred.py does - otherwise a crash between measuring and writing
    # swallows a change forever.
    C.write_json(STATE, {
        "app_sha": {r["url"]: r["sha"] for r in facts["apps"] if r["status"] == 200},
        "media_status": {r["url"]: r["status"] for r in facts["media"]},
        "measured": facts["utc"],
    })
    C.write_marker(MARKER, result)

    print(C.json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
