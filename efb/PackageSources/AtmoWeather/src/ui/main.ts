// The UI module as its own bundle (dist/AtmoUI.js): it sets window.AtmoUI, which the EFB app (AtmoWeather.tsx,
// mountInSim) and the browser harness (efb/harness, mount) mount. Evaluating it again replaces it: that's the reload.

import { mount } from "./app";
import { SimBridge, SimHost } from "./sim";

declare const UI_BUILD: string;

/** The UI in the sim, on the shell's bridge; the unmount also lets go of the bridge (a reload mounts anew). */
function mountInSim(root: HTMLElement, bridge: SimBridge): () => void {
  const host = new SimHost(bridge, () => root);
  const unmount = mount(root, host);
  return () => {
    unmount();
    host.dispose();
  };
}

(window as unknown as { AtmoUI: unknown }).AtmoUI = {
  mount, mountInSim, build: typeof UI_BUILD === "string" ? UI_BUILD : "dev",
};
