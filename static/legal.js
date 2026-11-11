/* Legal pages: build the contents list from the sections and highlight the one being read. */
(function () {
  "use strict";
  const toc = document.getElementById("toc");
  const sections = [...document.querySelectorAll("article section[id]")];
  toc.innerHTML = sections.map((s) => {
    const h = s.querySelector("h2").cloneNode(true);
    const n = h.querySelector("span"); if (n) n.remove();
    return `<a href="#${s.id}">${h.textContent.trim()}</a>`;
  }).join("");
  const links = [...toc.querySelectorAll("a")];
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + e.target.id));
  }), { rootMargin: "-20% 0px -70% 0px" });
  sections.forEach((s) => io.observe(s));
})();
