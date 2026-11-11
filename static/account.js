/* Confetti: buy the other add-on (photo gallery or invitation) from inside an account. */
(function () {
  "use strict";
  const INFO = {
    invite: { name: "Invitation + RSVP", price: "€20", body: "A wedding website invitation in one of five designs, with replies collected for you." },
    photos: { name: "Photo gallery", price: "€25", body: "A QR code for your tables. Guests' photos land in one private gallery, kept for 12 months." },
  };
  async function buy(slug, product) {
    const i = INFO[product];
    const code = await ConfettiDialog.prompt({
      title: `Add ${i.name}`,
      body: `${i.body} ${i.price}, one-time. Have an access code? Enter it below, or leave it empty to pay.`,
      ok: "Continue", input: { placeholder: "Access code (optional)" },
    });
    if (code === null) return;
    const r = await fetch(`/api/e/${encodeURIComponent(slug)}/addon`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product, access_code: code || null }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = Array.isArray(d.detail) ? d.detail[0].msg.replace(/^Value error, /, "") : (d.detail || "Please try again.");
      return ConfettiDialog.alert({ title: "Couldn't add it", body: msg });
    }
    location.href = d.payment_url || (product === "invite" ? `/e/${slug}/invite` : `/e/${slug}/gallery`);
  }
  window.ConfettiAccount = {
    buy,
    get: async (slug) => { const r = await fetch(`/api/e/${encodeURIComponent(slug)}/account`); return r.ok ? r.json() : null; },
  };
})();
