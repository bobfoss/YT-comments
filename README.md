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

Search has a **Videos → Comments** filter with **comments** and **no comments**
children, plus a **Search in → Comments** field. Counts represent videos with
captured participating threads; pending history references do not count yet.
Selecting **no comments** excludes the separate thread cards. Parent and child
selections use the shared video-filter controls and persist across reloads.
In global search, comment cards appear only for a nonblank matching search. One card represents
one thread and always includes every captured comment by
the participant. Search includes comment IDs and thread IDs as well as text
and author names. Other comments in that thread are searchable too; a matching
reply is previewed when necessary. Replies are collapsed initially. Known
library profile images are reused; unknown profiles use an initial. Each card's
video title is preceded by the uploader's cached profile thumbnail when known.
Author labels use the observed alias/name, falling back to the channel ID.

Comment cards share the main search **Sort** control and are sorted together
with native results before pagination. **Newest** uses the participant's latest
comment date in the thread; **Oldest** uses their earliest. Exact My Activity
timestamps take priority over capture-anchored estimates when a comment lacks
an exact date. Other authors' dates do not affect the thread's order.
**Most liked** appears in the main sort choices when comment matches exist;
it uses the largest known like count among the participant's comments in each
thread. Results without a known like count follow those with counts. Sorting
preferences persist through YTL's shared controls. Video detail panels still
list the video's threads when their local comment search is empty.

**Meta → Comments**, between Videos and Playlists in the sidebar, opens
`/comments`. This page lists all captured participating threads on a blank
search and searches only comments when text is entered. It has no sidebar
filter checkboxes or Search in controls, and ignores the global video filters.
The shared sort control offers Newest, Oldest, and (when results exist) Most
liked. Its Meta sort preference is saved separately from global search;
query, sort, and page can also be restored from the URL. The plugin requires
the host's generic `browser_collections_v1` feature. No schema change or
recapture is needed.

Exact My Activity timestamps are stored in UTC and displayed with YTL's
configured timezone. Comments without an exact timestamp keep relative ages,
anchored to their saved YouTube label and capture time. They advance as time
passes, including once per minute while the page is open and when returning to
the tab, without a rescan. Month and year ages use calendar subtraction;
rounded ages remain approximate. Unsupported labels display their capture time
alongside the original text. Tooltips retain the original
label and capture time, and **(edited)** is shown whenever YouTube reported it,
including beside exact My Activity dates. Existing captures gain this display
without a migration or refetch; estimates never replace exact timestamps.
Rounded counts remain labels unless YouTube also supplies an exact accessibility
count. A failed or partial capture never erases saved comments. A complete capture
replaces the thread's current comment
set. Capture status and reported-versus-captured reply counts make incomplete
coverage visible.

Default thread refresh age is 24 hours. Explicit **Refresh comments** bypasses
the age threshold. Each thread capture has a 500-page bound and records partial
coverage when exhausted. Schema version 2 adds comment and thread IDs to the
search index. Existing version 1 databases upgrade in place and rebuild that
index from captured comments, without refetching threads or changing content.

Verification:

```powershell
$python = '..\YT Library\.venv\Scripts\python.exe'
& $python -m unittest discover -s tests -v
& $python -m ruff check .
```

Run `tests/js/comment-dates.test.js` with Node's `--test` runner to check relative
clock advancement, tab return, month boundaries, and edited date rendering.

The retrieval protocol was investigated using YouTube Comment Reader 1.0.33;
this plugin is an independent Python implementation, not bundled extension code.
