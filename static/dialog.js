/* Confetti: styled confirm/alert dialogs (replace the browser's plain popups). */
(function () {
  "use strict";
  function open({ title, body, ok = "OK", cancel = null, danger = false, input = null }) {
    return new Promise((resolve) => {
      const back = document.createElement("div");
      back.className = "cdlg-back";
      back.innerHTML = `<div class="cdlg" role="alertdialog" aria-modal="true" aria-labelledby="cdlgT" aria-describedby="cdlgB">
        <h2 id="cdlgT"></h2><p id="cdlgB"></p>
        ${input ? '<input class="input cdlg-input" autocomplete="off" />' : ""}
        <div class="cdlg-actions">
          ${cancel ? '<button type="button" class="btn btn-ghost" data-v="0"></button>' : ""}
          <button type="button" class="btn ${danger ? "btn-delete" : ""}" data-v="1"></button>
        </div></div>`;
      back.querySelector("h2").textContent = title;
      back.querySelector("p").textContent = body || "";
      back.querySelector('[data-v="1"]').textContent = ok;
      if (cancel) back.querySelector('[data-v="0"]').textContent = cancel;
      const field = back.querySelector(".cdlg-input");
      if (field) { field.placeholder = input.placeholder || ""; field.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); }); }
      const prevFocus = document.activeElement;
      const done = (v) => {
        document.removeEventListener("keydown", onKey);
        back.classList.remove("show");
        setTimeout(() => back.remove(), 160);
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        resolve(field ? (v ? field.value.trim() : null) : v);
      };
      const onKey = (e) => { if (e.key === "Escape") done(false); };
      back.addEventListener("click", (e) => {
        if (e.target === back) return done(false);
        const b = e.target.closest("button[data-v]");
        if (b) done(b.dataset.v === "1");
      });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(back);
      requestAnimationFrame(() => back.classList.add("show"));
      (field || back.querySelector('[data-v="0"]') || back.querySelector('[data-v="1"]')).focus();
    });
  }
  window.ConfettiDialog = {
    confirm: (o) => open({ cancel: "Cancel", ...o }),
    alert: (o) => open({ ok: "OK", ...o }),
    // Resolves to the typed text ("" if left empty), or null if cancelled.
    prompt: (o) => open({ cancel: "Cancel", input: {}, ...o }),
  };
})();
