# AI Usage Reflection — PetSwipe

**Course:** CMPE 285 Software Engineering — Final Exam Project
**AI tool used:** Claude (Anthropic)

## Which parts Claude wrote end-to-end

Claude scaffolded essentially the entire project from a single detailed
specification: the Flask backend (`app.py`) including the SQLite schema,
connection handling, all five API routes, the input-validation logic on
`POST /api/vote`, and the three results sort strategies; the full 100-pet
seed dataset with unique names, breeds, and one-line personality
descriptions; and the entire single-page frontend (`templates/index.html`) —
the dark mobile-first CSS design system, the stacked swipe-card UI, the
pointer-based drag/tilt/tint/stamp interaction, the results board, tab
navigation, and session handling. The supporting `requirements.txt`,
`README.md`, and this file were also drafted by Claude.

## Where I had to fix or rewrite Claude's output

The most concrete fix was around **swipe input handling**. The first pass
wired up *separate* `mousedown/mousemove/mouseup` and
`touchstart/touchmove/touchend` listeners, which led to duplicated logic and
a bug where a fast touch-drag could fire both a touch and a synthesized mouse
event, double-counting a vote. I consolidated this into a single **Pointer
Events** path (`pointerdown/pointermove/pointerup`), which unifies mouse and
touch, removed the duplicate listeners, and eliminated the double-vote. I also
tightened the *idempotency* story: the initial code only relied on the UI to
prevent re-votes, so I made the server use `INSERT OR IGNORE` against the
`UNIQUE(item_id, session_id)` constraint so duplicates are impossible even if
the client misbehaves, and adjusted the endpoint to report `duplicate: true`
rather than erroring.

## One thing Claude did better than expected

The **seed data quality**. Rather than generic filler ("Dog 1", "Dog 2"),
Claude produced 100 genuinely distinct, charming pets — varied real breeds in
the correct species proportions, all-unique names, and personality-filled
one-sentence descriptions that make the swipe deck feel like a real adoption
app. It even added import-time `assert`s validating the count, species set,
and name uniqueness, which caught drift early.

## One thing Claude did worse than expected

**Edge cases in the results sorting.** The first "most divisive" sort ranked
pets purely by closeness to a 50/50 split, which put pets with **zero votes**
(vacuously "0% = not divisive", or NaN in an early version) in nonsensical
positions and risked a divide-by-zero on `yes_pct`. I had to specify that
unvoted pets should sink to the bottom of every sort and that `yes_pct` must
default to `0.0` when total votes is zero. Claude reached for the "happy path"
aggregation and under-considered the empty-data state until prompted.

## Edits applied after review

- Pinned Flask in `requirements.txt` to `Flask==2.3.3` to reduce environment
	variability when running the app locally or in CI.
- Added a short **Grading checklist** and a link to this file from `README.md`.
- Recommendation: run a small smoke test script (or curl requests) to verify
	the server responds to `/api/items` and `/api/results` in the target
	environment; I can add the script on request.

Updated: 2026-05-18