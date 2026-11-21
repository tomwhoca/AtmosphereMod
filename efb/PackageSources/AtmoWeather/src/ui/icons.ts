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
  coffee: svg('<path d="M5 9h11v4.5A5.5 5.5 0 0 1 10.5 19h0A5.5 5.5 0 0 1 5 13.5V9z"/>'
    + '<path d="M16 10.5h1.2a2.6 2.6 0 0 1 0 5.2H15.6M8.5 3.5c-.6 1 .6 1.6 0 2.8M12 3.5c-.6 1 .6 1.6 0 2.8"/>'),
  heart: svg('<path d="M12 19.5s-7-4.3-7-9.6A3.9 3.9 0 0 1 12 7.6a3.9 3.9 0 0 1 7 2.3c0 5.3-7 9.6-7 9.6z"/>'),
  close: svg('<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>'),
  sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4'
    + 'M5.6 18.4L7 17M17 7l1.4-1.4"/>'),
  haze: svg('<path d="M4 8h16M6.5 12h11M4 16h16M8.5 20h7"/>'),
  sliders: svg('<path d="M4.5 7h9M18.5 7h1M4.5 17h3M11.5 17h8"/><circle cx="16" cy="7" r="2.2"/><circle cx="9.5" cy="17" r="2.2"/>'),
  palette: svg('<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 1.9-1 1.4-2-.6-1.2.3-2.4 1.6-2.4h1.9a3.6 3.6 0 0 0 3.6-3.6'
    + 'C20.5 7.4 16.7 3.5 12 3.5z"/><circle cx="7.8" cy="11.5" r="1.1"/><circle cx="10.5" cy="7.6" r="1.1"/>'
    + '<circle cx="15.2" cy="8.2" r="1.1"/>'),
  rain: svg('<path d="M7 14.5h9.5a3.5 3.5 0 0 0 .4-7 5 5 0 0 0-9.6.8A3.2 3.2 0 0 0 7 14.5z"/>'
    + '<path d="M9 17.5l-1 2.5M13 17.5l-1 2.5M17 17.5l-1 2.5"/>'),
  contrast: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="currentColor"/>'),
  bug: svg('<rect x="8" y="6.5" width="8" height="13" rx="4"/><path d="M12 11v8.5M4.5 11.5H8M16 11.5h3.5'
    + 'M5.5 7l2.7 2M18.5 7l-2.7 2M5.5 18l2.7-1.8M18.5 18l-2.7-1.8M9.8 5.2L8.5 3.5M14.2 5.2l1.3-1.7"/>'),
  globe: svg('<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.5 3.5 5.3 3.5 8.5s-1.1 6-3.5 8.5'
    + 'c-2.4-2.5-3.5-5.3-3.5-8.5s1.1-6 3.5-8.5z"/>'),
  search: svg('<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>'),
  sort: svg('<path d="M4 7h9M4 12h6.5M4 17h4"/><path d="M17.5 5v14M14.5 16l3 3 3-3"/>'),
  filter: svg('<path d="M4 5.5h16l-6.2 7.3v5.4l-3.6 1.8v-7.2z"/>'),
  check: svg('<path d="M5.5 12.5l4 4 9-9"/>'),
  out: svg('<path d="M14 4.5h5.5V10M19.5 4.5L11 13"/>'
    + '<path d="M17.5 14v4.5a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5V8.5A1.5 1.5 0 0 1 6 7h4.5"/>'),
};

/** An icon as an element. */
export function icon(name: string): HTMLElement {
  const span = document.createElement("span");
  span.innerHTML = ICONS[name] || "";
  return (span.firstElementChild as HTMLElement) || span;
}
