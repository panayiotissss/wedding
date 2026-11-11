/* Confetti opening films (WED-36/41).
 *
 * To add a film: put <id>.mp4, <id>.webm, <id>-start.jpg and <id>-end.jpg in
 * static/invite/, measure where the blank card sits in the last frame (as
 * fractions of the 9:16 frame), set ready: true. Server-side ids live in
 * main.py FILM_THEMES.
 */
window.ConfettiFilms = {
  envelope: {
    label: "Emerald envelope", ready: true, files: "envelope",
    card: { left: 0.142, width: 0.75, top: 0.317, height: 0.314 },
    cardAt: 7.6,          // seconds: the card has settled by now
    background: "#0B2219",
  },
  film2: { label: "Film 2", ready: false },
  film3: { label: "Film 3", ready: false },
};
window.ConfettiFilm = function (id) {
  const f = window.ConfettiFilms[id];
  const film = f && f.ready ? f : window.ConfettiFilms.envelope;
  const base = "/static/invite/" + film.files;
  return Object.assign({ mp4: base + ".mp4", webm: base + ".webm", start: base + "-start.jpg", end: base + "-end.jpg" }, film);
};
