#!/usr/bin/env python3
"""Blind Krillion engine — plays the krillion.io daily dive without peeking.

Honesty model (different from Wordle, and stronger in one way):
  * Scores are NOT self-reported. Every accepted answer returns a server-signed
    receipt; the recap prints each receipt payload so anyone can decode it and
    check day, playerId, tier and score themselves. We cannot forge them.
  * The cheat this engine blocks is LOOKUP: krillion.io exposes /api/reveal and
    /api/answer-popularity (the answer key). The engine refuses to fetch either
    until all seven of OUR answers are already submitted.
  * One answer per prompt, no retries. The server would happily accept a
    resubmit; we never send one. A rejected answer is a miss (0).
  * A day's recap can only be printed after that day's resetsAt has passed, so
    a posted recap can never spoil a live dive.

What receipts do NOT prove: that the player didn't read the answer key in some
other window, or that they didn't play under several playerIds. Honor system,
detectable-by-design, same as the Wordle job. Say so plainly if asked.

Usage:
  krillion.py status              -> current day, answers used, total, commitment
  krillion.py prompts             -> today's 7 prompts (initializes the day)
  krillion.py answer PID TEXT     -> submit one answer (one shot per prompt)
  krillion.py summary             -> after 7 answers: totals + server stats
  krillion.py recap [DATE]        -> printable recap of a FINISHED day (refuses
                                     live days). Default: latest saved day.
  krillion.py post WEBHOOK [DATE] -> pipe a recap post (stdin) to a Discord
                                     webhook; refuses live days' dates.
  krillion.py reset [DATE]        -> drop saved state for a date
Env: KRILLION_STATE overrides the state file path.
"""
import base64
import datetime
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
STATE = os.environ.get(
    "KRILLION_STATE", os.path.expanduser("~/.krillion_state.json"))


def curl(url, data=None):
    cmd = ["curl", "-sS", "-m", "20", "-A", UA,
           "-H", "Content-Type: application/json"]
    if data is not None:
        cmd += ["-X", "POST", "--data", json.dumps(data)]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"non-JSON response: {r.stdout[:200]!r}")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _parse(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _b36(n, width):
    out = ""
    while n:
        out = "0123456789abcdefghijklmnopqrstuvwxyz"[n % 36] + out
        n //= 36
    return out.rjust(width, "0")


def _new_ids():
    player = secrets.token_hex(16)
    ts = int(time.time() * 1000)
    run = f"run_{_b36(ts, 10)}_{secrets.token_hex(12)[:24]}"
    return player, run


def _load():
    if os.path.exists(STATE):
        try:
            with open(STATE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"days": {}}


def _save(st):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)


def _today():
    return curl("https://krillion.io/api/today")


def _day(st, date):
    return st["days"].get(date)


def _init_day(st):
    t = _today()
    date = t["date"]
    d = _day(st, date)
    if d:
        return d
    player, run = _new_ids()
    nonce = secrets.token_hex(16)
    ptexts = "|".join(p["text"] for p in t["prompts"])
    d = {
        "date": date,
        "dayNumber": t["dayNumber"],
        "resetsAt": t["resetsAt"],
        "prompts": t["prompts"],
        "playerId": player,
        "runId": run,
        "nonce": nonce,
        "commit": hashlib.sha256(
            (nonce + date + ptexts).encode()).hexdigest(),
        "committed_at": _iso(_now()),
        "answers": {},
        "extras": None,
    }
    st["days"][date] = d
    _save(st)
    return d


def _live(d):
    return _parse(d["resetsAt"]) > _now()


def _total(d):
    return sum(a.get("score", 0) for a in d["answers"].values())


def _receipt_payload(receipt):
    try:
        part = receipt.split(".")[0]
        pad = part + "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return None


def cmd_status(st):
    t = _today()
    d = _day(st, t["date"]) or _init_day(st)
    print(f"Krillion day {d['dayNumber']} ({d['date']}) "
          f"{'LIVE' if _live(d) else 'CLOSED'} — resets {_parse(d['resetsAt'])}")
    print(f"commitment: {d['commit']}  (at {d['committed_at']}, before answers)")
    print(f"answers: {len(d['answers'])}/7  total: {_total(d)}/700")
    for pid, a in sorted(d["answers"].items()):
        verdict = (f"{a['tier']} {a['score']}" if a["accepted"] else "MISS")
        print(f"  {pid}: {a['sent']!r} -> {a.get('canonical') or '-'} {verdict}")
    return 0


def cmd_prompts(st):
    d = _init_day(st)
    if not _live(d):
        print("REFUSED: this day is already over; the engine only plays the "
              "live dive.")
        return 1
    print(f"Krillion day {d['dayNumber']} ({d['date']}), resets "
          f"{_parse(d['resetsAt'])}")
    print(f"commitment: {d['commit']}")
    for p in d["prompts"]:
        used = " [ANSWERED]" if p["id"] in d["answers"] else ""
        print(f"{p['id']}: {p['text']}{used}")
    return 0


def cmd_answer(st, pid, text):
    t = _today()
    d = _day(st, t["date"]) or _init_day(st)
    if not _live(d):
        print("REFUSED: day is over.")
        return 1
    if pid in d["answers"]:
        print(f"REFUSED: {pid} already answered ({d['answers'][pid]['sent']!r})."
              " One shot per prompt, no retries.")
        return 1
    valid_ids = {p["id"] for p in d["prompts"]}
    if pid not in valid_ids:
        print(f"REFUSED: unknown prompt {pid}. Valid: {sorted(valid_ids)}")
        return 2
    resp = curl("https://krillion.io/api/submit", {
        "date": d["date"], "promptId": pid, "answer": text,
        "previewCorrection": False,
        "playerId": d["playerId"], "runId": d["runId"]})
    rec = {
        "sent": text,
        "accepted": bool(resp.get("accepted")),
        "canonical": resp.get("answer"),
        "tier": resp.get("tier"),
        "score": int(resp.get("score", 0) or 0),
        "receipt": resp.get("receipt"),
        "at": _iso(_now()),
    }
    d["answers"][pid] = rec
    _save(st)
    if rec["accepted"]:
        print(f"{pid}: {text!r} -> ACCEPTED as {rec['canonical']!r} "
              f"[{rec['tier']}] score {rec['score']}")
    else:
        print(f"{pid}: {text!r} -> REJECTED (miss, 0)")
    print(f"total so far: {_total(d)}/700  ({len(d['answers'])}/7 answered)")
    return 0


def cmd_summary(st):
    t = _today()
    d = _day(st, t["date"])
    if not d:
        print("No game for today.")
        return 1
    if len(d["answers"]) < 7:
        print(f"LOCKED: {len(d['answers'])}/7 answered. Answer-key endpoints "
              "stay closed until the game is complete.")
        return 1
    if not d["extras"]:
        total = _total(d)
        comp = curl("https://krillion.io/api/today/complete", {
            "date": d["date"], "playerId": d["playerId"],
            "score": total, "record": False})
        d["extras"] = {
            "fetched_at": _iso(_now()),
            "complete": comp,
            "reveal": curl(f"https://krillion.io/api/reveal?date={d['date']}"),
            "popularity": curl(
                "https://krillion.io/api/answer-popularity"
                f"?date={d['date']}"),
        }
        _save(st)
    _print_recap(d)
    return 0


def cmd_recap(st, date=None):
    st_days = st["days"]
    if not st_days:
        print("NO_DATA")
        return 3
    if date:
        d = _day(st, date)
        if not d:
            print(f"NO_DATA for {date}")
            return 3
    else:
        d = max(st_days.values(), key=lambda x: x["date"])
    if len(d["answers"]) < 7:
        print(f"NO_DATA: day {d['date']} incomplete "
              f"({len(d['answers'])}/7).")
        return 3
    if _live(d):
        print(f"REFUSED: day {d['date']} ({d['dayNumber']}) is still LIVE "
              f"(resets {_parse(d['resetsAt'])}). Recaps post only after the "
              "day closes — no spoiling.")
        return 1
    if not d["extras"]:
        # day is closed and our game is done: safe to fetch answer-key data
        total = _total(d)
        try:
            d["extras"] = {
                "fetched_at": _iso(_now()),
                "complete": None,
                "reveal": curl(
                    f"https://krillion.io/api/reveal?date={d['date']}"),
                "popularity": curl(
                    "https://krillion.io/api/answer-popularity"
                    f"?date={d['date']}"),
            }
        except RuntimeError as e:
            print(f"note: answer-key fetch failed: {e}")
            d["extras"] = {"fetched_at": _iso(_now()), "complete": None,
                           "reveal": None, "popularity": None}
        _save(st)
    _print_recap(d)
    return 0


def _print_recap(d):
    ex = d.get("extras") or {}
    comp = ex.get("complete")
    total = _total(d)
    head = f"Krillion day {d['dayNumber']} ({d['date']}) — total {total}/700"
    if comp and isinstance(comp, dict) and "sample" in comp:
        head += (f" | better than {comp['betterThan']}% of "
                 f"{comp['sample']:,} divers")
    print(head)
    print(f"player: {d['playerId']}  commit: {d['commit'][:16]}…")
    pop = (ex.get("popularity") or {}).get("prompts", {})
    for p in d["prompts"]:
        a = d["answers"].get(p["id"], {})
        if a.get("accepted"):
            n = (pop.get(p["id"]) or {}).get(a["canonical"], "?")
            print(f"{p['id']} {p['text']}\n   -> {a['canonical']} "
                  f"[{a['tier']}] {a['score']}  (picked by {n} players)")
        else:
            print(f"{p['id']} {p['text']}\n   -> {a.get('sent')!r} REJECTED "
                  "(miss)")
    rev = ex.get("reveal")
    if rev:
        deepest = []
        for p in rev.get("prompts", []):
            ans = p.get("answers") or []
            if ans:
                deepest.append(f"{p['id']}={ans[0]['answer']}")
        if deepest:
            print("intended deepest cuts: " + "  ".join(deepest))
    print("receipts (decode payload = server-signed score):")
    for pid, a in sorted(d["answers"].items()):
        pl = _receipt_payload(a.get("receipt") or "")
        if pl:
            print(f"  {pid}: score={pl.get('score')} tier={a.get('tier')} "
                  f"day={pl.get('game')} player={pl.get('playerId')[:8]}…")


def cmd_post(st, webhook, date=None):
    """Pipe a composed recap post (stdin) to a Discord webhook.

    Same gate as `recap`: while a day is live, nothing about it gets posted —
    a recap must never spoil the dive that is still running.
    """
    text = sys.stdin.read().strip()
    if not text:
        print("REFUSED: empty post body (pipe the recap text via stdin).")
        return 2
    if date:
        d = _day(st, date)
    else:
        d = max(st["days"].values(), key=lambda x: x["date"]) \
            if st["days"] else None
    if d and _live(d):
        print(f"REFUSED: day {d['date']} (day {d['dayNumber']}) is still LIVE "
              "(resets "
              f"{_parse(d['resetsAt'])}). Post closed days only — no spoiling.")
        return 1
    req = urllib.request.Request(
        webhook, data=json.dumps({"content": text}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"posted to webhook (HTTP {r.status})")
            return 0
    except urllib.error.HTTPError as e:
        print(f"webhook error {e.code}: {e.read()[:200]!r}")
        return 1


def main(argv):
    # prompts contain UTF-8 (curly quotes); Windows consoles default to cp1252
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    cmd = argv[1] if len(argv) > 1 else "status"
    st = _load()
    if cmd == "status":
        return cmd_status(st)
    if cmd == "prompts":
        return cmd_prompts(st)
    if cmd == "answer":
        if len(argv) < 4:
            print("usage: krillion.py answer PID 'answer text'")
            return 2
        return cmd_answer(st, argv[2], " ".join(argv[3:]))
    if cmd == "summary":
        return cmd_summary(st)
    if cmd == "recap":
        return cmd_recap(st, argv[2] if len(argv) > 2 else None)
    if cmd == "post":
        if len(argv) < 3:
            print("usage: echo '<recap text>' | krillion.py post WEBHOOK_URL "
                  "[DATE]")
            return 2
        return cmd_post(st, argv[2], argv[3] if len(argv) > 3 else None)
    if cmd == "reset":
        date = argv[2] if len(argv) > 2 else _today()["date"]
        if date in st["days"]:
            del st["days"][date]
            _save(st)
            print(f"cleared {date}")
        else:
            print(f"no state for {date}")
        return 0
    print("usage: krillion.py [status|prompts|answer PID TEXT|summary|"
          "recap [DATE]|reset [DATE]]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
