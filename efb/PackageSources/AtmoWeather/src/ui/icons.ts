// The redesign's icons: 24 x 24 outlines in currentColor (our own drawings).

const svg = (body: string): string =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ` +
  `stroke-linejoin="round">${body}</svg>`;

export const ICONS: Record<string, string> = {
  overview: svg('<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/>'
    + '<rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.8"/>'),
  sky: svg('<circle cx="12" cy="9" r="3.5"/><path d="M12 2.5v1.5M5.6 5.1l1 1M18.4 5.1l-1 1M3.5 15h17M6 19h12"/>'),
  light: svg('<path d="M7.5 18.5h9.2a3.8 3.8 0 0 0 .4-7.6 5.2 5.2 0 0 0-10 .8 3.5 3.5 0 0 0 .4 6.8z"/>'
    + '<path d="M17 3.5v1.6M21 7.5h-1.6M19.8 4.7l-1.1 1.1"/>'),
  clouds: svg('<path d="M6.5 19h11a4 4 0 0 0 .5-8 5.5 5.5 0 0 0-10.6.9A3.6 3.6 0 0 0 6.5 19z"/>'),
  presets: svg('<path d="M6.5 3.5h11a1 1 0 0 1 1 1v16l-6.5-4.2-6.5 4.2v-16a1 1 0 0 1 1-1z"/>'),
  keys: svg('<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M7.5 14h9"/>'),
  tools: svg('<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/><circle cx="12" cy="18" r="1.3"/>'),
  link: svg('<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1.2 1.2"/>'
    + '<path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1.2-1.2"/>'),
  lab: svg('<path d="M9.5 3.5h5M10.5 3.5v6l-5 8.6a1.6 1.6 0 0 0 1.4 2.4h10.2a1.6 1.6 0 0 0 1.4-2.4l-5-8.6v-6"/>'
    + '<path d="M7.6 15h8.8"/>'),
  minus: svg('<path d="M6 12h12"/>'),
  plus: svg('<path d="M12 6v12M6 12h12"/>'),
  reset: svg('<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/>'),
  info: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>'),
  down: svg('<path d="M6 9.5l6 6 6-6"/>'),
  up: svg('<path d="M6 14.5l6-6 6 6"/>'),
};

/** An icon as an element. */
export function icon(name: string): HTMLElement {
  const span = document.createElement("span");
  span.innerHTML = ICONS[name] || "";
  return (span.firstElementChild as HTMLElement) || span;
}
