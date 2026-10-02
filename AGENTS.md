# YT Comments

This is an optional Python plugin for YT Library. Read this file before work.
Use `../YT Library/.venv/Scripts/python.exe` for Python and local unittest/Ruff
checks. The plugin owns its SQLite database, schema migrations and UI assets.
Never open the YT Library database; use the versioned host contract.

Capture only threads in which the authenticated YouTube account participates.
My Activity discovers comment IDs; verify participation with authenticated
YouTube evidence. Keep full thread context, replies collapsed by default, and
one search card per thread containing all that account's comments. Display
profile aliases/names, falling back to channel IDs. Never label authors You.
Reuse known profile thumbnails only. Do not fetch unknown authors' images.

Backfill only threads discovered in My Activity, then update incrementally and
alongside video metadata. Use host queues, cookies, proxy, pacing and discovery
services. Keep authentication mismatch, incomplete capture and unavailable
threads distinct; failed checks must not erase useful content. Exact timestamps
are UTC; relative source labels must not become invented exact instants.

Preserve unrelated work, commit coherent verified milestones with substantive
bodies, and push only on request. Keep personal data and configuration out of Git.
