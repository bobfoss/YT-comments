# YT Comments (YTC)

Optional Python plugin for YT Library API v2. Discovers comment participation
through the configured Google My Activity session and captures full participating
threads through authenticated YouTube watch-page continuations. It never scans
the library's videos or saves unrelated top-level threads from response pages.

Install with YTL's interpreter:

```powershell
& '..\YT Library\.venv\Scripts\python.exe' -m pip install --no-deps -e .
```

Enable the `comments` entry under YTL's `plugins` configuration, with `config`
pointing to this repository's `yt_comments.config.json`. Restart using YTL's
`scripts\service.ps1`. The plugin creates its own configuration and schema.
Use Advanced Admin → Plugins → YT Comments → **Discover comments** for the
initial backfill. Discovery also runs during manual and scheduled library
Updates. Successful video metadata scans queue due known participating threads.
All jobs use the common queue and may be stopped and resumed there.

Discovery indexes the complete account history initially, then stops after
reaching an already indexed page on later runs. It adds referenced video IDs
and observed titles to YTL and queues metadata for missing videos. Neither
discovery nor comment capture creates watch occurrences. Hidden history records
without recoverable video/comment links are skipped rather than fabricated.

Only captures with explicit authenticated participation are saved. The first
capture establishes the YouTube channel ID; changing that identity produces an
error and preserves existing data. Google and YouTube cookie exports should
belong to the same account. Cookie ownership, proxy settings, pacing, and queue
operations remain in YTL. No cookies or profile images are stored by YTC.

Search has a **Comments** result kind and **Search in → Comments** field.
One card represents one thread and always includes every captured comment by
the participant. Other comments in that thread are searchable too; a matching
reply is previewed when necessary. Replies are collapsed initially. Known
library profile images are reused; unknown profiles use an initial. Author
labels use the observed alias/name, falling back to the channel ID.

The **Comment order** selector persists in the plugin configuration. Newest
and oldest refer to account participation timestamps from My Activity; most
liked orders by the largest like count among the participant's comments in a
thread. Individual counts remain visible. The global native-result sort does
not control this independent result type.

Exact My Activity timestamps are stored in UTC and displayed with YTL's
configured timezone. Relative YouTube date labels remain observations, with
the capture date in their tooltip. Rounded counts remain labels unless YouTube
also supplies an exact accessibility count. A failed or partial capture never
erases saved comments. A complete capture replaces the thread's current comment
set. Capture status and reported-versus-captured reply counts make incomplete
coverage visible.

Default thread refresh age is 24 hours. Explicit **Refresh comments** bypasses
the age threshold. Each thread capture has a 500-page bound and records partial
coverage when exhausted. Schema version 1 is initialized locally; future
versions must add ordered migrations and preserve captured content.

Verification:

```powershell
$python = '..\YT Library\.venv\Scripts\python.exe'
& $python -m unittest discover -s tests -v
& $python -m ruff check .
```

The retrieval protocol was investigated using YouTube Comment Reader 1.0.33;
this plugin is an independent Python implementation, not bundled extension code.
