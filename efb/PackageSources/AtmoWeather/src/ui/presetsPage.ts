// The Presets page: the settings saved by name. Save (a name typed here, the keyboard asked of the sim; a name taken
// asks for a second tap), the list with Load, Share and Delete (a second tap where something would be lost), a preset
// that lacks newer settings asks how to fill them in, an import from a file (the add-on's file dialog); and Community
// Presets, the presets other pilots share (src/community.ts): searched, sorted, filtered by the version they were made
// with, a page at a time; add one to your presets.

import {
  changesFromCurrent, CommunityApi, compatibleVersions, diffNote, diffShort, ListQuery, pageCount, QUERY_MAX,
  sameSettings, settingsDiff, SharedPreset, SORTS, VersionCount,
} from "../community";
import {
  missingText, NAME_RULE, PRESET_DEFAULT, PRESET_NAME_MAX, presetChar, presetName, sameName, settingsInWords,
} from "../presets";
import { button, card, iconMenu, MenuItem, textField, twoTaps } from "./controls";
import { h, Scope, Value } from "./dom";
import { icon } from "./icons";
import { Store } from "./model";

/** A second tap on a button waits this long. */
const ARMED_MS = 4000;
/** A search starts this long after the last key typed (Enter: at once). */
const SEARCH_WAIT_MS = 700;
/** The versions filter: those with the same settings as the installed version (the default), all, or one ("v:0.7.3"). */
const COMPATIBLE = "compatible", ALL_VERSIONS = "all";
const SORT_PHRASE: Record<string, string> = { new: "Newest first", added: "Most added first", name: "A to Z" };

export function presetsPage(scope: Scope, store: Store, focusName: Value<number>, community: CommunityApi,
                            share: (name: string) => void): HTMLElement {
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
    // (an add from Community Presets: its note is in that card)
    const added = /from Community Presets$/.test((s && s.presetNote) || "");
    const t = local.get() || (!added && s && s.presetNote) || "";
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
  /** A preset Share was tapped on that lacks newer settings: shared only once it has them all (so others get it as
   * its author sees it, and its version's settings aren't taken for fewer than they are). */
  const notYet = new Value("");
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
    const key = JSON.stringify([presets(), s && s.presetsMissing, store.preset, store.presetSaved, asking.get(),
                                store.hasCommunity, notYet.get()]);
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
        sameName(n, PRESET_DEFAULT) || !store.hasCommunity ? null : button(ls, "Share", "", () => {
          local.set("");
          asking.set("");
          if (missingOf(n).length > 0) {
            notYet.set(n);
          } else {
            notYet.set("");
            share(n);
          }
        }, live),
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
      if (notYet.get() === n && missing.length > 0) {
        const count = `${missing.length} newer setting${missing.length === 1 ? "" : "s"}`;
        const them = missing.length === 1 ? "it" : "them";
        list.appendChild(h("div", { class: "ask" },
          h("div", { text: `\u201c${n}\u201d was saved before ${count} existed (${settingsInWords(missing)}). `
                     + (current ? `Save it to add ${them} as they are now, then share it.`
                       : `Load it once and choose how to fill ${them} in, then share it.`) }),
          h("div", { class: "btns" },
            current ? button(ls, "Save it now", "primary", () => {
              notYet.set("");
              store.saveInPlace();
            }, live) : button(ls, "Load it", "primary", () => {
              notYet.set("");
              load(n);
            }, live),
            button(ls, "Cancel", "", () => notYet.set("")))));
      }
    }
  };
  scope.add(store.state.watch(render));
  scope.add(asking.watch(render));
  scope.add(notYet.watch(render));
  return h("div", null, saveCard, card("Your presets", list), older, communityCard(scope, store, community));
}

/** Community Presets: the presets other pilots share, a page at a time. Three icons in its heading: search (opens
 * a field: name, author, description), sort (a menu), versions (a menu: by default those with the same settings as
 * the installed version; all, or one, the ones with other settings marked); it loads again after a share. Each
 * preset: its name and author, the version it was made with, how often it was added, its description, how its
 * settings differ from yours (if they do), what it would change in your settings (folded), Add to my presets. */
function communityCard(scope: Scope, store: Store, api: CommunityApi): HTMLElement {
  const list = h("div", { class: "shared-list" });
  const note = h("div", { class: "note" });
  const showNote = (t: string) => {
    note.textContent = t;
    note.style.display = t ? "" : "none";
  };
  showNote("");
  // (the add-on's note on an add: "Added \u201cX (2)\u201d from Community Presets")
  scope.add(store.state.watch((s) => {
    const n = (s && s.presetNote) || "";
    if (/from Community Presets$/.test(n)) {
      showNote(`${n}: load it under Your presets.`);
    }
  }));
  let alive = true;
  scope.add(() => { alive = false; });
  // (adding and sharing need an add-on with Community Presets: known once its state has come)
  const canAdd = new Value(false);
  const older = h("div", { class: "note", text: "Adding and sharing presets need the add-on of this version: install the "
                                                + "mod again." });
  scope.add(store.state.watch((s) => {
    canAdd.set(store.hasCommunity);
    older.style.display = s && !store.hasCommunity ? "" : "none";
  }));

  // ---- what's asked: the search, the sort, the versions, the page
  const search = new Value("");
  const sortBy = new Value<string>(SORTS[0].by);
  const versionPick = new Value<string>(COMPATIBLE);
  /** The versions there are and the settings each has (undefined: not known yet). */
  const versions = new Value<VersionCount[] | undefined>(undefined);
  let page = 1;
  /** The versions asked for (none: all); null while they aren't known. */
  const wantedVersions = (): string[] | null => {
    const pick = versionPick.get();
    if (pick === ALL_VERSIONS) {
      return [];
    }
    if (pick !== COMPATIBLE) {
      return [pick.slice(2)];
    }
    const vs = versions.get();
    return vs ? compatibleVersions(vs) : null;
  };

  // the search: its icon opens the field (and closing it ends the search)
  const searchOpen = new Value(false);
  const field = textField(scope, store.host, { placeholder: "Search names, authors and descriptions", value: search,
                                               max: QUERY_MAX, enter: () => fresh() });
  const closeSearch = h("div", { class: "tool", title: "Close the search" }, icon("close"));
  const searchRow = h("div", { class: "searchrow" }, field, closeSearch);
  const searchBtn = h("div", { class: "tool", title: "Search" }, icon("search"));
  const markSearch = () => {
    searchRow.style.display = searchOpen.get() ? "" : "none";
    searchBtn.className = `tool${searchOpen.get() ? " open" : ""}${search.get().trim() ? " set" : ""}`;
  };
  scope.add(searchOpen.watch(markSearch));
  scope.add(search.watch(markSearch));
  scope.listen(searchBtn, "click", () => {
    searchOpen.set(!searchOpen.get());
    if (searchOpen.get()) {
      setTimeout(() => field.focus(), 0);
    } else {
      field.blur();
      search.set("");
      fresh();
    }
  });
  scope.listen(closeSearch, "click", () => {
    field.blur();
    searchOpen.set(false);
    search.set("");
    fresh();
  });

  // the sort and the versions: menus
  const sortItems = new Value<readonly MenuItem[]>(SORTS.map((s) => ({ id: s.by, label: s.label })));
  const versionItems = new Value<readonly MenuItem[]>([]);
  scope.add(versions.watch((vs) => {
    const same = vs ? compatibleVersions(vs) : [];
    const items: MenuItem[] = [
      { id: COMPATIBLE, label: "Compatible versions",
        sub: !vs ? "Loading ..." : same.length ? `The same settings as yours: ${same.map((v) => `v${v}`).join(", ")}`
          : "None shared with the same settings as yours yet" },
      { id: ALL_VERSIONS, label: "All versions", sub: "Also those with other settings" },
    ];
    for (const v of vs || []) {
      const d = settingsDiff(v.keys);
      const count = `${v.count} preset${v.count === 1 ? "" : "s"}`;
      items.push({ id: `v:${v.version}`, label: `v${v.version}${v.version === store.host.version ? " (yours)" : ""}`,
                   sub: sameSettings(d) ? count : `${count}: ${diffShort(d)}`, warn: !sameSettings(d) });
    }
    versionItems.set(items);
  }));
  const tools = h("div", { class: "tools" }, searchBtn,
    iconMenu(scope, "sort", "Sort", sortItems, sortBy, SORTS[0].by),
    iconMenu(scope, "filter", "Versions", versionItems, versionPick, COMPATIBLE));

  // what's shown, in a line under the heading
  const status = h("div", { class: "muted status" });
  const showStatus = () => {
    const pick = versionPick.get();
    const q = search.get().trim();
    status.textContent = [SORT_PHRASE[sortBy.get()] || "",
      pick === ALL_VERSIONS ? "all versions" : pick === COMPATIBLE ? "compatible versions" : `v${pick.slice(2)} only`,
      q ? `\u201c${q}\u201d` : ""].filter((x) => x).join(" \u00b7 ");
  };
  scope.add(sortBy.watch(showStatus));
  scope.add(versionPick.watch(showStatus));
  scope.add(search.watch(showStatus));

  // ---- the pages
  const pagerText = h("span", { class: "muted" });
  const hasPrev = new Value(false), hasNext = new Value(false);
  const prev = button(scope, "Previous", "small", () => turn(-1), hasPrev);
  const next = button(scope, "Next", "small", () => turn(1), hasNext);
  const pager = h("div", { class: "pager", style: "display: none" }, prev, pagerText, next);
  const head = h("div", { class: "card-head" }, h("h2", { text: "Community Presets" }), tools);

  /** The card's top back in view after a page turned (when it's above the page's scrolled view). */
  const topInView = () => {
    let el = list.parentElement;
    while (el && !(el.scrollHeight > el.clientHeight && /auto|scroll/.test(getComputedStyle(el).overflowY || ""))) {
      el = el.parentElement;
    }
    if (el) {
      const top = head.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop;
      if (top < el.scrollTop) {
        el.scrollTop = Math.max(0, top - 8);
      }
    }
  };

  let listScope = new Scope();
  scope.add(() => listScope.dispose());
  /** A message instead of the list (and a way to try again). */
  const instead = (text: string, again?: () => void) => {
    listScope.dispose();
    listScope = new Scope();
    list.innerHTML = "";
    pager.style.display = "none";
    list.appendChild(h("div", { class: "muted", text }));
    if (again) {
      list.appendChild(h("div", { class: "actions" }, button(listScope, "Try again", "", again)));
    }
  };
  const loadVersions = () => {
    instead("Loading ...");
    void api.versions().then((r) => {
      if (!alive) {
        return;
      }
      if (!r.ok) {
        instead(`${r.error}.`, loadVersions);
        return;
      }
      versions.set(r.value);
      wanted = "";
      fresh();
    });
  };
  let asked = 0;
  const load = (turned: boolean) => {
    const n = ++asked;
    const vs = wantedVersions();
    if (vs === null) {
      return;  // (the versions come first: loadVersions asks again)
    }
    if (vs.length === 0 && versionPick.get() === COMPATIBLE) {
      instead((versions.get() || []).length ? "None shared with the same settings as your version yet. The versions "
        + "icon above lists the others: their presets work too, with some settings at the mod's defaults."
        : "Nothing shared yet: share one of yours with its Share button above.");
      return;
    }
    const qy: ListQuery = { sort: sortBy.get() as ListQuery["sort"], versions: vs, q: search.get().trim(), page };
    if (list.childNodes.length === 0) {
      list.appendChild(h("div", { class: "muted", text: "Loading ..." }));
    } else {
      pagerText.textContent = "Loading ...";
    }
    void api.list(qy).then((r) => {
      if (!alive || n !== asked) {
        return;
      }
      if (!r.ok) {
        instead(`${r.error}.`, () => load(false));
        return;
      }
      const v = r.value;
      if (v.presets.length === 0 && v.total > 0 && page > 1) {
        // (fewer since: the last page there is)
        page = pageCount(v.total, v.per);
        load(turned);
        return;
      }
      listScope.dispose();
      listScope = new Scope();
      list.innerHTML = "";
      if (v.total === 0) {
        list.appendChild(h("div", { class: "muted", text: qy.q ? `Nothing found for \u201c${qy.q}\u201d.`
          : versionPick.get() !== ALL_VERSIONS && versionPick.get() !== COMPATIBLE
            ? `None made with v${versionPick.get().slice(2)} any more.`
          : "Nothing shared yet: share one of yours with its Share button above." }));
      }
      v.presets.forEach((p) => list.appendChild(entry(listScope, p)));
      const pages = pageCount(v.total, v.per);
      page = v.page;
      hasPrev.set(page > 1);
      hasNext.set(page < pages);
      pagerText.textContent = `Page ${page} of ${pages} \u00b7 ${v.total} preset${v.total === 1 ? "" : "s"}`;
      pager.style.display = v.total > v.per ? "" : "none";
      if (turned) {
        topInView();
      }
    });
  };
  const turn = (by: number) => {
    page = Math.max(1, page + by);
    load(true);
  };
  /** From the first page again: the search, the sort or the versions changed. */
  let wanted = "";
  const fresh = () => {
    if (searchTimer) {
      clearTimeout(searchTimer);
      searchTimer = undefined;
    }
    const key = JSON.stringify([sortBy.get(), wantedVersions(), versionPick.get(), search.get().trim()]);
    if (key === wanted) {
      return;
    }
    wanted = key;
    page = 1;
    load(false);
  };
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  scope.add(() => searchTimer && clearTimeout(searchTimer));
  scope.add(search.watch(() => {
    if (searchTimer) {
      clearTimeout(searchTimer);
    }
    searchTimer = setTimeout(fresh, SEARCH_WAIT_MS);
  }));
  scope.add(sortBy.watch(fresh));
  scope.add(versionPick.watch(fresh));

  // ---- a preset
  const entry = (ls: Scope, p: SharedPreset): HTMLElement => {
    // what it would change in the settings as they are (the mod's defaults until the add-on has answered); again
    // when they change
    const details = h("div", { class: "details", style: "display: none" });
    const changesText = h("span");
    const compare = () => {
      const live = store.connected;
      const c = live ? changesFromCurrent(p.values, (k) => store.raw(k))
        : changesFromCurrent(p.values, (k) => store.defaultOf(k), "default");
      const lines = c.lines.concat(c.hidden ? [`And ${c.hidden} the app doesn't show.`] : []);
      const text = lines.length ? lines.join("\n")
        : live ? "The same as your current settings." : "The same as the mod's defaults.";
      const label = `What it changes ${live ? "in your settings" : "from the defaults"} (${c.lines.length + c.hidden})`;
      if (details.textContent !== text) {
        details.textContent = text;
      }
      if (changesText.textContent !== label) {
        changesText.textContent = label;
      }
    };
    ls.add(store.state.watch(compare));
    const chevron = h("span", null, icon("down"));
    const toggle = h("div", { class: "more" }, changesText, chevron);
    ls.listen(toggle, "click", () => {
      const open = details.style.display === "none";
      details.style.display = open ? "" : "none";
      chevron.innerHTML = "";
      chevron.appendChild(icon(open ? "up" : "down"));
    });
    const meta = h("div", { class: "meta", text: [p.version ? `Made with v${p.version}` : "Version unknown",
      p.downloads ? `added ${p.downloads}\u00d7` : "not added yet"].join(" \u00b7 ") });
    const differs = diffNote(settingsDiff(Object.keys(p.values)));
    const add = button(ls, "Add to my presets", "primary small", () => {
      showNote("");
      store.addShared(p.name, p.values);
      void api.added(p.id);
    }, canAdd);
    return h("div", { class: "shared" },
      h("div", { class: "label" }, h("span", { class: "pn", text: p.name }),
        h("span", { class: "by", text: `by ${p.author}` })),
      meta,
      p.description ? h("div", { class: "shared-desc", text: p.description }) : null,
      differs ? h("div", { class: "differs", text: differs }) : null,
      toggle, details,
      h("div", { class: "btns" }, add));
  };

  // (a share from here: it's in the list now)
  let shares = store.shared.get();
  scope.add(store.shared.watch((n) => {
    if (n !== shares) {
      shares = n;
      loadVersions();
    }
  }));
  loadVersions();
  return h("div", { class: "card community" }, head,
    h("div", { class: "body" },
      h("div", { class: "muted intro", text: "Presets other pilots share. Add one to your presets, then load it there; "
                                             + "share yours with the Share button above." }),
      older, searchRow, status, list, pager, note));
}
