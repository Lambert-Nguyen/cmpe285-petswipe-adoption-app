# 🐾 PetSwipe

A mobile-first, swipe-to-vote web app for adoptable pets. Swipe **right** to
vote *"Adopt!"*, **left** to *"Skip"*, then jump to a live results board that
aggregates votes from **every** user.

Built for **CMPE 285 — Software Engineering** (final exam project).

---

## How to install and run

Requirements: Python 3.8+.

```bash
# from the project root
pip install -r requirements.txt        # or: pip install flask
python app.py
```

Then open **http://127.0.0.1:5000** in a browser. For the intended experience,
open your browser dev tools and toggle a mobile device frame (e.g. 390 × 844).

On first launch the app creates `petswipe.db` and seeds exactly **100 pets**
(40 dogs, 30 cats, 12 rabbits, 10 birds, 8 hamsters). Re-running `python app.py`
is safe — seeding is skipped when the database already has data. To start
completely fresh, delete `petswipe.db` and restart.

---

## Architecture

The backend is a single **Flask** file (`app.py`) backed by one **SQLite**
file (`petswipe.db`). It owns two tables — `items` (the pet catalog) and
`votes` (one row per session per pet) — and exposes five JSON endpoints:
serve the page, list items (annotated per-session so the deck can resume),
record a vote, return sorted aggregate results, and return global stats.
All vote aggregation (yes/no counts, approval %, sort orders) is computed in
SQL/Python at request time, so the results board always reflects every vote
across every visitor with no caching to invalidate.

The frontend is a single `templates/index.html` with all HTML, CSS, and
vanilla JavaScript inline — **no React, no Node, no build step**. It is a
two-view single-page app (Swipe / Results) that toggles `display` instead of
navigating, so switching tabs never reloads. The swipe deck keeps only the
top three cards in the DOM for a stacked-card effect and uses Pointer Events
(one code path for both mouse and touch) for drag, tilt, color tint, decision
stamps, threshold detection, and the fly-out/snap-back animations.

---

## Dedup strategy (idempotency)

Double-voting is prevented at the **database layer**, not just the UI:

```sql
UNIQUE(item_id, session_id)   -- on the votes table
```

The vote endpoint inserts with `INSERT OR IGNORE`. If the same session votes
on the same pet again, the unique constraint makes the insert a silent no-op:
the API still returns `200 {"ok": true, "duplicate": true}` instead of erroring
or creating a second row. This makes `POST /api/vote` safely **idempotent** —
retries, double-taps, and race conditions can't corrupt the tally.

The client cooperates by generating a `crypto.randomUUID()` session id once,
storing it in `localStorage`, and sending it to `/api/items` so already-voted
pets are filtered out of the deck on load (the user resumes where they left
off). The UI undo button only re-queues a card locally — it never deletes the
server-side vote, keeping the global tally honest.

## Why SQLite

SQLite is a zero-configuration, single-file, serverless database that ships
with Python's standard library — no daemon to install or run, which makes the
"`pip install flask && python app.py`" requirement genuinely one step. It
comfortably handles this app's scale (hundreds of pets, thousands of votes)
and enforces our data integrity rules (the `CHECK` on `choice`, the `FOREIGN
KEY`, and the `UNIQUE` dedup constraint) directly in the schema. The whole
dataset lives in one portable `petswipe.db` file that's trivial to reset.

---

## Core requirements completed

- [x] Flask + SQLite backend, single project folder
- [x] Vanilla HTML/CSS/JS single page served by Flask (no build step)
- [x] Schema exactly as specified (`items`, `votes`, `UNIQUE` dedup)
- [x] Exactly 100 seeded pets (40/30/12/10/8), unique charming names
- [x] Real per-species pet photos (`loremflickr.com/400/500/{species}?lock={id}`)
 - [x] All API endpoints (`/`, `/api/items`, `/api/vote`, `/api/undo`, `/api/results`, `/api/stats`)
- [x] Input validation + `400` on bad input on `POST /api/vote`
- [x] Idempotent voting via UNIQUE constraint
- [x] Mobile-first dark theme, amber accent, DM Sans + Playfair Display
- [x] Card stack effect (3 cards: full / scaled / smaller)
- [x] Touch + mouse drag, tilt, green/red tint, ADOPT!/SKIP stamps
- [x] 80px threshold, fly-out on commit, snap-back below threshold
- [x] ✕ / ♥ tap buttons as swipe alternatives (+ bonus undo)
- [x] Progress bar (X / 100) updating after each vote
- [x] End-of-deck state with "see results" call to action
- [x] Results view: stats bar, 3 sorts, species filter, ranked list, % bars
- [x] Tab navigation with no page reload
- [x] Session via `crypto.randomUUID()`, resumes the deck on reload
- [x] CSS variables for all colors, semantic HTML, commented code

## AI Notes & Grading checklist

- **AI reflection:** See [AI_NOTES.md](AI_NOTES.md) for the project's AI usage reflection and attribution.

- **Grading checklist (rubric → implementation)**
  - **Backend + API endpoints:** Implemented in `app.py` — `/api/items`, `/api/vote`, `/api/results`, `/api/stats`.
  - **Schema & deduplication:** `items` and `votes` tables created in `app.py` with `UNIQUE(item_id, session_id)` and constraints enforced via `PRAGMA foreign_keys = ON`.
  - **Seeding:** Exactly 100 seeded pets created by `init_db()` in `app.py` (asserts validate count and uniqueness).
  - **Frontend SPA:** `templates/index.html` contains the single-page mobile-first app (Swipe + Results) with no build step.
  - **Idempotency & validation:** `POST /api/vote` validates inputs and uses `INSERT OR IGNORE` to keep votes idempotent.
  - **Docs & run instructions:** `requirements.txt` pinned (see below). Run instructions remain: install deps and `python app.py`.

**Note about the dev server:** The app is currently started with `debug=True` in `app.py` for an easy demo experience. This is intentional for the assignment but not production-safe — set `debug=False` and use a WSGI server (e.g., `gunicorn`) for production deployments.

## Known issues / trade-offs

- **Session = browser localStorage.** Clearing storage or using another
  browser/device creates a new voting identity. This is the standard trade-off
  for a no-login demo and is acceptable for the exam scope.
 - **Undo:** The UI supports a single-step Undo and the backend exposes a
   `POST /api/undo` endpoint to retract a previously recorded vote for the
   same session. The frontend calls this endpoint when the user taps Undo so
   the server-side vote is removed and the card is re-queued locally.
- **Flask dev server.** `debug=True` and the built-in server are great for a
  demo but not production; a real deployment would use a WSGI server (gunicorn)
  and `debug=False`.
- **Images depend on loremflickr.com.** An internet connection is needed for
  pet photos (real species-tagged Flickr photos, pinned per pet via `?lock=`);
  an `onerror` fallback re-requests a different locked photo if a load fails, but
  fully offline use will show empty image areas.
- **No pagination on results.** All 100 pets render at once. Fine here; a much
  larger catalog would want windowed/virtualized rendering.
