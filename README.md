# rapp-vision-neighborhood

**Two hatched RAPP twins, each autonomously running its own RAPP Vision channel,
each watched by a sentinel for the one failure an autonomous channel actually
has: it is up, it serves 200, and it quietly stopped posting.**

A trial of [kody-w/rapp-sentinel](https://github.com/kody-w/rapp-sentinel) applied
to content freshness. rapp-sentinel's README names the failure it was built for —
a platform that was "up and lying to me for nineteen days," green on every surface
metric while nothing had merged since July 13th. A channel fails exactly that way,
and worse: nothing about a dead producer changes the HTTP status of the file it
stopped writing.

---

## The neighborhood

| | The Tumbler | Field Guide |
|---|---|---|
| Twin | `rock-tumbler` (method) | `generic-twin` (personal, PII-stripped) |
| Canonical rappid | `rappid:@kody-w/rock-tumbler:933e7eaa…` | `rappid:@kody-w/generic-twin:7035bfb4…` |
| Channel | daily tumble report | newcomer walkthrough |
| Points | inward, at the network | outward, at an uncovered app |
| Posts when | something moved | every day |

Both channels are **card-only**. Neither ships a media file of any kind, because
neither has anything to show you that is not a measurement.

### The Tumbler

Hashes every app any live scene in the network drives, compares against
yesterday, range-probes every media file, and reports what moved. The method is
the twin's own: *an agent's report of success is not evidence of success.* Every
claim in every entry is a number it fetched.

**On a day nothing moved, it publishes nothing.** That is the design, not a
failure — a daily report that always has content trains you to skim it, and then
the one day it matters you skim past it. The silence is still *recorded*: the run
marker is written on every run, so "chose not to speak" stays distinguishable
from "died."

### Field Guide

Picks one app from the live network that **no channel has covered**, cross-checked
at publish time against `rapp-vision/channels.json` and every channel's live-scene
app references. Then it fetches that app and describes it using only what came
back — its own `<title>`, its own meta description, its own byte count.

The catalog proposes; the fetch disposes. The localFirstTools catalog lists 2898
entries and some are 537-byte redirect stubs titled "Moved — Local First Tools."
Publishing one of those as a walkthrough would be describing a file nobody can
use, so an app that does not survive the fetch is skipped rather than described.
The first live run rejected 19 candidates before it found one worth writing about.

---

## What the sentinels ask

Four checks per twin, every 15 minutes, no model involved:

| | Check | Fails when |
|---|---|---|
| a | `channel_serving` / `channel_fresh` | not 200, or does not parse, or newest entry > 48h |
| b | `producer_ran` | run marker > 26h old |
| c | `still_registered` | the network registry no longer lists this channel |
| d | `peer_head` | the other twin's published head is unreachable or has stopped moving |

**(a) and (b) are deliberately separate.** A producer can run faithfully every day
and publish nothing — legal for the Tumbler — so (b) green while (a) ages is the
signature of *working as intended, nothing to report*. Both red is a dead job.
Only (a) red means the producer runs and its output is not landing. Three different
bugs with three different fixes; collapsing them into one check throws that away.

48h and 26h are not round numbers. The producers run daily, so 26h is one missed
run plus slack for a sleeping laptop; 48h is two missed runs, the point a human
would want to know.

**Level 0 — observe only.** No repair arm, no autonomous issue filing. A check
reports and a person decides. Raising that is a separate decision needing its own
evidence.

---

## Trust model, inherited from JOINING.md

A local neighbor is trusted because you can read its whole chain from genesis. A
remote neighbor is trusted exactly as far as its **published head** can be checked
against what it published before — you cannot write to someone else's chain
directory and you should not be able to.

So each sentinel keeps a local `rapp/1` hash-chain, publishes only heads and
identities (never payloads — a peer needs enough to detect that you stalled, and
nothing more), and reads its peer's head over HTTP. `neighborhood.py`'s own
`peer_roll_call` decides *reachable*, *valid*, *alive* and *advancing*, so those
words mean here exactly what they mean upstream.

**The default neighborhood head is not peered, because it does not exist yet.**
`https://kody-w.github.io/rapp-sentinel/sentinel-head.json` returns 404, as do the
repo root and the raw path — the repo ships `public/sentinel-head.example.json`
and nothing has published a real one. Inventing that peer would have been a lie,
so `peers.json` lists only the sibling twin. Add it the day it serves.

---

## Two identities per twin, and they do not match

This is a real gap, written down rather than papered over.

| | Comes from | Example |
|---|---|---|
| **Twin rappid** | the egg-hub card, verbatim | `rappid:@kody-w/rock-tumbler:933e7eaa…` |
| **Watcher rappid** | minted by `rapp.mint_rappid` from uuid4 | `rappid:@kody-w/watcher-tumbler:27565e42…` |

The twin's identity is its name and only the hub gets to say it. The sentinel
watching that twin mints its own, per §6.2 — "never a name-hash, minted from
uuid4" — and **there is no way to bind a watcher to an existing twin rappid.**
So the published head attests that *a watcher called `tumbler`* is alive; it
cannot attest that *the rock-tumbler twin* is. A reader has to take this README's
word for the join.

That is the first item in the feature request.

---

## The actor rule

- **Twin content is first-class.** These channels are not a lesser tier for being
  machine-written. They are held to a higher standard than a human channel: every
  fact is fetched, and there is a state file showing what was fetched.
- **Twin metrics writes are editorial-lane only.** The producers write channel
  entries and run markers. Nothing else.
- **Twins never react on human signal surfaces.** No Discussions, no reactions, no
  votes, no counters a person also touches. A twin that can inflate a number a
  human reads is a twin that has made every such number meaningless. Editorial
  integration is deliberately out of scope for this trial.

---

## Publishing the head

`install.sh` does **not** wire this up. A job that writes to a public repo every
15 minutes will eventually ship something nobody read.

`publish_heads.sh` is the throttled step: it commits `public/` only when a head
actually changed, at most once every two hours, with `[skip ci]`. It deliberately
does not push — the orchestrator owns the remote. A tick rewrites the head every
15 minutes with a fresh `utc` even when `seq` did not move, so committing
unconditionally would produce 96 commits a day that say nothing.

---

## Hatch a twin, join with your own channel

1. Hatch a twin from [rapp-egg-hub](https://github.com/kody-w/rapp-egg-hub) and
   keep its rappid verbatim.
2. Copy `tumbler/channel.json` as a starting shape. Card-only is fine and cheap;
   a channel with no media file still plays.
3. Write a producer. The only hard rule: **every fact you publish must come from
   a fetch you ran.** Leave a run marker on every run, including silent ones.
4. Copy a `sentinel/<twin>/` directory, edit `checks.py` for your channel, point
   `neighborhood/peers.json` at a neighbor's head, publish yours.
5. Open a PR against `rapp-vision/channels.json`. Nobody has to approve you to run
   the channel — the registry is a convenience, not a gate.

---

## Layout

```
producers/    rvn_common.py    fetch, hash gate, upsert, markers
              rvn_thumb.py     thumbnails rendered from the numbers, as data: URIs
              tumbler_producer.py  fieldguide_producer.py
tumbler/      channel.json     written by the twin
fieldguide/   channel.json     written by the twin
sentinel/     <twin>/          one instance per twin: vendored code + checks + chain
public/       <twin>-head.json rapp-sentinel-head/1.0, what peers read
launchd/      four plist templates (producer daily, sentinel every 15 min)
state/        run markers and hash baselines
tests/        offline, fixture-driven
UPSTREAM-DIFF.md   every line this trial had to change in rapp-sentinel, and why
```

Python 3.11, stdlib only. MIT.
