# Project 5 — Mixtape Bug Hunt — Submission

**Branch:** `bugfix/mixtape`
**Bugs fixed:** all 5 (Issues #1–#5) + 1 regression test suite

---

## AI Usage

I used an AI coding assistant (Claude) throughout, deliberately keeping it in
an *explain / trace / verify* role rather than a *diagnose-for-me* role.

**Where AI helped:**

- **Codebase orientation.** I had the assistant summarize each service file
  ("what is this module responsible for, what does each function do") to build
  the codebase map below faster than reading cold. I verified every claim
  against the actual source before writing it down.
- **Tracing call chains.** I asked it to trace, e.g., "how does rating a song
  flow from route to service" — which confirmed `POST /songs/<id>/rate →
  routes/songs.py:rate → notification_service.rate_song`, and made it obvious
  that `rate_song` had no `create_notification` call while `add_to_playlist`
  did (Issue #4).
- **A specific Python semantics question.** For Issue #1 I confirmed that
  `datetime.weekday()` returns `6` for Sunday (Mon=0 … Sun=6) — the exact fact
  the buggy `today.weekday() != 6` guard depended on.
- **Explaining a surprising result.** For Issue #3 the search bug *did not
  reproduce* at first (search returned one row, not three). I asked why, then
  verified myself: the raw join genuinely returns 3 rows, but **SQLAlchemy
  2.0's ORM uniquifies full entities** by primary key on `.all()`, masking the
  duplicates for this specific code path. The AI's first instinct ("just add
  `.distinct()`") was *plausible but treated the symptom* — reading the code
  myself showed the join is entirely unused and the correct fix is to remove
  it.

**Where I had to override / verify the AI:**

- The `.distinct()` suggestion for Issue #3 (above) — I rejected it in favor of
  removing the pointless join, which is the actual root cause.
- I did not trust any "the bug is probably in X" guess. Every fix was confirmed
  by reproducing the behavior first (shell reproductions and the failing tests)
  and re-running afterward.

---

## Codebase Map
*(written during Milestone 1, before opening any issue)*

Mixtape is a Flask app using the app-factory pattern with a clean
**route → service → model** separation. Routes never contain business logic;
they parse input, call exactly one service function, and format the JSON
response.

### Main files and their roles

- **`app.py`** — Flask application factory (`create_app`). Owns the single
  `SQLAlchemy` instance `db`, reads config (SQLite by default), registers the
  four blueprints under URL prefixes (`/songs`, `/playlists`, `/users`,
  `/feed`), and calls `db.create_all()`. **Must be started with
  `FLASK_APP=app:create_app flask run`** — running `python app.py` double-imports
  and breaks SQLAlchemy.

- **`models.py`** — SQLAlchemy models and association tables:
  - **Entities:** `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`,
    `Playlist`, `Notification`.
  - **Association tables:** `friendships` (symmetric many-to-many, inserted in
    both directions on friend-add), `song_tags` (song ↔ tag),
    `playlist_entries` (playlist ↔ song **with extra columns**: `position`,
    `added_by`, `added_at`). Playlist order is explicit via `position`, not
    insertion order.
  - `Rating` has a unique constraint on `(user_id, song_id)` — one rating per
    user per song. A rating is its own row; there is no rating field on `Song`.
  - `User.last_listened_at` + `listening_streak` are denormalized onto the user
    and updated by the streak service.

- **`routes/`** — one blueprint per resource. Each endpoint delegates to a
  service and translates `ValueError` into a 4xx JSON error.
  - `songs.py`: `/search`, `/<id>`, `/<id>/rate` (POST), `/<id>/listen` (POST)
  - `playlists.py`: create, `/<id>`, `/<id>/songs` (GET + POST)
  - `users.py`: `/<id>`, `/<id>/streak`, `/<id>/notifications`, mark-read
  - `feed.py`: `/<id>/listening-now`, `/<id>/activity`

- **`services/`** — all business logic (and all five bugs):
  - `streak_service.py` — `record_listening_event` + `update_listening_streak`
    (consecutive-day streak math), `get_streak`.
  - `feed_service.py` — `get_friends_listening_now` (recency-filtered) and
    `get_activity_feed` (last-N, unfiltered).
  - `search_service.py` — `search_songs` (title/artist ILIKE), `get_song`.
  - `notification_service.py` — `create_notification`, `add_to_playlist` (adds
    song + notifies sharer), `rate_song`, `get_notifications`, `mark_as_read`.
  - `playlist_service.py` — `create_playlist`, `get_playlist_songs`
    (ordered by position), `get_playlist`, `get_user_playlists`.

- **`seed_data.py`** — drops and recreates the DB with 5 users, 13 songs (with
  0 / 1 / 3+ tags), 3 playlists, listening events (recent + old), streaks, and
  one existing playlist-add notification. Deliberately seeds the state each bug
  needs.

### Data flow example — *a friend adds your song to a playlist and you get notified*

1. `POST /playlists/<playlist_id>/songs` with `{song_id, added_by}` →
   `routes/playlists.py:add_song`.
2. Route calls `notification_service.add_to_playlist(playlist_id, song_id,
   added_by)`.
3. The service loads the `Song`, adder `User`, and `Playlist`; appends the song
   to `playlist.songs` (via the `playlist_entries` association) if not already
   present; commits.
4. If `song.shared_by != added_by`, it calls `create_notification(...)` with
   type `song_added_to_playlist`, creating a `Notification` row for the
   **original sharer**.
5. The sharer later reads it via `GET /users/<id>/notifications` →
   `notification_service.get_notifications`.

This is the working pattern that Issue #4 (rating notifications) was *missing* —
`rate_song` did steps 1–3 but skipped step 4.

### Patterns I noticed

- **Every route delegates immediately to one service function.** Logic lives in
  `services/`; that's where the bugs are.
- **Denormalized streak state** on `User` rather than computed from
  `ListeningEvent` rows — so streak correctness depends entirely on the update
  logic running right.
- **Association tables carry data** (`playlist_entries.position/added_by`),
  which is why playlists have explicit ordering.
- **Timestamps are UTC** (`datetime.now(timezone.utc)`), but some stored values
  can be naive — `update_listening_streak` defensively re-attaches
  `tzinfo=utc`.

---

## Root Cause Analysis

### Issue #1 — My listening streak keeps resetting

- **How I reproduced it.** The seeded test `test_streak_increments_on_sunday`
  failed: seeding a listen on Saturday 2024-06-15 then Sunday 2024-06-16
  produced `listening_streak == 1` instead of `2`. I confirmed with the failing
  assertion `assert 1 == 2`.
- **How I found the root cause.** Route `GET /users/<id>/streak` →
  `streak_service.get_streak`, but the write path is
  `record_listening_event → update_listening_streak`. Reading
  `update_listening_streak`, the increment branch was
  `elif days_since_last == 1 and today.weekday() != 6:`. The `weekday() != 6`
  clause jumped out — I confirmed `datetime.weekday()` returns 6 for Sunday
  (Mon=0…Sun=6).
- **The root cause.** `weekday()` returns `6` for Sunday. The code only
  incremented the streak when the previous listen was exactly one day ago
  **and today is not Sunday**. On any Sunday, the `and today.weekday() != 6`
  clause was false, so a genuinely-consecutive Sunday listen fell through to the
  `else` branch and reset the streak to `1`. That's exactly kenji's report:
  streaks died on Sunday mornings.
- **My fix + side-effect check.** Removed the `and today.weekday() != 6` clause
  so a listen one calendar day after the last always increments, any weekday.
  I re-ran all streak tests (new-user start, same-day no-double-count,
  consecutive increment, skipped-day reset, and the Sunday case) — all pass, so
  the reset-on-skip and same-day behaviors are intact.

### Issue #2 — Friends Listening Now shows people from yesterday

- **How I reproduced it.** In a shell, I gave `darius` a single listening event
  10 hours ago (i.e. "last night, ~11pm") and called
  `get_friends_listening_now(nova)` in the morning. `darius` appeared in the
  feed even though his listen was the previous calendar day.
- **How I found the root cause.** `GET /feed/<id>/listening-now` →
  `feed_service.get_friends_listening_now`. Line 1 of that logic:
  `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD`, with
  `RECENT_THRESHOLD = timedelta(hours=24)`. That's a **rolling 24-hour window**,
  not "today."
- **The root cause.** A rolling 24h window keeps any event from the last 24
  hours visible. So a listen at 11pm last night is still "within 24h" until 11pm
  tonight — precisely nova's complaint that yesterday-evening listens hang
  around until the same time the next day. The feature means *today's* listens,
  a calendar-day boundary, not a rolling window.
- **My fix + side-effect check.** Replaced the 24h threshold with the start of
  the current UTC calendar day:
  `cutoff = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)`.
  I verified **both sides of the boundary**: a listen from last night is now
  excluded, and a listen from 00:01 today is still included. I left
  `get_activity_feed` untouched (it is intentionally unfiltered by recency).

### Issue #3 — The same song keeps showing up twice in search

- **How I reproduced it.** This one was subtle: `search_songs("Anthem")`
  returned the multi-tag song **once**, and `test_search_no_duplicates_multi_tag_song`
  passed. So the user-visible duplication did *not* reproduce on the current
  stack. I dug into *why* rather than declaring it fixed. Querying the raw
  joined rows (`db.session.query(Song.id).outerjoin(song_tags…)`) returned
  **3 rows** for a 3-tag song, while the ORM entity query returned **1**.
- **How I found the root cause.** `GET /songs/search` →
  `search_service.search_songs`. The query does
  `.outerjoin(song_tags, Song.id == song_tags.c.song_id)` but filters only on
  `Song.title` / `Song.artist` and never references the join again. The join
  fans out one row per tag.
- **The root cause.** The `outerjoin` against `song_tags` multiplies result
  rows by a song's tag count (3 tags → 3 rows) while contributing nothing to
  the filter — tags are already loaded via the `Song.tags` relationship inside
  `to_dict()`. The reason it doesn't *currently* show duplicates is that
  **SQLAlchemy 2.0's ORM deduplicates full entities by primary key on
  `.all()`**, collapsing the 3 rows back to 1. The bug is real and latent: the
  moment the query selects columns instead of entities, uses `.count()`, or
  runs under a config with uniquing disabled, the duplicates surface exactly as
  reported.
- **My fix + side-effect check.** Removed the unused `outerjoin` (and the now-
  unused `Tag`/`song_tags` imports) so the query returns one row per matching
  song at the SQL level. I confirmed raw rows dropped from 3 → 1 and that the
  returned song still carries its `tags` list (`['rap','hip-hop','boom bap']`)
  via the relationship. All search tests pass, including the no-tag and
  one-tag cases. I chose removal over `.distinct()` because the join was doing
  no work — `.distinct()` would only paper over it.

### Issue #4 — Notified when a friend added my song to a playlist, but not when they rated it

- **How I reproduced it.** In a shell, I recorded the sharer's notification
  count, had another user call `rate_song(...)` on the sharer's song, and
  re-counted: **0 before, 0 after**. The rating saved correctly but no
  notification was created.
- **How I found the root cause.** `POST /songs/<id>/rate` →
  `notification_service.rate_song`. Comparing it line-by-line with
  `add_to_playlist` in the same file: `add_to_playlist` ends with a
  `create_notification(...)` call guarded by `if song.shared_by !=
  added_by_user_id`. `rate_song` had **no `create_notification` call at all** —
  it committed the rating and returned.
- **The root cause.** This is an architectural omission, not a typo: the rating
  path was never wired into the notification system. `create_notification`
  exists and works (playlist adds prove it), but `rate_song` simply never
  invokes it, so the sharer is never told their song was rated.
- **My fix + side-effect check.** After the rating commit, added a
  `create_notification` call (type `song_rated`) to `song.shared_by`, guarded
  by `if song.shared_by != user_id` so users don't get notified for rating
  their own songs — mirroring the `add_to_playlist` pattern exactly. I verified
  a cross-user rating now creates exactly one `song_rated` notification, a
  self-rating creates none, and re-rating (the update path) still notifies. The
  playlist-add notification path is unchanged.

### Issue #5 — The last song in a playlist never shows up

- **How I reproduced it.** `test_playlist_returns_all_songs` failed (returned 4
  of 5 songs), and live against seed data the "Friday Energy" playlist (7 songs)
  returned only **6** — the missing one being the highest `position`.
- **How I found the root cause.** `GET /playlists/<id>/songs` →
  `playlist_service.get_playlist_songs`. The query is correct — it joins
  `playlist_entries` and orders ascending by `position`. But the return line was
  `return [song.to_dict() for song in songs[:-1]]`. The `[:-1]` slice drops the
  final element.
- **The root cause.** `songs[:-1]` discards the last item of the position-
  ordered list. Because the list is sorted ascending by `position`, the last
  item is always the most recently added song — so the newest song is always
  hidden, and adding another song "frees" the previous one and hides the new
  one instead. Exactly darius's report.
- **My fix + side-effect check.** Changed `songs[:-1]` to `songs`. The two
  playlist tests (all-songs count and correct order) pass, and the empty-
  playlist test still returns `[]` without error (slicing an empty list also
  returned `[]`, so that case was never broken and still isn't).

---

## Regression Tests (stretch)

I added two new test files for the two bugs the existing suite did **not**
cover:

- **`tests/test_notifications.py`** — would have caught Issue #4. Asserts that
  rating another user's shared song creates exactly one `song_rated`
  notification, that self-ratings create none, and that re-rating still
  notifies.
- **`tests/test_feed.py`** — would have caught Issue #2. Asserts that a listen
  from late last night is excluded from "listening now" while a listen from
  earlier today is included (both sides of the calendar-day boundary).

Full suite: **18 passed** (`pytest tests/`).

---

## Commit history (`git log --oneline main..bugfix/mixtape`)

```
61e81a0 test: add regression tests for rating notifications and feed boundary
5d9709a fix: remove tag join that duplicated songs in search results
ff11b6e fix: create a notification when a song is rated
8878efa fix: return all playlist songs including the most recently added
87d4479 fix: scope Friends Listening Now to the current calendar day
7ea91ea fix: allow streak to increment on Sundays
```

*(Screenshot of `git log --oneline` to be attached at submission — see
`git-log.txt` in the repo root for the captured output.)*
