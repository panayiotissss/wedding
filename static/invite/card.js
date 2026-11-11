/* Confetti invitation fonts (WED-36): the name + text pairs couples choose from.
 * Used by the website renderer (static/site.html), the editor and the landing page. */
(function () {
  "use strict";
  const FONTS = {
    classic:     { label: "Classic",     names: "'Great Vibes', cursive",               text: "'Cormorant Garamond', Georgia, serif" },
    romantic:    { label: "Romantic",    names: "'Parisienne', cursive",                text: "'Cormorant Garamond', Georgia, serif" },
    calligraphy: { label: "Calligraphy", names: "'Alex Brush', cursive",                text: "'Cormorant Garamond', Georgia, serif" },
    modern:      { label: "Modern",      names: "'Playfair Display', Georgia, serif",   text: "'Playfair Display', Georgia, serif", italic: true },
    timeless:    { label: "Timeless",    names: "'Cormorant Garamond', Georgia, serif", text: "'Cormorant Garamond', Georgia, serif", italic: true },
  };
  const FONT_CSS = "https://fonts.googleapis.com/css2?family=Great+Vibes&family=Parisienne&family=Alex+Brush" +
    "&family=Playfair+Display:ital,wght@0,400;0,500;1,400&family=Cormorant+Garamond:ital,wght@0,500;0,600;1,500&display=swap";
  const fmtDate = (d) => d ? new Date(d + "T12:00:00").toLocaleDateString("en-GB",
    { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "";
  window.ConfettiCard = { FONTS, FONT_CSS, fmtDate };
})();
