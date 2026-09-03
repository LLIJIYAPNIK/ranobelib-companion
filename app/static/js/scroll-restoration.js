// PR 129: browsers auto-restore the previous scroll position on reload/back-forward
// navigation by default - that restore (to wherever the visitor was before) runs after
// tap-to-read.js/reader-progress.js's own deferred scroll-to-saved-progress and would
// silently win the race, undoing it. Disabling it here, as early as possible (before
// either of those scripts, before layout/paint even), leaves that scroll-restore as the
// only thing that ever moves the page on load. Moved out of an inline <script> into its
// own file (PR 189) so script-src can drop 'unsafe-inline'.
history.scrollRestoration = "manual";
