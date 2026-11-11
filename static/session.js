/* Public pages: if this browser is logged in, "Log in" becomes "My account". */
(function () {
  "use strict";
  const links = document.querySelectorAll("[data-account]");
  if (!links.length) return;
  fetch("/api/session").then((r) => r.json()).then((s) => {
    if (!s.logged_in) return;
    links.forEach((a) => { a.textContent = "My account"; a.href = s.home; });
  }).catch(() => {});
})();
