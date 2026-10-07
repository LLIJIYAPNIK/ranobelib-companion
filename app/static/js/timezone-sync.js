// PR 322: reports the browser's IANA time zone once, so activity "days" (today, the
// streak, the profile calendar) follow the user's own midnight. base.html only includes
// this while the account has no zone yet, and POST /settings/timezone only fills an
// empty one - a zone picked by hand in the account settings is never overwritten.
(() => {
  let zone;
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return;
  }
  if (!zone) return;
  const body = new FormData();
  body.append("timezone", zone);
  fetch("/settings/timezone", { method: "POST", body, credentials: "same-origin" }).catch(() => {
    // Offline or blocked: the next page load tries again.
  });
})();
