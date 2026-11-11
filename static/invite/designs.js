/* Confetti website designs (WED-36).
 *
 * Each design has a look (CSS in static/site.html, keyed by id), a default
 * font pair (static/invite/card.js FONTS) and starter sections: the content a
 * couple gets when they pick it. Starter sections are ready to read; anything
 * left empty (a place, a story) simply doesn't show until it's filled in.
 * Server-side ids live in main.py SITE_THEMES.
 */
(function () {
  "use strict";
  const ev = (title, time, place = "") => ({ type: "event", title, time, place, map_url: "" });
  const qa = (question, answer) => ({ type: "qa", question, answer });
  const text = (title, body) => ({ type: "text", title, body });

  const DESIGNS = {
    linen: {
      label: "Linen", note: "Ivory paper, a gold frame that draws itself, names written by pen.",
      font: "classic", swatch: ["#F6F1E8", "#9C7A4A", "#2E2620"],
      message: "Together with our families, we would love you to celebrate with us.",
      starter: [
        text("Our story", ""),
        ev("Ceremony", "17:00"), ev("Drinks", "18:30"), ev("Dinner", "20:00"), ev("Dancing", "22:00"),
        { type: "countdown", title: "Until we say yes" },
        text("Dress code", "Formal. Think evening wear and comfortable shoes for dancing."),
        qa("Can I bring a plus-one?", "Your invitation is for the names it was sent to. If you have a plus-one, we will have told you."),
        qa("Is there parking?", "Yes, there is free parking at the venue."),
        text("Getting there", ""),
      ],
    },
    garden: {
      label: "Garden", note: "Branches grow in, petals drift, soft cards slide into place.",
      font: "romantic", swatch: ["#EEF1E8", "#5F7F55", "#22332A"],
      message: "We're getting married in the garden, and it wouldn't be the same without you.",
      starter: [
        text("How we met", ""),
        ev("Garden ceremony", "17:00"), ev("Aperitivo on the lawn", "18:00"), ev("Dinner under the trees", "19:30"), ev("Dancing", "22:00"),
        text("What to wear", "Garden formal. The ceremony is on grass, so leave the stilettos at home."),
        { type: "countdown", title: "See you in" },
        qa("Are children welcome?", "We love your little ones, but this will be an adults-only evening."),
        qa("What if it rains?", "There is a covered terrace, so the party goes on whatever the weather."),
      ],
    },
    noir: {
      label: "Noir", note: "Black tie. Gold dust, shimmering names, a grand countdown.",
      font: "modern", swatch: ["#0D0D0C", "#C9A45C", "#F1ECE2"],
      message: "An evening of black tie, champagne and dancing. Please join us.",
      starter: [
        { type: "countdown", title: "The countdown" },
        ev("Ceremony", "18:00"), ev("Champagne reception", "19:00"), ev("Dinner", "20:30"), ev("Dancing until late", "23:00"),
        text("Dress code", "Black tie."),
        text("Our story", ""),
        qa("Is there a gift list?", "Your presence is the only gift we need."),
        qa("Can I take photos?", "During the ceremony, please keep phones away. Afterwards, snap away."),
      ],
    },
    riviera: {
      label: "Riviera", note: "Sun-washed blue and white, rolling waves, a summer by the sea.",
      font: "timeless", swatch: ["#F7F4EE", "#1F5F8B", "#C8643B"],
      message: "Pack your sunglasses. We're getting married by the sea.",
      starter: [
        ev("Ceremony by the sea", "18:00"), ev("Sunset drinks", "19:00"), ev("Dinner", "20:30"), ev("Beach party", "23:00"),
        text("What to wear", "Summer chic. Linen, light colours and flat shoes for the sand."),
        text("Where to stay", ""),
        { type: "countdown", title: "Summer is coming" },
        qa("Will it be hot?", "Probably. There will be shade, fans and plenty of cold water."),
        qa("Is there a shuttle?", "Yes, a shuttle runs from the town centre before the ceremony and after the party."),
      ],
    },
    bloom: {
      label: "Bloom", note: "Soft colour that drifts and blooms, huge names, a date that never stops.",
      font: "calligraphy", swatch: ["#F6E6E1", "#B4536A", "#3A1F27"],
      message: "Love is in the air, and so is a very good party. Come celebrate with us.",
      starter: [
        text("Our story", ""),
        { type: "countdown", title: "Days to go" },
        ev("Ceremony", "17:30"), ev("Cocktails", "18:30"), ev("Dinner", "20:00"), ev("Party", "22:00"),
        text("Dress code", "Cocktail attire. Colour is very welcome."),
        qa("Can I bring a plus-one?", "If your invitation says so, absolutely."),
        qa("When should I reply by?", "As soon as you can, so we can save you a seat."),
      ],
    },
  };

  // Sample couple for demos (landing page, design previews).
  const DEMO = {
    names: "Sofia & Daniel", date: "2027-06-12", time: "17:00", rsvp_by: "2027-05-01",
    fill: {
      "Our story": "We met at a friend's wedding in Paphos, on the dance floor at one in the morning. Seven years, two cities and one dog later, it's our turn.",
      "How we met": "We met at a friend's wedding in Paphos, on the dance floor at one in the morning. Seven years later, it's our turn.",
      "Getting there": "The venue is 20 minutes from Paphos airport. Taxis wait outside arrivals.",
      "Where to stay": "We've reserved rooms at the hotel next door. Mention our names when you book.",
    },
    places: ["Agios Georgios Church", "Elysium Gardens", "Elysium Gardens", "The Terrace"],
  };

  const newId = () => Math.random().toString(36).slice(2, 10);
  function starter(id) {
    const d = DESIGNS[id] || DESIGNS.linen;
    return d.starter.map((s) => ({ ...s, id: newId() }));
  }
  function demo(id) {
    const d = DESIGNS[id] || DESIGNS.linen;
    let e = 0;
    const sections = starter(id).map((s) => {
      if (s.type === "text" && !s.body) s.body = DEMO.fill[s.title] || "";
      if (s.type === "event") s.place = DEMO.places[e++] || "";
      return s;
    });
    return { names: DEMO.names, date: DEMO.date, time: DEMO.time, rsvp_by: DEMO.rsvp_by, message: d.message,
             theme: id, font: d.font, sections, upload_url: null, preview: true, demo: true };
  }

  window.ConfettiDesigns = { DESIGNS, starter, demo };
})();
