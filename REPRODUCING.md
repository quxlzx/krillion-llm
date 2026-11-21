# Reproducing from a fresh clone

Everything below was smoke-tested on Linux with Python 3.13 (stdlib only,
plus `curl` on PATH).

## 1. Offline test suite (no network, no game account)

```bash
python3 -m unittest tests/test_krillion.py -v
```

Expected: `Ran 10 tests ... OK`. The suite runs against a fake API and asserts
the honesty gates: answer-key endpoints untouched before all 7 answers, one
shot per prompt, live days never printed or posted, commitment hash binds the
prompts.

## 2. Play a live day (needs internet; the game is anonymous)

```bash
python3 krillion.py prompts
python3 krillion.py answer p1 'your rare answer'
# ... one answer per prompt, p1..p7 ...
python3 krillion.py summary
```

State lives in `~/.krillion_state.json` (override with `KRILLION_STATE=/path`).
Each day gets a fresh anonymous `playerId`; no account, no token, nothing to
register.

Success looks like:

```
p1 'Gitega' -> ACCEPTED as 'Gitega' [deepcut] score 85
total so far: 85/700  (1/7 answered)
```

## 3. Recap + post (after the day closes)

```bash
python3 krillion.py recap
echo '**Day 129 — 470/700 ...**' | python3 krillion.py post "$DISCORD_WEBHOOK"
```

While the day is still live both commands print `REFUSED: ... still LIVE`.
That is the anti-spoiler gate, not a bug: the intended flow is *play today,
post yesterday*.

## 4. Nightly cron (the intended deployment)

One job, every evening after most players have dived (the day resets at
midnight local / 05:00 UTC). Pseudocode for the agent prompt:

1. `recap` → if it prints data, compose a post from it and `post` it; if it
   prints `NO_DATA`/`REFUSED`, post nothing.
2. `prompts` → think rare-but-valid answers from your own knowledge;
   `answer` each once; then `summary`. Post nothing about today.
3. Hard rule: never fetch `/api/reveal` or `/api/answer-popularity` outside
   the engine before all 7 answers are in.

## What is NOT reproducible from a clone

- The day-129 baseline table in the README is a historical playthrough against
  live human answer distributions — you can re-read its receipts from a state
  file, but you can't re-score it.
- Rarity scores shift with the player population; expect different numbers on
  different days.
