// What's new: a popup over the app, once per release that has one, until it's closed. Which one was
// closed last is kept in the sim's data store (the SDK's DataStore, which the EFB itself uses for its
// search history), so it shows again only for the next release's.

import { TTButton } from "@efb/efb-api";
import { ComponentProps, DataStore, DisplayComponent, FSComponent, Subject, VNode } from "@microsoft/msfs-sdk";

/** The release the popup is for, and what it says: change it with every release that has news for the
 * tablet (a release without news leaves it, so nothing shows again). */
export const WHATS_NEW = {
  release: "0.7.0",
  title: "New in AtmosphereMod 0.7",
  items: [
    ["Varied cloud tops",
      "Air doesn't rise the same everywhere, so clouds in one layer no longer all end at the same height: "
      + "where the air rises more, they grow taller. On by default (C37 in tunables.ini)."],
    ["Sun-facing cloud sides",
      "A new slider in the Cloud lighting tab, off by default: light on the sides of clouds that face the "
      + "sun, as real, thick cloud reflects it. If you turn it on, set Powder effect to 0 %."],
    ["Haze strength",
      "A new slider in the Haze tab, under Haze from METAR, for when the reported haze looks too strong or "
      + "too weak to you. 100 % is as reported."],
    ["Smoother haze changes",
      "When a station's new report changes between an open-ended visibility (9999, 10SM, CAVOK) and a "
      + "number, the haze now fades over 5 minutes instead of jumping."],
    ["Using RenoDX?",
      "The installer has a parameter that lets the two mods work together: see \"With RenoDX\" in the "
      + "README."],
  ] as [string, string][],
};

/** The data store's key (the release whose popup was closed last). */
export const WHATS_NEW_KEY = "atmospheremod.whats-new-closed";

export interface Store {
  get(key: string): unknown;
  set(key: string, value: unknown): void;
}

/** The sim's data store; if it isn't there, the popup shows each time the app starts. */
const simStore: Store = {
  get: (key) => DataStore.get(key),
  set: (key, value) => DataStore.set(key, value as string),
};

export class WhatsNewModel {
  public readonly open: Subject<boolean>;

  public constructor(private readonly store: Store = simStore) {
    let closed: unknown;
    try {
      closed = store.get(WHATS_NEW_KEY);
    } catch {
      closed = undefined;
    }
    this.open = Subject.create(WHATS_NEW.items.length > 0 && closed !== WHATS_NEW.release);
  }

  public close(): void {
    this.open.set(false);
    try {
      this.store.set(WHATS_NEW_KEY, WHATS_NEW.release);
    } catch {
      // (shown again next time)
    }
  }
}

interface WhatsNewViewProps extends ComponentProps {
  model: WhatsNewModel;
}

/** Over the app's page, between the EFB's status bar and its dock (as the page is, Settings.tsx). */
export class WhatsNewView extends DisplayComponent<WhatsNewViewProps> {
  public render(): VNode {
    const { model } = this.props;
    return (
      <div class={{ "atmo-news": true, "atmo-hidden": model.open.map((o) => !o) }}>
        <div class="atmo-news-card">
          <div class="atmo-news-title">{WHATS_NEW.title}</div>
          {WHATS_NEW.items.map(([head, text]) => (
            <div class="atmo-news-item">
              <div class="atmo-news-head">{head}</div>
              <div class="atmo-news-text">{text}</div>
            </div>
          ))}
          <div class="atmo-buttons">
            <TTButton key="Got it" type="secondary" callback={() => model.close()} />
          </div>
        </div>
      </div>
    );
  }
}

export const WHATS_NEW_CSS = `
.atmo-news { position: absolute; top: 96px; right: 0; bottom: 96px; left: 0; z-index: 30; overflow-y: auto;
  box-sizing: border-box; padding: 24px 32px; background-color: rgba(0, 0, 0, 0.6); }
.atmo-news-card { box-sizing: border-box; max-width: 900px; margin: 0 auto; padding: 24px 28px 12px;
  background-color: rgb(15, 20, 27); border: 1px solid rgb(63, 67, 73); border-radius: 8px; color: #e6e6e6; }
.atmo-news-title { font-size: 32px; margin-bottom: 18px; }
.atmo-news-item { margin-bottom: 16px; }
.atmo-news-head { font-size: 26px; margin-bottom: 4px; }
.atmo-news-text { font-size: 23px; line-height: 1.35; opacity: 0.85; }
.atmo-narrow .atmo-news { padding: 16px 20px; }
`;
