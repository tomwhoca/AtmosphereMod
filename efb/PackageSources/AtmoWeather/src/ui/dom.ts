// The UI module's own small toolkit: a value others can watch, and an element builder. No SDK: the tablet's engine
// (CoherentGT 2.0, about Chromium 49) and a browser both run it.

/** A value that tells its watchers when it changes. */
export class Value<T> {
  private readonly watchers: ((value: T) => void)[] = [];

  public constructor(private value: T) {}

  public get(): T {
    return this.value;
  }

  public set(value: T): void {
    if (value === this.value) {
      return;
    }
    this.value = value;
    for (const w of this.watchers.slice()) {
      w(value);
    }
  }

  /** Calls `watcher` now and on every change; returns the unsubscribe. */
  public watch(watcher: (value: T) => void): () => void {
    this.watchers.push(watcher);
    watcher(this.value);
    return () => {
      const i = this.watchers.indexOf(watcher);
      if (i >= 0) {
        this.watchers.splice(i, 1);
      }
    };
  }
}

type Child = Node | string | number | null | undefined | false;
interface Props {
  class?: string;
  style?: string;
  text?: string;
  title?: string;
  [event: `on${string}`]: ((e: Event) => void) | undefined;
}

/** An element: h("div", {class: "a", onclick: f}, child, "text", ...). */
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, props?: Props | null,
                                                         ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (props) {
    for (const key of Object.keys(props)) {
      const value = (props as Record<string, unknown>)[key];
      if (value === undefined) {
        continue;
      }
      if (key === "class") {
        el.className = String(value);
      } else if (key === "style") {
        el.setAttribute("style", String(value));
      } else if (key === "text") {
        el.textContent = String(value);
      } else if (key === "title") {
        el.title = String(value);
      } else if (key.indexOf("on") === 0 && typeof value === "function") {
        el.addEventListener(key.slice(2), value as EventListener);
      }
    }
  }
  for (const c of children) {
    if (c === null || c === undefined || c === false) {
      continue;
    }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return el;
}

/** Collects unsubscribes and listeners to drop when a screen goes. */
export class Scope {
  private readonly drops: (() => void)[] = [];

  public add(drop: () => void): void {
    this.drops.push(drop);
  }

  public listen(target: EventTarget, type: string, listener: EventListener): void {
    target.addEventListener(type, listener);
    this.drops.push(() => target.removeEventListener(type, listener));
  }

  public dispose(): void {
    while (this.drops.length > 0) {
      this.drops.pop()!();
    }
  }
}
