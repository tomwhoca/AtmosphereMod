// The Tools page: the look (dark or light), the performance report and What's new (each opens its sheet), the
// settings file (read it again; every setting back to the default), and what's installed.

import { TABS } from "../specs";
import { WHATS_NEW } from "../whatsnew";
import { button, card, twoTaps } from "./controls";
import { h, Scope, Value } from "./dom";
import { Store } from "./model";

export type SheetName = "" | "news" | "report";

export function toolsPage(scope: Scope, store: Store, theme: Value<string>, open: (s: SheetName) => void): HTMLElement {
  const live = new Value(false);
  scope.add(store.state.watch((s) => live.set(!!s)));

  const look = (name: string, label: string) => {
    const b = button(scope, label, "", () => theme.set(name));
    scope.add(theme.watch((t) => { b.className = `btn${t === name ? " chosen" : ""}`; }));
    return b;
  };
  const appearance = card("Appearance",
    h("div", { class: "actions" }, look("auto", "Auto"), look("dark", "Dark"), look("light", "Light")),
    h("div", { class: "note", text: "Auto: light by day in the sim, dark at dawn, dusk and night." }));

  const report = card("Performance report",
    h("div", { class: "muted", text: "Measures what the mod costs on your PC, with it and without, and if you like "
                                     + "sends me the result. About a minute, in a view with clouds." }),
    h("div", { class: "actions" }, button(scope, "Open", "primary", () => open("report"), live)));

  const news = card("What's new",
    h("div", { class: "muted", text: `${WHATS_NEW.title}, and the changes in earlier versions.` }),
    h("div", { class: "actions" }, button(scope, "Open", "", () => open("news"))));

  const reloadKey = h("span", { class: "muted" });
  scope.add(store.state.watch((s) => {
    const k = s && s.hotkeys ? s.hotkeys.reload : "Ctrl+Shift+F10";
    reloadKey.textContent = k ? `(also ${k})` : "";
  }));
  const allKeys = TABS.reduce<string[]>((all, t) => all.concat(t.settings.map((s) => s.key)), []);
  const file = card("Settings",
    h("div", { class: "muted", text: "The settings live in tunables.ini. Read it again after editing it by hand; it "
                                     + "also reloads the mod's shaders." }),
    h("div", { class: "actions" }, button(scope, "Reload tunables.ini", "", () => store.reload(), live), reloadKey),
    h("div", { class: "actions" },
      twoTaps(scope, "Reset all settings", "Every setting to its default? Tap again", "danger",
              () => store.reset(allKeys), () => true, live)));

  const about = h("table", { class: "kv" });
  const fill = () => {
    about.innerHTML = "";
    const s = store.state.get();
    const row = (k: string, v: string) => about.appendChild(h("tr", null, h("td", { text: k }), h("td", { text: v })));
    row("EFB app", store.host.version);
    row("Add-on in the sim", s ? s.version || "an older one" : "not connected");
    row("METAR link", store.link.get() || "-");
  };
  scope.add(store.state.watch(fill));
  scope.add(store.link.watch(fill));
  return h("div", null, report, news, file, h("div", { class: "cols" }, appearance, card("About", about)));
}
