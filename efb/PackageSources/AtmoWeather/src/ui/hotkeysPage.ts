// The Hotkeys page: the mod's keys, changed by pressing the new keys in the sim (the add-on learns them: src/hotkeys.ts).
// Each action: its keys (or "Press the new keys ..." while the add-on waits), Change (Cancel while it waits), Clear,
// Default. Under them the add-on's note on the last change and other add-ons' keys that are the same.

import { DEFAULT_HOTKEYS, HOTKEY_ACTIONS, hotkeyClashes, hotkeyNoteText, KEYS_ALLOWED, LEARN_SECONDS } from "../hotkeys";
import { button, section, tileLabel, tipPanel, toggleTip } from "./controls";
import { h, Scope, Value } from "./dom";
import { icon } from "./icons";
import { Store } from "./model";

export function hotkeysPage(scope: Scope, store: Store): HTMLElement {
  // (the add-on has answered and has hotkeys to change: 0.7.3 on)
  const live = new Value(false);
  const keysNow = (): Record<string, string> => (store.state.get() && store.state.get()!.hotkeys) || DEFAULT_HOTKEYS;
  const learning = (): string => (store.state.get() && store.state.get()!.learning) || "";

  const rows = h("div");
  const refresh: (() => void)[] = [];
  for (const action of HOTKEY_ACTIONS) {
    const info = h("div", { class: "icon-btn info", title: "What it does" }, icon("info"));
    const keys = h("div", { class: "keys" });
    const changeText = new Value("Change");
    const change = button(scope, changeText, "small", () => store.learnHotkey(learning() === action.name ? "" : action.name),
                          live);
    const clear = button(scope, "Clear", "small", () => store.setHotkey(action.name, ""), live);
    const dflt = action.default !== ""
      ? button(scope, "Default", "small", () => store.setHotkey(action.name, action.default), live) : null;
    const row = h("div", { class: "row hotkey" },
      h("div", { class: "text" }, h("div", { class: "label" }, h("span", { text: action.label }), info)),
      h("div", { class: "hk" }, keys, h("div", { class: "btns" }, change, clear, dflt)),
      tipPanel(action.tip));
    scope.listen(info, "click", () => toggleTip(row));
    refresh.push(() => {
      const all = keysNow();
      const waiting = learning() === action.name;
      keys.textContent = waiting ? "Press the new keys ..." : all[action.name] || "none";
      keys.className = `keys${waiting ? " learning" : all[action.name] ? "" : " none"}`;
      changeText.set(waiting ? "Cancel" : "Change");
      // (a developer's action only where the add-on lists it: a developer's install)
      row.style.display = action.developer && !(action.name in all) ? "none" : "";
    });
    rows.appendChild(row);
  }

  const note = h("div", { class: "note" });
  const clashes = h("div", { class: "note warn lines" });
  const older = h("div", { class: "note", text: "Changing the hotkeys needs the add-on of this version: install the mod again." });
  scope.add(store.state.watch((s) => {
    live.set(!!s && s.hotkeys !== undefined);
    refresh.forEach((f) => f());
    const n = s ? hotkeyNoteText(s.hotkeyNote || "", keysNow()) : "";
    note.textContent = n;
    note.style.display = n ? "" : "none";
    const c = s ? hotkeyClashes(keysNow(), s.otherHotkeys || []).join("\n") : "";
    clashes.textContent = c;
    clashes.style.display = c ? "" : "none";
    older.style.display = s && s.hotkeys === undefined ? "" : "none";
  }));
  // (the mod's own switch, the key most use, in large)
  const toggleKey = h("div", { class: "big" });
  scope.add(store.state.watch(() => { toggleKey.textContent = keysNow().toggle || "No key"; }));
  const hero = h("div", { class: "tiles" }, h("div", { class: "tile aurora wide" },
    tileLabel("keys", "Keys in the Sim"), toggleKey,
    h("div", { class: "bigsub", text: "The whole mod on or off, to compare with the sim's own" }),
    h("p", { class: "tp", text: `Tap Change, then press the new keys in the sim within ${LEARN_SECONDS} seconds: `
                                + `${KEYS_ALLOWED}. The keys work while the sim's window is in front.` })));
  return h("div", null, hero, section("Your Keys", "keys", rows, note, clashes, older));
}
