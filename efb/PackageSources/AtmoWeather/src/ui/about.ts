// About This Mod: the user's own words about the project and how to support it (2026-11-21), as a golden tile with
// the two donation buttons. On Home, and at the end of What's New (the user's wish: always there).

import { button, tileLabel } from "./controls";
import { h, Scope } from "./dom";
import { icon } from "./icons";

/** Where a donation goes: the README's and flightsim.to's coffee link, and the user's PayPal donation page (a
 * hosted button: no address in the link). */
export const SUPPORT = {
  coffee: "https://buymeacoffee.com/tomwhoca",
  paypal: "https://www.paypal.com/donate/?hosted_button_id=CHZN2J8XPLNJU",
};

export const ABOUT_TEXT = [
  "AtmosphereMod aims to bring you a more realistic experience in MSFS 2024 by improving the sim's atmospheric "
  + "rendering. It's an experimental project that I develop in my free time, made possible by the community's "
  + "engagement and support.",
  "If you enjoy it, a small donation helps me keep improving it, and it's much appreciated. Thank you!",
];

/** The tile: the text, Buy me a coffee and PayPal (opened in the PC's browser by `openUrl`). */
export function aboutTile(scope: Scope, openUrl: (url: string) => void): HTMLElement {
  const coffee = button(scope, "Buy me a coffee", "primary", () => openUrl(SUPPORT.coffee));
  coffee.insertBefore(icon("coffee"), coffee.firstChild);
  const paypal = button(scope, "PayPal", "", () => openUrl(SUPPORT.paypal));
  paypal.insertBefore(icon("heart"), paypal.firstChild);
  return h("div", { class: "tile sun wide" }, tileLabel("heart", "About This Mod"),
    ...ABOUT_TEXT.map((text) => h("p", { class: "about", text })),
    h("div", { class: "actions" }, coffee, paypal));
}
