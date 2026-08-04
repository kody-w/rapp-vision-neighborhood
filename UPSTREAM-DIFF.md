# UPSTREAM-DIFF.md

Every divergence from vendored `kody-w/rapp-sentinel`, why it was forced, and
what upstream could change so the next person does not have to make it.

Vendored files per instance: `rapp.py`, `neighborhood.py`, `health.py`.
Generated per instance: `checks.py`, `tick.py`, `config.json`,
`neighborhood/peers.json`.

`rapp.py` is **unmodified** — byte-identical to upstream in both instances.

---

## 1. Vendoring is not a choice — `HOME` is `__file__`-anchored

**Upstream, `sentinel.py` and `neighborhood.py`:**

```python
HOME = Path(__file__).resolve().parent
```

Every derived path — `config.json`, `state/`, `neighborhood/`, `public/` — hangs
off that. `grep -nE 'SENTINEL_HOME|--home|environ\.get|getenv' *.py` across the
whole repo returns **nothing**: no flag, no environment variable, no constructor
argument.

Consequence: an "instance" *is* a directory containing a copy of the code. Two
neighborhoods on one machine means two copies of `rapp.py`, `neighborhood.py`
and `health.py` — this trial has two, so those three files exist twice. Every
upstream bugfix now has to be applied 2× by hand, and drift is invisible.

**Asked of upstream:** honour `SENTINEL_HOME` (or `--home`) with the current
behaviour as the fallback. Roughly:

```python
HOME = Path(os.environ.get("SENTINEL_HOME") or Path(__file__).resolve().parent)
```

That one line turns `sentinel/` from a directory you copy into a program you point
at data, and makes the launchd templates able to reference a single install.

---

## 2. The documented extension point is not the file that needs editing

`checks.py` says it is "the only file most people need to edit." `health.py` says
it "should rarely need changing." In practice `health.py::probe_watchers()`
hardcodes three domain-specific probes:

| Upstream probe | Status here |
|---|---|
| `POST localhost:7071/chat` (brainstem answers turns) | meaningless — nothing in this trial runs a brainstem |
| `launchctl list com.openrappter.daemon` | meaningless — different estate, different job labels |
| self-freshness from own beacon | **kept byte-identical** (90 min, warn) |

Two of three would have failed on **every tick, forever**. A permanently-red check
is worse than no check: it trains the reader to ignore the surface, which is the
exact nineteen-days-of-green failure in reverse.

**Diverged:** `probe_watchers()` retargeted to this neighborhood's two watcher
beacons. Self-freshness left untouched — same 90-minute threshold, same `warn`
severity, same shape — so it means upstream's thing.

Also removed: `_brainstem_answers_turns()`, dead once its only caller was
retargeted. Left in place it would be a helper that looks load-bearing and is not.

**Asked of upstream:** move the domain probes into `checks.py` (the documented
extension point), or read them from `config.json`, and let `health.py` carry only
the generic self-freshness probe. Then `health.py` genuinely would rarely need
changing, as advertised.

---

## 3. `advancing` is `True` the first time you ever see a peer

**Upstream, `neighborhood.py::peer_roll_call`:**

```python
same = prev.get("heads") == info["heads"] and prev.get("heads") is not None
```

On the first fetch `prev` is empty → `same` is `False` → `advancing` is `True`.

A peer that was **born stalled** — published one head and never ticked again —
reads as advancing on first observation, and stays that way until a second
observation contradicts it. The window is one tick interval, which is exactly the
window in which you are most likely to be looking, because you are looking
*because* you just set it up.

**Not patched here**, because it is upstream's semantics and this trial should not
fork them silently. Documented instead.

**Asked of upstream:** a third state. `advancing` ∈ {`True`, `False`, `unknown`}, or
an `observations` counter so a consumer can tell "advancing" from "insufficient
data." Unknown is honest; `True` is a guess wearing a fact's clothes.

---

## 4. No way to bind a watcher to an existing rappid

`identities()` mints unconditionally:

```python
rapp.mint_rappid(OWNER, f"watcher-{slug}")   # uuid4 inside
```

Correct per §6.2 — identities are minted, never name-hashed. But the thing being
watched here **already has** a canonical identity issued by the egg hub, and there
is no parameter, config key or file that lets a watcher say *I speak for that one*:

```
twin    rappid:@kody-w/rock-tumbler:933e7eaa…
watcher rappid:@kody-w/watcher-tumbler:27565e42…
```

The published head therefore attests that a watcher named `tumbler` is alive. It
cannot attest that the rock-tumbler twin is. A peer reading the head has to trust
prose in a README for the join — which is precisely the kind of unverifiable claim
the rest of the design refuses to make.

**Asked of upstream:** an optional `watches` field in `config.json`, carried into
`identities()` and into the published head, so the binding is *stated in the
artifact* and a peer can check it against the hub card itself.

---

## 5. `/usr/bin/python3` is 3.9.6

All three launchd templates hardcode `/usr/bin/python3`. On this machine that is
**Python 3.9.6**, not 3.11 — so anything using 3.10+ syntax dies at import time,
under launchd, where nobody is watching stderr.

**Diverged:** this trial's templates render a `__PYTHON__` placeholder pinned to an
explicit 3.11 path, and every producer/library file is additionally written 3.9-safe
so a wrong interpreter still runs. Verified: `rvn_common.py` compiles clean under
both 3.9.6 and 3.11.

**Asked of upstream:** make the interpreter a template placeholder that
`install-launchd.sh` fills from `sys.executable`. It already knows the answer at
install time.

---

## 6. `publish_head()` hardcodes its output path

```python
public/sentinel-head.json
```

Two instances on one machine both want to publish. Rather than fork the function —
which would fork the schema with it — `tick.py` lets `publish_head()` write exactly
where and what it wants, then **copies** the result to
`public/<twin>-head.json`. The bytes a peer reads are the bytes upstream's own code
produced, unaltered; only the filename differs.

**Asked of upstream:** derive the filename from the configured neighborhood or
instance name. Falls out for free if #1 lands.

---

## Not diverged, deliberately

- `rapp.py` — untouched, both instances.
- The `rapp-sentinel-head/1.0` schema — written by upstream's own `publish_head()`;
  this trial never hand-builds a head.
- `peer_roll_call` semantics — see #3; documented, not forked.
- Self-freshness probe in `health.py` — same threshold, same severity.
- Level 0. No repair arm, no autonomous filing. Raising it needs its own evidence.

---

## 7. `fetch_peer` throws away the HTTP status

```python
except Exception as e:
    return {..., "reachable": False, "detail": f"{type(e).__name__}"}
```

Every failure therefore arrives at the operator as the bare word `HTTPError`.
Observed verbatim from a real tick:

```
peer_head  False  fieldguide unreachable (HTTPError)
```

A **404** means the peer never published, or the URL is wrong — a configuration
bug, fix it now. A **503** means the peer's host is having a bad minute — ignore
it, it will clear. A **403** means something else entirely. The alert cannot
distinguish them, so every peer failure looks equally urgent, which is the same
as none of them being urgent.

**Diverged, additively:** this trial's `checks.py` re-probes the peer URL purely
to *label* the failure. `peer_roll_call` still decides reachable / valid /
advancing, so upstream semantics are untouched. The same tick now reads:

```
peer_head  False  fieldguide unreachable (HTTPError, HTTP 404)
```

**Asked of upstream:** include `e.code` in `detail` when the exception is an
`HTTPError`. One line, and every peer alert becomes actionable.

---

## 8. Empirical confirmation of #3

Two consecutive `neighborhood.py peers` calls against a peer that **did not change
at all** between them:

```
call 1:  reachable True  valid True  alive True  advancing True
call 2:  reachable True  valid True  alive True  advancing False
```

Same peer, same bytes, same head hash, opposite verdicts. The first was wrong.
This is not a race — it is the first-sight default described in #3, reproduced
deliberately.

The practical cost, in this repo, today: `peers-seen.json` had to be cleared after
testing (test-transport state must not seed production), which means the first
production tick will again report `advancing: True` on first sight of a peer that
may well be stalled. There is no way to avoid that with the current semantics —
you either carry test state into production, or you accept one false green.
