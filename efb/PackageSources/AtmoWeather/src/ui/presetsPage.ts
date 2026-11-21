// The Presets page: the settings saved by name. Save (a name typed here, the keyboard asked of the sim; a name taken
// asks for a second tap), the list with Load and Delete (a second tap where something would be lost), a preset that
// lacks newer settings asks how to fill them in, an import from a file (the add-on's file dialog).

import { missingText, NAME_RULE, PRESET_DEFAULT, PRESET_NAME_MAX, presetChar, presetName, sameName } from "../presets";
import { button, card, textField, twoTaps } from "./controls";
import { h, Scope, Value } from "./dom";
import { Store } from "./model";

/** A second tap on a button waits this long. */
const ARMED_MS = 4000;

export function presetsPage(scope: Scope, store: Store, focusName: Value<number>): HTMLElement {
  const presets = (): string[] => (store.state.get() && store.state.get()!.presets) || [];
  const missingOf = (name: string): string[] => {
    const s = store.state.get();
    return (s && s.presetsMissing && s.presetsMissing[name]) || [];
  };
  /** Loading another preset would lose changes. */
  const unsaved = (): boolean => !store.presetSaved || store.unconfirmed;
  // (the add-on has answered and has presets: 0.6.0 on)
  const live = new Value(false);
  const older = h("div", { class: "note", text: "Presets need the add-on of this version: install the mod again." });
  scope.add(store.state.watch((s) => {
    live.set(!!s && s.presets !== undefined);
    older.style.display = s && s.presets === undefined ? "" : "none";
  }));

  // the note: why the app didn't send a name (until the next request), else the add-on's
  const local = new Value("");
  const note = h("div", { class: "note" });
  const showNote = () => {
    const s = store.state.get();
    const t = local.get() || (s && s.presetNote) || "";
    note.textContent = t;
    note.style.display = t ? "" : "none";
  };
  scope.add(store.state.watch(showNote));
  scope.add(local.watch(showNote));

  // ---- save
  const name = new Value("");
  const saveText = new Value("Save");
  let replacing = "", timer: ReturnType<typeof setTimeout> | undefined;
  const disarm = () => {
    replacing = "";
    saveText.set("Save");
  };
  scope.add(name.watch(() => replacing && disarm()));
  scope.add(() => timer && clearTimeout(timer));
  const save = () => {
    const n = presetName(name.get());
    if (n === "") {
      local.set(NAME_RULE);
      return;
    }
    if (sameName(n, PRESET_DEFAULT)) {
      local.set(`"${PRESET_DEFAULT}" can't be changed: choose another name`);
      return;
    }
    const existing = presets().filter((p) => sameName(p, n))[0];
    if (existing && replacing !== existing) {
      replacing = existing;
      saveText.set(`Replace "${existing}"? Tap again`);
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(disarm, ARMED_MS);
      return;
    }
    disarm();
    local.set("");
    store.saveAs(existing || n);
    name.set("");
  };
  const field = textField(scope, store.host, { placeholder: "A name for the settings as they are", value: name,
                                               accepts: presetChar, max: PRESET_NAME_MAX, enter: save });
  const saveBtn = button(scope, saveText, "primary", save, live);
  scope.add(saveText.watch((t) => {
    if (t === "Save") {
      saveBtn.classList.remove("armed");
    } else {
      saveBtn.classList.add("armed");
    }
  }));
  scope.add(focusName.watch((n) => {
    if (n > 0) {
      setTimeout(() => field.focus(), 0);
    }
  }));
  const importBtn = button(scope, "Import from a file", "", () => {
    local.set("");
    store.importPreset();
  }, live);
  const saveCard = card("Save the settings as they are",
    h("div", { class: "fieldrow" }, field, saveBtn),
    h("div", { class: "actions" }, h("span", { class: "muted", text: "Or a preset someone shared:" }), importBtn),
    note);

  // ---- the list: made again when what it shows changes (not at each state: a second tap would be lost)
  const list = h("div");
  const asking = new Value("");
  let askedUnsaved = false;
  let listScope = new Scope();
  scope.add(() => listScope.dispose());
  const load = (n: string) => {
    local.set("");
    if (missingOf(n).length > 0) {
      askedUnsaved = unsaved();
      asking.set(n);
      return;
    }
    store.load(n, unsaved());
  };
  /** The question's answer: the settings the preset lacks added to it, with Default's values or the current ones,
   * then loaded. A setting changed since it asked: asked again, with the warning. */
  const answer = (n: string, fill: "defaults" | "current") => {
    asking.set("");
    if (unsaved() && !askedUnsaved) {
      load(n);
      return;
    }
    store.load(n, unsaved(), fill);
  };
  let shown = "";
  const render = () => {
    const s = store.state.get();
    const key = JSON.stringify([presets(), s && s.presetsMissing, store.preset, store.presetSaved, asking.get()]);
    if (key === shown) {
      return;
    }
    shown = key;
    listScope.dispose();
    listScope = new Scope();
    const ls = listScope;
    list.innerHTML = "";
    if (presets().length === 0) {
      list.appendChild(h("div", { class: "muted", text: s ? "No presets yet." : "Waiting for the add-on ..." }));
    }
    for (const n of presets()) {
      const current = sameName(n, store.preset);
      const missing = missingOf(n);
      const tag = current ? h("span", { class: `tag${store.presetSaved ? "" : " unsaved"}`,
                                        text: store.presetSaved ? "ACTIVE" : "ACTIVE, UNSAVED" }) : null;
      const lacks = missing.length > 0
        ? h("div", { class: "tip-line", text: `No value for ${missing.length} newer setting${missing.length === 1 ? "" : "s"}` })
        : null;
      const actions = h("div", { class: "btns" },
        current ? null : twoTaps(ls, "Load", "Discard changes? Tap again", "", () => load(n),
                                 () => unsaved() && missingOf(n).length === 0, live),
        sameName(n, PRESET_DEFAULT) ? null
          : twoTaps(ls, "Delete", "Delete? Tap again", "danger", () => {
            local.set("");
            store.remove(n);
          }, () => true, live));
      const row = h("div", { class: "row preset-row" },
        h("div", { class: "text" }, h("div", { class: "label" }, h("span", { class: "pn", text: n }), tag), lacks),
        actions);
      list.appendChild(row);
      if (asking.get() === n) {
        const them = missing.length === 1 ? "it" : "them";
        list.appendChild(h("div", { class: "ask" },
          h("div", { text: `${missingText(n, missing)}${askedUnsaved ? " Your unsaved changes will be lost." : ""}`
                     + ` Fill ${them} in with the mod's defaults, or with your current values?` }),
          h("div", { class: "btns" },
            button(ls, "Use defaults", "primary", () => answer(n, "defaults"), live),
            button(ls, "Keep current", "", () => answer(n, "current"), live),
            button(ls, "Cancel", "", () => asking.set("")))));
      }
    }
  };
  scope.add(store.state.watch(render));
  scope.add(asking.watch(render));
  return h("div", null, saveCard, card("Your presets", list), older);
}
