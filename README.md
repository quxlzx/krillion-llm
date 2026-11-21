# krillion-llm

**The [Krillion](https://krillion.io) daily dive, played blind by an agent, with server-signed scores.**

[Krillion](https://krillion.io) is a daily trivia game: seven open prompts,
the same for everyone, and a correct answer only scores if it's *uncommon*
(one in a krillion, roughly). This engine drives an LLM agent (or you) through
the dive honestly — no answer-key peeking, no retry farming — and produces a
recap post whose scores anyone can verify against the game server's own signed
receipts.

Zero third-party dependencies — Python 3.9+ stdlib only (plus `curl`).

Not affiliated with krillion.io. Unofficial tool; it makes ~10 API calls a day.
Be gentle. (And no, [krillion.org](https://krillion.org) is an ad-clone — the
real game is krillion.io.)

## Verify in 2 commands

```bash
python3 -m unittest tests/test_krillion.py -v   # offline, no network
python3 krillion.py status                      # live day, read-only
```

## How an agent plays

```bash
python3 krillion.py prompts                 # today's 7 prompts
python3 krillion.py answer p1 'Gitega'      # ONE shot per prompt
python3 krillion.py answer p2 'Moonstruck'
# ... x7, then:
python3 krillion.py summary                 # totals + server distribution
python3 krillion.py recap                   # printable recap, closed days only
echo '<composed post>' | python3 krillion.py post $DISCORD_WEBHOOK
```

The "think of a rare answer" part is deliberately left to the LLM in the loop
(your agent, your model) — the engine handles submission, state, and the
honesty gates. A nightly cron that plays the live dive and posts *yesterday's*
recap is the intended deployment; see [REPRODUCING.md](REPRODUCING.md).

## Why the scores are trustworthy

- **Scores are not self-reported.** Every accepted answer returns a
  server-signed receipt (JWT-style payload: score, tier, day, playerId). The
  recap prints each payload; decode it and check the numbers yourself. The
  player cannot forge them.
- **The answer key stays locked until the game is done.** krillion.io exposes
  `/api/reveal` and `/api/answer-popularity` — the whole answer sheet. The
  engine refuses to fetch either until all seven of *our* answers are already
  submitted, and the test suite asserts that ordering.
- **One shot per prompt.** The game server would happily accept resubmits
  (answer-shop until you hit a rare one); the engine refuses them.
- **Recaps can't spoil live days.** A day's recap only prints, and only posts,
  after that day's reset has passed. Live days are hard-refused.
- **Commitment up front.** `sha256(nonce + date + prompts)` is logged before
  any answer, so the prompt set can't be swapped after the fact.

What receipts do *not* prove: that a player didn't read the answer key in some
other window, or play under several playerIds. This is an honor system made
detectable-by-design, not a sandbox. Say so plainly if someone asks "did the
bot cheat".

## Verified baseline (real playthrough)

Day 129 (2026-11-21): **470/700 — better than 98% of 560,592 divers.**

| prompt | answer | tier | score |
|---|---|---|---|
| capital city of any country | Gitega | deepcut | 85 |
| rom-com movie | Moonstruck | rare | 60 |
| one-word animal, "ch"/"sh" | Shad | rare | 60 |
| something used to scoop | Dredger | rare | 60 |
| a fish (non-extinct) | Barbel | deepcut | 85 |
| battle royale video game | Naraka: Bladepoint | rare | 60 |
| sport with multiple balls at once | Croquet | rare | 60 |

Tiers observed: `plankton` (10) → `rare` (60) → `deepcut` (85); rejected
answers score 0.

## API notes (reverse-engineered, Nov 2026)

| endpoint | purpose |
|---|---|
| `GET /api/today` | `{date, dayNumber, resetsAt, prompts[]}` |
| `POST /api/submit` | `{date, promptId, answer, previewCorrection, playerId, runId}` → `{accepted, answer, tier, score, receipt}` |
| `POST /api/today/complete` | `{date, playerId, score, record:false}` → day's score distribution |
| `GET /api/reveal?date=` / `GET /api/answer-popularity?date=` | the answer key — **cheat vectors, engine-gated** |

`runId` format: `run_<base36-ms-timestamp padded to 10>_<24 base36 chars>`.
A browser `User-Agent` is required.

## License

MIT — see [LICENSE](LICENSE).
