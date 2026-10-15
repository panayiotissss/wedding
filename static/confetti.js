// Small confetti burst for success moments (payment done, upload done).
window.confettiBurst = function (count) {
  if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ["#9E4F45", "#E4B25A", "#F6D9D2", "#2B2420", "#C98B7F"];
  for (let i = 0; i < (count || 70); i++) {
    const p = document.createElement("div");
    p.className = "confetti-piece";
    p.style.left = Math.random() * 100 + "vw";
    p.style.background = colors[i % colors.length];
    p.style.animationDuration = 1.8 + Math.random() * 1.6 + "s";
    p.style.animationDelay = Math.random() * 0.4 + "s";
    p.style.transform = `rotate(${Math.random() * 360}deg)`;
    document.body.appendChild(p);
    setTimeout(() => p.remove(), 4200);
  }
};
