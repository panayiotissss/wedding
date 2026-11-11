/* Confetti invitation card (WED-36).
 *
 * Draws the couple's text onto the blank card in the last frame of the
 * envelope video. Used by the guest page (animated: the names are written
 * left to right as if by a pen, the other lines type themselves letter by
 * letter) and by the editor's preview (static).
 *
 * The host element must sit exactly over the card. All sizes are in cqw of
 * that element, so it scales with any screen.
 */
(function () {
  "use strict";

  const FONTS = {
    classic:     { label: "Classic",     names: "'Great Vibes', cursive",            text: "'Cormorant Garamond', Georgia, serif", size: 12.5 },
    romantic:    { label: "Romantic",    names: "'Parisienne', cursive",             text: "'Cormorant Garamond', Georgia, serif", size: 10.5 },
    calligraphy: { label: "Calligraphy", names: "'Alex Brush', cursive",             text: "'Cormorant Garamond', Georgia, serif", size: 12 },
    modern:      { label: "Modern",      names: "'Playfair Display', Georgia, serif", text: "'Playfair Display', Georgia, serif",  size: 8.6, italic: true },
    timeless:    { label: "Timeless",    names: "'Cormorant Garamond', Georgia, serif", text: "'Cormorant Garamond', Georgia, serif", size: 10, italic: true },
  };
  const FONT_CSS = "https://fonts.googleapis.com/css2?family=Great+Vibes&family=Parisienne&family=Alex+Brush" +
    "&family=Playfair+Display:ital,wght@0,400;0,500;1,400&family=Cormorant+Garamond:ital,wght@0,500;0,600;1,500&display=swap";

  const CSS = `
  /* The page's "reduce motion" rule would make the writing instant; this card is meant to animate. */
  .cc { position: absolute; inset: 0; container-type: size; display: flex; align-items: center; justify-content: center; pointer-events: none; }
  .cc-in { width: 84%; display: flex; flex-direction: column; align-items: center; text-align: center; color: #2E2620; transform-origin: center; }
  .cc-kicker { font-size: 2.7cqw; letter-spacing: .3em; text-transform: uppercase; color: #8A6D3B; font-weight: 600; }
  .cc-names { position: relative; line-height: 1.22; margin: 1.2cqw 0 .4cqw; white-space: nowrap; color: #3A2C1F; padding: 0 2cqw; }
  .cc-line { font-style: italic; font-size: 4.1cqw; color: #5C514A; }
  .cc-date { font-size: 3.5cqw; letter-spacing: .2em; text-transform: uppercase; margin: 2.6cqw 0 1.2cqw; font-weight: 600; }
  .cc-row { font-size: 4.2cqw; line-height: 1.4; max-width: 100%; }
  .cc-row b { font-size: 2.6cqw; letter-spacing: .22em; text-transform: uppercase; color: #8A6D3B; font-weight: 600; margin-right: .6em; vertical-align: .15em; }
  .cc-row span { font-weight: 600; }
  .cc-rule { width: 14cqw; height: 1px; background: linear-gradient(90deg, transparent, #B8955A, transparent); margin: 1.6cqw 0 1.2cqw; }

  /* Pen writing: a soft mask sweeps left to right; a gold nib glow rides its edge. */
  .cc.anim .cc-names { -webkit-mask-image: linear-gradient(90deg, #000 45%, transparent 55%); mask-image: linear-gradient(90deg, #000 45%, transparent 55%);
    -webkit-mask-size: 250% 100%; mask-size: 250% 100%; -webkit-mask-repeat: no-repeat; mask-repeat: no-repeat;
    -webkit-mask-position: 100% 0; mask-position: 100% 0; }
  .cc.anim.go .cc-names { animation: ccWrite var(--write, 2.6s) linear var(--wd, .5s) forwards !important; }
  @keyframes ccWrite { to { -webkit-mask-position: 0 0; mask-position: 0 0; } }
  .cc-nib { position: absolute; top: 50%; left: -25%; width: 3.2cqw; height: 3.2cqw; margin: -1.6cqw 0 0 -1.6cqw; border-radius: 50%;
    background: radial-gradient(circle, rgba(255,236,180,.95), rgba(228,178,90,.5) 45%, transparent 70%); opacity: 0; }
  .cc.anim.go .cc-nib { animation: ccNib var(--write, 2.6s) linear var(--wd, .5s) forwards !important; }
  @keyframes ccNib { 0% { left: -25%; opacity: 0; } 18% { opacity: 1; } 82% { opacity: 1; } 100% { left: 125%; opacity: 0; } }

  /* Typing: each letter appears in turn. */
  .cc.anim .cc-ch { opacity: 0; }
  .cc.anim.go .cc-ch { animation-name: ccType !important; animation-duration: .28s !important; animation-timing-function: ease-out !important; animation-fill-mode: forwards !important; }
  @keyframes ccType { from { opacity: 0; filter: blur(2px); } to { opacity: 1; filter: none; } }
  .cc.anim .cc-rule { transform: scaleX(0); }
  .cc.anim.go .cc-rule { animation: ccRule .8s ease-out var(--rd, 3s) forwards !important; }
  @keyframes ccRule { to { transform: scaleX(1); } }
  `;

  function ensureAssets() {
    if (document.getElementById("cc-style")) return;
    const st = document.createElement("style");
    st.id = "cc-style";
    st.textContent = CSS;
    document.head.appendChild(st);
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = FONT_CSS;
    document.head.appendChild(l);
  }

  const fmtDate = (d) => d ? new Date(d + "T12:00:00").toLocaleDateString("en-GB",
    { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "";

  function lines(inv) {
    const rows = [];
    if (inv.venue) rows.push(["Ceremony", inv.venue]);
    if (inv.reception) rows.push(["Reception", inv.reception]);
    if (inv.extra1_label || inv.extra1_text) rows.push([inv.extra1_label, inv.extra1_text]);
    if (inv.extra2_label || inv.extra2_text) rows.push([inv.extra2_label, inv.extra2_text]);
    return rows;
  }

  // Wrap each character in a span so it can appear on its own.
  function typed(el, text, start, step) {
    let t = start;
    for (const ch of text) {
      const s = document.createElement("span");
      s.className = "cc-ch";
      s.textContent = ch;
      s.style.animationDelay = t.toFixed(3) + "s";
      el.appendChild(s);
      if (ch !== " ") t += step;
    }
    return t;
  }

  function render(host, inv, opts) {
    ensureAssets();
    const animate = !!(opts && opts.animate);
    const font = FONTS[inv.font] || FONTS.classic;
    host.innerHTML = "";
    const cc = document.createElement("div");
    cc.className = "cc" + (animate ? " anim" : "");
    const box = document.createElement("div");
    box.className = "cc-in";
    box.style.fontFamily = font.text;
    cc.appendChild(box);
    host.appendChild(cc);

    const STEP = 0.032;  // seconds per typed letter
    let t = 0.15;
    const add = (cls, text, pace) => {
      const el = document.createElement("div");
      el.className = cls;
      if (animate) t = typed(el, text, t, pace || STEP) + 0.15; else el.textContent = text;
      box.appendChild(el);
      return el;
    };

    add("cc-kicker", "Together with their families", 0.022);

    const names = document.createElement("div");
    names.className = "cc-names";
    names.textContent = inv.names || "";
    names.style.fontFamily = font.names;
    names.style.fontSize = font.size + "cqw";
    if (font.italic) names.style.fontStyle = "italic";
    const nib = document.createElement("i");
    nib.className = "cc-nib";
    names.appendChild(nib);
    box.appendChild(names);
    const write = Math.min(3.4, Math.max(1.8, (inv.names || "").length * 0.13));
    cc.style.setProperty("--wd", t.toFixed(2) + "s");
    cc.style.setProperty("--write", write.toFixed(2) + "s");
    t += write * 0.85;

    add("cc-line", "request the pleasure of your company");
    const when = [fmtDate(inv.date), inv.time].filter(Boolean).join(" · ");
    if (when) add("cc-date", when, 0.026);

    const rows = lines(inv);
    if (rows.length) {
      const rule = document.createElement("div");
      rule.className = "cc-rule";
      cc.style.setProperty("--rd", t.toFixed(2) + "s");
      box.appendChild(rule);
      t += 0.3;
    }
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      row.className = "cc-row";
      const b = document.createElement("b");
      const v = document.createElement("span");
      if (animate) {
        if (label) t = typed(b, label, t, 0.02);
        t = typed(v, value || "", t + 0.05, STEP) + 0.12;
      } else { b.textContent = label || ""; v.textContent = value || ""; }
      if (label) row.appendChild(b);
      row.appendChild(v);
      box.appendChild(row);
    }

    function fit() {
      // Long names: shrink until they fit the width; then shrink the whole block to the card's height.
      names.style.fontSize = font.size + "cqw";
      const maxW = cc.clientWidth * 0.84;
      let px = parseFloat(getComputedStyle(names).fontSize);
      while (names.scrollWidth > maxW && px > 8) { px -= 0.5; names.style.fontSize = px + "px"; }
      box.style.transform = "";
      const room = cc.clientHeight * 0.78;
      const h = box.scrollHeight;
      if (h > room) box.style.transform = `scale(${(room / h).toFixed(3)})`;
    }
    const ready = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    ready.then(fit);
    fit();

    return {
      duration: t + 0.4,
      fit,
      play() { fit(); void cc.offsetWidth; cc.classList.add("go"); },
      showAll() { cc.classList.remove("anim"); fit(); },
    };
  }

  window.ConfettiCard = { FONTS, FONT_CSS, render, fmtDate };
})();
