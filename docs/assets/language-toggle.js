// Injects a language switcher into the mdBook top bar. The combined book serves
// each language under its own path segment (/en/, /de/), so the counterpart URL
// is the current path with that segment swapped. `labels` is the single source
// of truth: add a language here and it joins the rotation automatically.
(function () {
  "use strict";

  const labels = { en: "EN", de: "DE" };
  const codes = Object.keys(labels);

  const match = window.location.pathname.match(new RegExp("/(" + codes.join("|") + ")/"));

  if (!match) {
    return;
  }

  const current = match[1];
  const target = codes[(codes.indexOf(current) + 1) % codes.length];

  const link = document.createElement("a");
  link.className = "icon-button";
  link.href = window.location.pathname.replace("/" + current + "/", "/" + target + "/");
  link.setAttribute("aria-label", "Switch language");
  link.textContent = labels[target];
  link.style.fontWeight = "600";

  const buttons = document.querySelector(".right-buttons");

  if (buttons) {
    buttons.insertBefore(link, buttons.firstChild);
  }
})();
