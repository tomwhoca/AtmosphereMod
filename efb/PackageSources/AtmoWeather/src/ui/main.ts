// The UI module as its own bundle (dist/AtmoUI.js): it sets window.AtmoUI, which the EFB app (AtmoWeather.tsx) and
// the browser harness (efb/harness) mount. Evaluating it again replaces it: that's the reload.

import { mount } from "./app";

declare const UI_BUILD: string;

(window as unknown as { AtmoUI: unknown }).AtmoUI = { mount, build: typeof UI_BUILD === "string" ? UI_BUILD : "dev" };
