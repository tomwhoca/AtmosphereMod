// The redesign's look, second version (2026-11-21, the user's wish: vibrant colours instead of the pastels, a
// design language like a modern mobile app's: soft paper and white cards with large radii, saturated colour tiles
// with a glow, ink-black pills for what's chosen and for the main action, thin ticks as a motif). The palette is the
// atmosphere's own: cerulean (the clear sky), golden hour (the sun low), twilight violet (dusk), alpenglow (the pink
// on clouds and peaks at sunset), aurora green, and a storm red for what can't be undone and for failures. Each
// page has its colour (pg-*: Home and Tools cerulean, Sky & Haze golden, Clouds violet, Presets alpenglow, Keys
// aurora): its switches, value bars and marks. Two variants: light (day: paper and white) and dark (night: ink and
// slate), the colours the same in both. Lengths are written as "16u": u is the tablet's scale against the small one at the first measured
// window (624 px short side), so one design holds at every size and window.
// The engine (CoherentGT 2.0) has no gap, sticky, clamp(), inset: tools/lint_efb.py checks. Its fonts: Roboto in
// four separate faces (Roboto-Light, the tablet's own, -Regular, -Medium, -Bold) and no weights or monospace
// (measured 2026-11-20): weight goes by family (--f-med), not font-weight. The tablet styles some class names
// globally (hidden, disabled, slider, button, title: measured): ours avoid them (tools/lint_efb.py). A gradient
// always has a plain colour before it, so a background the engine can't draw still has its colour.

import { units } from "./dom";

export const THEMES = ["dark", "light"];
/** What the user can choose (Tools > Appearance): a variant, or auto (by the time of day in the sim). */
export const THEME_CHOICES = ["auto", "dark", "light"];

export const UI_CSS = units(`
/* the palette (r, g, b, for rgb() and rgba()), the same in both variants */
.atmo-ui {
  --sky: 47, 128, 247; --sun: 255, 190, 46; --dusk: 124, 92, 255; --glow: 242, 78, 142; --aurora: 18, 194, 140;
  --storm: 235, 72, 72;
  /* the page's colour (pg-* below) and the text on it */
  --acc: var(--sky); --on-acc: #ffffff;
  /* what it means: working, waiting or unsaved, can't be undone, experimental */
  --ok: var(--aurora); --rose: var(--storm); --exp: var(--dusk); }
.atmo-ui.pg-sky { --acc: var(--sun); --on-acc: #2a1d00; }
/* (marks in the page's colour on a card: the golden one deepened to be seen on white) */
.atmo-ui { --acc-mark: var(--acc); }
.atmo-ui.light.pg-sky { --acc-mark: 206, 134, 8; }
.atmo-ui.pg-clouds { --acc: var(--dusk); }
.atmo-ui.pg-presets { --acc: var(--glow); }
.atmo-ui.pg-keys { --acc: var(--aurora); }

/* the variants: night (the default) and day */
.atmo-ui {
  --ground: #0c0f15; --card: #161a23; --fill: #212736; --field: #1b2030;
  --text: #f4f6fa; --text-2: #bac2cf; --muted: #7d8696; --icon-dim: #5a6375;
  --b1: rgba(255, 255, 255, 0.05); --b2: rgba(255, 255, 255, 0.08); --b3: rgba(255, 255, 255, 0.13);
  --b4: rgba(255, 255, 255, 0.22); --tick: rgba(255, 255, 255, 0.10);
  --card-shadow: 0 6u 18u rgba(0, 0, 0, 0.28); --menu-shadow: 0 10u 30u rgba(0, 0, 0, 0.5);
  --switch-off: #2c3243; --knob-off: #c7cedb;
  --ink: 244, 246, 250; --on-ink: #0c0f15;
  --dawn: 255, 190, 46;
  --scrim: rgba(4, 6, 10, 0.66);
  /* the open tab: a white pill */
  --tab-on: #f4f6fa; --tab-on-text: #0c0f15; --tab-shadow: none;
  /* the night sky: cerulean and violet overhead (behind the sim's status bar), an aurora's green low at the left,
     a last alpenglow low at the right, over the night's ink */
  --sky-glow: radial-gradient(ellipse 90% 34% at 0% 0%, rgba(47, 128, 247, 0.5) 0%, rgba(47, 128, 247, 0) 100%),
    radial-gradient(ellipse 80% 32% at 100% 0%, rgba(124, 92, 255, 0.48) 0%, rgba(124, 92, 255, 0) 100%),
    radial-gradient(ellipse 95% 30% at 15% 100%, rgba(18, 194, 140, 0.16) 0%, rgba(18, 194, 140, 0) 100%),
    radial-gradient(ellipse 70% 24% at 100% 100%, rgba(242, 78, 142, 0.1) 0%, rgba(242, 78, 142, 0) 100%),
    linear-gradient(180deg, #141c38 0u, #10152a 60u, #0c0f15 260u); }
.atmo-ui.light {
  --ground: #f1f2f5; --card: #ffffff; --fill: #eff1f4; --field: #f3f4f7;
  --text: #11141a; --text-2: #4b5363; --muted: #868e9c; --icon-dim: #b0b6c1;
  --b1: rgba(17, 20, 26, 0.05); --b2: rgba(17, 20, 26, 0.08); --b3: rgba(17, 20, 26, 0.12);
  --b4: rgba(17, 20, 26, 0.2); --tick: rgba(17, 20, 26, 0.08);
  --card-shadow: 0 1u 2u rgba(17, 20, 26, 0.04), 0 8u 22u rgba(17, 20, 26, 0.06);
  --menu-shadow: 0 12u 32u rgba(17, 20, 26, 0.16);
  --switch-off: #dce0e7; --knob-off: #ffffff;
  --ink: 17, 20, 26; --on-ink: #ffffff;
  /* (the golden hour's yellow, deepened to be read on white) */
  --dawn: 206, 134, 8;
  --scrim: rgba(17, 20, 26, 0.32);
  --tab-on: #ffffff; --tab-on-text: #11141a; --tab-shadow: 0 3u 10u rgba(17, 40, 90, 0.16);
  /* the day sky: cerulean and violet overhead, deep enough for the sim's white clock in the status bar, the
     alpenglow's pink and the golden hour's warmth below them, then the paper; a little of both again at the
     bottom */
  --sky-glow: radial-gradient(ellipse 90% 30% at 0% 0%, rgba(47, 128, 247, 0.9) 0%, rgba(47, 128, 247, 0) 100%),
    radial-gradient(ellipse 80% 28% at 100% 0%, rgba(124, 92, 255, 0.75) 0%, rgba(124, 92, 255, 0) 100%),
    radial-gradient(ellipse 70% 26% at 85% 24%, rgba(242, 78, 142, 0.18) 0%, rgba(242, 78, 142, 0) 100%),
    radial-gradient(ellipse 65% 24% at 10% 30%, rgba(255, 190, 46, 0.18) 0%, rgba(255, 190, 46, 0) 100%),
    radial-gradient(ellipse 90% 26% at 0% 100%, rgba(47, 128, 247, 0.12) 0%, rgba(47, 128, 247, 0) 100%),
    radial-gradient(ellipse 70% 22% at 100% 100%, rgba(255, 190, 46, 0.14) 0%, rgba(255, 190, 46, 0) 100%),
    linear-gradient(180deg, #5a8ff0 0u, #9ab9f2 45u, #dbe6f7 130u, #eef1f6 240u); }

.atmo-ui { position: absolute; left: 0; top: 0; right: 0; bottom: 0; overflow: hidden; color: var(--text);
  --f-light: Roboto-Light, Arial, sans-serif; --f-reg: Roboto-Regular, Arial, sans-serif;
  --f-med: Roboto-Medium, Arial, sans-serif; --f-bold: Roboto-Bold, Arial, sans-serif;
  font-family: var(--f-reg); font-weight: normal; font-size: 15u; line-height: 1.3;
  background: var(--ground); background-image: var(--sky-glow); }
.atmo-ui * { box-sizing: border-box; }
.atmo-ui svg { display: block; }
.atmo-ui ::-webkit-scrollbar { width: 4u; height: 4u; }
.atmo-ui ::-webkit-scrollbar-track { background: transparent; }
.atmo-ui ::-webkit-scrollbar-thumb { background: var(--b4); border-radius: 2u; }

/* the frame: the sim's status bar over the top strip; the bottom strip, where the dock button sits, stays empty as
   in the sim's own apps */
.atmo-ui .nav, .atmo-ui .main { position: absolute; }
.atmo-ui.v .nav { top: 33u; left: 0; right: 0; height: 96u; }
/* (the bottom strip: 47u, the dock button's 46u rounded up to whole pixels at every size: 55 px for 54.9) */
.atmo-ui.v .main { top: 129u; left: 0; right: 0; bottom: 47u; }
.atmo-ui.h .nav { top: 33u; left: 0; bottom: 47u; width: 172u; }
.atmo-ui.h .main { top: 33u; left: 172u; right: 0; bottom: 47u; }

/* the brand and the mod's switch */
.atmo-ui .brand { display: flex; align-items: center; height: 44u; padding: 0 16u; }
.atmo-ui .brand .mark { width: 20u; height: 20u; margin-right: 10u; flex: 0 0 auto; border-radius: 10u;
  background: rgb(var(--sky));
  background-image: linear-gradient(135deg, rgb(var(--sun)) 0%, rgb(var(--glow)) 45%, rgb(var(--dusk)) 75%, rgb(var(--sky)) 100%); }
.atmo-ui .brand .name { flex: 1 1 auto; font-family: var(--f-med); font-size: 16u; min-width: 0; }
.atmo-ui .modrow { display: flex; align-items: center; }
.atmo-ui .modrow .modlabel { flex: 1 1 auto; color: var(--muted); font-size: 13u; }
.atmo-ui .modrow .state { font-size: 13u; margin-right: 8u; color: var(--text-2); }
.atmo-ui.v .modrow { position: absolute; top: 0; right: 16u; height: 44u; }
.atmo-ui.v .modrow .modlabel { display: none; }
.atmo-ui.h .modrow { height: 40u; margin: 0 10u 8u; padding: 0 8u 0 14u; border-radius: 20u; background: var(--card);
  box-shadow: var(--card-shadow); }

/* navigation: a bar of tabs (vertical), a list (horizontal); the open one an ink pill */
.atmo-ui .tabs { display: flex; }
.atmo-ui.v .tabs { padding: 0 10u; height: 52u; }
.atmo-ui.h .tabs { flex-direction: column; padding: 2u 10u; }
.atmo-ui .tab { position: relative; display: flex; align-items: center; cursor: pointer; color: var(--text-2); }
.atmo-ui.v .tab { flex: 1 1 0; flex-direction: column; justify-content: center; font-size: 11u; margin: 2u 3u;
  border-radius: 16u; }
.atmo-ui.v .tab svg { width: 19u; height: 19u; margin-bottom: 3u; }
.atmo-ui.h .tab { height: 40u; padding: 0 14u; margin-bottom: 3u; font-size: 14u; border-radius: 20u; }
.atmo-ui.h .tab svg { width: 18u; height: 18u; margin-right: 11u; flex: 0 0 auto; }
.atmo-ui .tab:hover { color: var(--text); }
.atmo-ui .tab.on { color: var(--tab-on-text); background: var(--tab-on); box-shadow: var(--tab-shadow); }
.atmo-ui .tab.on svg { color: rgb(var(--acc)); }

/* a page: its head (the title at the left, the preset at the right), then what scrolls */
.atmo-ui .main { display: flex; flex-direction: column; }
.atmo-ui .head { flex: 0 0 auto; display: flex; align-items: center; padding: 8u 16u 10u; }
.atmo-ui .head .titles { flex: 1 1 auto; min-width: 0; padding-right: 12u; }
.atmo-ui .head h1 { margin: 0; font-family: var(--f-med); font-weight: normal; font-size: 24u; letter-spacing: -0.01em; }
.atmo-ui .head p { margin: 2u 0 0; color: var(--text-2); font-size: 13u; }
.atmo-ui .scroll { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 2u 16u 14u; }

/* the preset, beside every page's title: its name and state, Undo and Save while it has unsaved changes */
.atmo-ui .preset { flex: 0 0 auto; display: flex; align-items: center; height: 44u; padding: 0 5u 0 16u;
  border-radius: 22u; border: 1px solid transparent; background: var(--card); box-shadow: var(--card-shadow); }
.atmo-ui .preset .pinfo { min-width: 0; margin-right: 6u; }
.atmo-ui .preset .plabel { font-size: 10u; letter-spacing: 0.08em; color: var(--muted); }
.atmo-ui .preset .pname { font-family: var(--f-med); font-size: 13u; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; max-width: 170u; }
.atmo-ui .preset .pstate { font-size: 12u; color: var(--muted); margin: 0 6u; white-space: nowrap; }
.atmo-ui .preset.unsaved { border-color: rgba(var(--dawn), 0.7); }
.atmo-ui .preset.unsaved .pstate { color: rgb(var(--dawn)); }
.atmo-ui .preset .btn { height: 32u; padding: 0 13u; font-size: 13u; margin-left: 4u; border-radius: 16u; }
.atmo-ui .preset.saved .btn { display: none; }
.atmo-ui .preset.saved { padding-right: 16u; }

/* cards */
.atmo-ui .card { margin-bottom: 12u; border-radius: 20u; border: 1px solid var(--b1); background: var(--card);
  box-shadow: var(--card-shadow); }
.atmo-ui .card > h2 { margin: 0; padding: 14u 18u 2u; font-family: var(--f-med); font-weight: normal; font-size: 15u;
  color: var(--text); }
.atmo-ui .card .body { padding: 4u 18u 12u; }
/* a section's head: a round icon in the page's colour, the title */
.atmo-ui .card > .sec-head { display: flex; align-items: center; padding: 14u 18u 4u; }
.atmo-ui .sec-head h2, .atmo-ui .sec-title h2 { margin: 0; font-family: var(--f-med); font-weight: normal; font-size: 15u;
  color: var(--text); }
.atmo-ui .chipicon { display: flex; align-items: center; justify-content: center; flex: 0 0 auto; width: 32u; height: 32u;
  margin-right: 11u; border-radius: 16u; color: rgb(var(--acc-mark)); background: rgba(var(--acc), 0.16); }
.atmo-ui .chipicon svg { width: 17u; height: 17u; }
.atmo-ui .cols { display: flex; flex-wrap: wrap; margin: 0 -6u; }
.atmo-ui .cols > .card { margin-left: 6u; margin-right: 6u; flex: 1 1 240u; min-width: 0; }
.atmo-ui .muted { color: var(--muted); }
.atmo-ui .mono { font-size: 13u; letter-spacing: 0.02em; word-wrap: break-word; color: var(--text-2); }

/* Home's tiles: a colour each, with a glow from the top right; white text (dark on the golden one), large light
   numbers; pills and buttons on them in white */
.atmo-ui .tiles { display: flex; flex-wrap: wrap; margin: 0 -6u; }
.atmo-ui .tile { position: relative; overflow: hidden; flex: 1 1 150u; min-width: 0; margin: 0 6u 12u; padding: 14u 16u 12u;
  border-radius: 20u; color: #ffffff; background: rgb(var(--c)); --c: var(--sky);
  background-image: radial-gradient(circle at 88% 0%, rgba(255, 255, 255, 0.42) 0%, rgba(255, 255, 255, 0) 58%),
    linear-gradient(160deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0) 45%, rgba(0, 0, 0, 0.14) 100%); }
.atmo-ui .tile.wide { flex: 1 1 100%; }
/* (two in a row at every size: Tools' tiles) */
.atmo-ui .tile.half { flex: 1 1 260u; }
.atmo-ui .tile.sky { --c: var(--sky); }
.atmo-ui .tile.sun { --c: var(--sun); color: #2a1d00; }
.atmo-ui .tile.dusk { --c: var(--dusk); }
.atmo-ui .tile.glow { --c: var(--glow); }
.atmo-ui .tile.aurora { --c: var(--aurora); }
/* (a failure: storm red; an icon after a button's text) */
.atmo-ui .tile.storm { --c: var(--storm); }
.atmo-ui .btn.trail svg { margin: 0 0 0 7u; }
.atmo-ui .tile .tlabel { display: flex; align-items: center; font-family: var(--f-med); font-size: 12u; opacity: 0.86; }
.atmo-ui .tile .tlabel svg { width: 15u; height: 15u; margin-right: 7u; }
.atmo-ui .tile .tlabel .right { margin-left: auto; font-family: var(--f-reg); }
.atmo-ui .tile .big { margin-top: 10u; font-family: var(--f-light); font-size: 32u; line-height: 1.05;
  letter-spacing: -0.01em; }
.atmo-ui .tile .bigsub { margin-top: 3u; font-size: 13u; opacity: 0.86; }
.atmo-ui .tile .stats { display: flex; flex-wrap: wrap; margin: 12u -8u 0; }
.atmo-ui .tile .stat { flex: 1 1 110u; min-width: 0; margin: 0 8u 4u; }
.atmo-ui .tile .stat .k { font-size: 11u; opacity: 0.75; }
.atmo-ui .tile .stat .v { font-family: var(--f-med); font-size: 14u; }
/* (the weather: the clouds and how they're drawn; the flight category's scale, LIFR to VFR, its ticks lit in the
   METAR's; the METAR as it came) */
.atmo-ui .tile .tline { display: flex; margin-top: 12u; font-size: 13u; line-height: 1.35; }
.atmo-ui .tile .tline .k { flex: 0 0 64u; font-family: var(--f-med); }
.atmo-ui .tile .tline .v { flex: 1 1 auto; min-width: 0; }
.atmo-ui .tile .tline .lay { font-family: var(--f-med); }
.atmo-ui .tile .tline .how { margin-top: 2u; font-size: 12u; opacity: 0.85; }
.atmo-ui .tile .fc { margin-top: 14u; }
.atmo-ui .tile .fc-title { display: flex; font-family: var(--f-med); font-size: 11u; opacity: 0.85; margin-bottom: 6u; }
.atmo-ui .tile .fc-title .right { margin-left: auto; padding-left: 8u; }
.atmo-ui .tile .segs { display: flex; margin: 0 -3u; }
.atmo-ui .tile .seg { flex: 1 1 0; min-width: 0; margin: 0 3u; --k: var(--aurora); }
.atmo-ui .tile .seg.lifr { --k: var(--glow); }
.atmo-ui .tile .seg.ifr { --k: var(--storm); }
.atmo-ui .tile .seg.mvfr { --k: var(--sky); }
.atmo-ui .tile .segticks { height: 18u; opacity: 0.32;
  background-image: linear-gradient(90deg, #ffffff 0u, #ffffff 2u, rgba(255, 255, 255, 0) 2u);
  background-size: 7u 100%; background-repeat: repeat-x; }
.atmo-ui .tile .seg.on .segticks { opacity: 1; }
.atmo-ui .tile .seglabel { margin-top: 5u; text-align: center; font-family: var(--f-med); font-size: 11u; opacity: 0.75; }
.atmo-ui .tile .seglabel span { display: inline-block; padding: 1u 8u; border-radius: 9u; }
.atmo-ui .tile .seg.on .seglabel { opacity: 1; }
.atmo-ui .tile .seg.on .seglabel span { color: rgb(var(--k)); background: #ffffff; }
.atmo-ui .tile .traw { margin-top: 12u; font-size: 11u; line-height: 1.35; opacity: 0.72; word-wrap: break-word; }
.atmo-ui .tile .tlink { margin-top: 8u; font-family: var(--f-med); font-size: 13u; text-decoration: underline;
  cursor: pointer; display: inline-flex; align-items: center; }
.atmo-ui .tile .tlink svg { width: 13u; height: 13u; margin-left: 5u; }
.atmo-ui .tile .live { display: inline-block; width: 8u; height: 8u; border-radius: 4u; margin-right: 8u;
  background: #ffffff; -webkit-animation: atmo-pulse 1.6s ease-in-out infinite; animation: atmo-pulse 1.6s ease-in-out infinite; }
/* (a page's main switch on its tile: on or off in large, the switch beside it, its description on request) */
.atmo-ui .tile .tlabel .tinfo { display: flex; margin-left: 6u; cursor: pointer; opacity: 0.85; }
.atmo-ui .tile .tlabel .tinfo svg { margin-right: 0; }
.atmo-ui .tile .tmain { display: flex; align-items: center; justify-content: space-between; }
.atmo-ui .tile .tmain .switch { margin-top: 10u; }
.atmo-ui .tile .ttip p, .atmo-ui .tile .tp { margin: 8u 0 0; font-size: 13u; line-height: 1.4; opacity: 0.92; }
/* (switches, fields and notes on a tile) */
.atmo-ui .tile .switch { background: rgba(255, 255, 255, 0.32); }
.atmo-ui .tile .switch::after { background: #ffffff; }
.atmo-ui .tile .switch.on { background: #ffffff; }
.atmo-ui .tile .switch.on::after { background: rgb(var(--c)); }
.atmo-ui .tile.sun .switch { background: rgba(42, 29, 0, 0.2); }
.atmo-ui .tile.sun .switch.on { background: #11141a; }
.atmo-ui .tile .field { color: inherit; background: rgba(255, 255, 255, 0.24); border-color: transparent; }
.atmo-ui .tile .field:focus { border-color: #ffffff; }
.atmo-ui .tile .field::-webkit-input-placeholder { color: rgba(255, 255, 255, 0.78); }
.atmo-ui .tile .muted, .atmo-ui .tile .note { color: inherit; opacity: 0.88; }
/* (pills and buttons on a tile: white, the chosen one solid; on the golden tile, ink) */
.atmo-ui .tile .chips { margin-top: 8u; }
.atmo-ui .tile .chip { color: inherit; background: rgba(255, 255, 255, 0.2); }
.atmo-ui .tile .chip:hover { border-color: rgba(255, 255, 255, 0.6); }
.atmo-ui .tile .chip.on { color: #11141a; background: #ffffff; }
.atmo-ui .tile .chip.armed { color: inherit; border-color: #ffffff; background: rgba(0, 0, 0, 0.22); }
.atmo-ui .tile .chip.ghost { display: inline-flex; align-items: center; background: transparent;
  border-color: rgba(255, 255, 255, 0.55); }
.atmo-ui .tile .chip.ghost svg { width: 14u; height: 14u; margin-right: 6u; }
.atmo-ui .tile .about { color: inherit; opacity: 0.92; margin: 8u 0 0; }
.atmo-ui .tile .actions { margin-top: 12u; }
.atmo-ui .tile .btn { color: inherit; background: rgba(255, 255, 255, 0.22); }
.atmo-ui .tile .btn:hover { border-color: rgba(255, 255, 255, 0.6); }
.atmo-ui .tile .btn.primary { color: #11141a; background: #ffffff; }
.atmo-ui .tile.sun .btn { background: rgba(42, 29, 0, 0.1); }
.atmo-ui .tile.sun .btn:hover { border-color: rgba(42, 29, 0, 0.4); }
.atmo-ui .tile.sun .btn.primary { color: #ffffff; background: #11141a; }
.atmo-ui .tile .btn.chosen { color: #11141a; background: #ffffff; }
.atmo-ui .tile.sun .btn.chosen { color: #ffffff; background: #11141a; }
.atmo-ui .tile .btn.armed { color: inherit; border-color: #ffffff; background: rgba(0, 0, 0, 0.22); }
.atmo-ui .tile.sun .chip.ghost { border-color: rgba(42, 29, 0, 0.45); }
@-webkit-keyframes atmo-pulse { 0% { opacity: 1; } 50% { opacity: 0.25; } 100% { opacity: 1; } }
@keyframes atmo-pulse { 0% { opacity: 1; } 50% { opacity: 0.25; } 100% { opacity: 1; } }

/* a setting's row */
.atmo-ui .row { display: flex; align-items: center; min-height: 46u; padding: 6u 0; border-top: 1px solid var(--b1); }
.atmo-ui .row:first-child { border-top: none; }
.atmo-ui .row .text { flex: 1 1 auto; min-width: 0; padding-right: 10u; }
.atmo-ui .row .label { display: flex; align-items: center; }
.atmo-ui .row .tip { font-size: 13u; color: var(--muted); margin-top: 4u; display: none; }
.atmo-ui .row.open .tip { display: block; }
.atmo-ui .row .tip p { margin: 0 0 5u; line-height: 1.4; }
.atmo-ui .row.off { opacity: 0.42; }
/* why a greyed setting has no effect now (controls.ts blocked) */
.atmo-ui .row .why { font-size: 12u; color: var(--text-2); margin-top: 2u; }
.atmo-ui .row .why:empty { display: none; }
.atmo-ui .icon-btn { width: 26u; height: 26u; display: flex; align-items: center; justify-content: center;
  color: var(--icon-dim); cursor: pointer; flex: 0 0 auto; margin-left: 2u; border-radius: 13u; }
.atmo-ui .icon-btn svg { width: 15u; height: 15u; }
.atmo-ui .icon-btn:hover { color: var(--text-2); background: var(--fill); }
.atmo-ui .icon-btn.faded { opacity: 0; pointer-events: none; }
.atmo-ui .icon-btn.reset { color: rgb(var(--dawn)); }
.atmo-ui .spacer { flex: 0 0 auto; width: 8u; }
.atmo-ui .more { display: flex; align-items: center; cursor: pointer; min-height: 38u; color: var(--muted); font-size: 13u;
  border-top: 1px solid var(--b1); }
.atmo-ui .more svg { width: 15u; height: 15u; margin-left: 4u; }
.atmo-ui .more:hover { color: var(--text); }

/* controls */
.atmo-ui .switch { position: relative; width: 44u; height: 26u; border-radius: 13u; flex: 0 0 auto; cursor: pointer;
  background: var(--switch-off); }
.atmo-ui .switch::after { content: ""; position: absolute; left: 3u; top: 3u; width: 20u; height: 20u; border-radius: 10u;
  background: var(--knob-off); box-shadow: 0 1u 3u rgba(0, 0, 0, 0.2); }
.atmo-ui .switch.on { background: rgb(var(--acc)); }
.atmo-ui .switch.on::after { left: 21u; background: #ffffff; }
/* (the mod's own switch: always the sky's cerulean, whatever the page's colour) */
.atmo-ui .modrow .switch.on { background: rgb(var(--sky)); }
/* the value bar: minus | the bar, a pill filled to the value, the value written inside it, ticks behind | plus */
.atmo-ui .stepper { display: flex; align-items: center; justify-content: space-between; flex: 1 1 auto; min-width: 0;
  height: 32u; }
.atmo-ui .sbtn { width: 32u; height: 32u; display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
  border-radius: 16u; cursor: pointer; color: var(--text-2); background: var(--fill); }
.atmo-ui .sbtn svg { width: 14u; height: 14u; }
.atmo-ui .sbtn:hover { color: var(--text); background: rgba(var(--acc), 0.18); }
/* (the bar as wide as its control leaves it: 270u less the reset, the two buttons and their margins; set here, so the
   copy of the text on the fill can be as wide without measuring: a folded section measured 0, and the engine rounds
   the fill to whole pixels, which a copy sized from the fill would multiply) */
.atmo-ui { --barw: 168u; }
.atmo-ui.h { --barw: 208u; }
.atmo-ui .bar { position: relative; flex: 0 0 auto; width: var(--barw); height: 32u; cursor: pointer; overflow: hidden;
  border-radius: 16u; background: var(--fill);
  background-image: linear-gradient(90deg, var(--tick) 0u, var(--tick) 1u, rgba(0, 0, 0, 0) 1u);
  background-size: 8u 100%; background-repeat: repeat-x; }
/* (its right end straight: the value's text changes colour exactly there) */
.atmo-ui .bfill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 16u 0 0 16u; background: rgb(var(--acc));
  background-image: linear-gradient(90deg, rgba(255, 255, 255, 0) 0%, rgba(255, 255, 255, 0.22) 100%); }
.atmo-ui .btext { position: absolute; left: 0; top: 0; bottom: 0; width: 100%; display: flex; align-items: center;
  justify-content: center; font-family: var(--f-med); font-size: 13u; color: var(--text); white-space: nowrap;
  pointer-events: none; }
.atmo-ui .btext.on { color: var(--on-acc); left: 0; width: var(--barw); }  /* (centred where the other is) */
.atmo-ui .bclip { position: absolute; left: 0; top: 0; bottom: 0; overflow: hidden; pointer-events: none; }
.atmo-ui .control { display: flex; align-items: center; flex: 0 0 auto; width: 270u; }
.atmo-ui .control.switchctl { justify-content: flex-end; }
.atmo-ui .control .icon-btn.reset { margin-left: 6u; }
/* an experimental setting's mark */
.atmo-ui .named { display: inline-flex; align-items: center; }
.atmo-ui .exp { display: inline-flex; margin-left: 6u; color: rgb(var(--exp)); }
.atmo-ui .exp svg { width: 14u; height: 14u; }
.atmo-ui.h .control { width: 310u; }
/* buttons: pills; the main one ink, the others a soft fill */
.atmo-ui .btn { display: inline-flex; align-items: center; justify-content: center; height: 34u; padding: 0 16u;
  border-radius: 17u; cursor: pointer; font-family: var(--f-med); font-size: 13u; color: var(--text);
  border: 1px solid transparent; background: var(--fill); }
.atmo-ui .btn:hover { border-color: var(--b4); }
.atmo-ui .btn.primary { color: var(--on-ink); background: rgb(var(--ink)); }
.atmo-ui .btn.primary:hover { border-color: rgb(var(--acc)); }
.atmo-ui .btn.armed { color: rgb(var(--dawn)); border-color: rgba(var(--dawn), 0.7); background: rgba(var(--dawn), 0.1); }
.atmo-ui .btn.danger { color: rgb(var(--rose)); background: rgba(var(--rose), 0.1); }
.atmo-ui .btn.inert { opacity: 0.35; cursor: default; }
.atmo-ui .btn.chosen { color: var(--on-ink); background: rgb(var(--ink)); }
.atmo-ui .btn + .btn { margin-left: 6u; }
.atmo-ui .tag { display: inline-block; font-family: var(--f-med); font-size: 10u; letter-spacing: 0.05em;
  color: var(--text-2); padding: 2u 8u; border-radius: 9u; background: var(--fill); white-space: nowrap; }
.atmo-ui .tag.unsaved { color: rgb(var(--dawn)); background: rgba(var(--dawn), 0.14); }
.atmo-ui .dot { display: inline-block; width: 8u; height: 8u; border-radius: 4u; margin-right: 7u; flex: 0 0 auto; }
.atmo-ui .dot.ok { background: rgb(var(--ok)); }
.atmo-ui .dot.wait { background: rgb(var(--dawn)); }
.atmo-ui .field { width: 100%; height: 36u; padding: 0 16u; border-radius: 18u; font-family: var(--f-reg); font-size: 14u;
  color: var(--text); background: var(--field); border: 1px solid var(--b2); outline: none; }
.atmo-ui .field:focus { border-color: rgb(var(--acc)); }

/* the overview's preset chips: pills, the active one ink */
.atmo-ui .chips { display: flex; flex-wrap: wrap; margin: 4u -3u 0; }
.atmo-ui .chip { margin: 3u; padding: 7u 14u; border-radius: 17u; cursor: pointer; font-size: 13u;
  border: 1px solid transparent; color: var(--text-2); background: var(--fill); }
.atmo-ui .chip:hover { border-color: var(--b4); }
.atmo-ui .chip.on { color: var(--on-ink); background: rgb(var(--ink)); }
.atmo-ui .chip.armed { color: rgb(var(--dawn)); border-color: rgba(var(--dawn), 0.7); background: rgba(var(--dawn), 0.1); }


/* forms, notes and a page's actions */
.atmo-ui .fieldrow { display: flex; align-items: center; margin: 6u 0; }
.atmo-ui .fieldrow .field { flex: 1 1 auto; min-width: 0; }
.atmo-ui .fieldrow .btn { margin-left: 10u; flex: 0 0 auto; }
.atmo-ui .actions { display: flex; align-items: center; flex-wrap: wrap; margin-top: 12u; }
.atmo-ui .actions .muted { margin-right: 10u; font-size: 13u; }
.atmo-ui .actions .btn + .muted { margin: 0 0 0 10u; }
.atmo-ui .actions.end { justify-content: flex-end; }
.atmo-ui .btns { display: flex; flex: 0 0 auto; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.atmo-ui .btn svg { width: 15u; height: 15u; margin-right: 7u; flex: 0 0 auto; }
.atmo-ui .about { margin: 0 0 8u; font-size: 14u; line-height: 1.45; color: var(--text-2); }
.atmo-ui .btn.small { height: 28u; padding: 0 12u; font-size: 12u; border-radius: 14u; }
.atmo-ui .note { margin-top: 8u; font-size: 13u; color: var(--text-2); }
.atmo-ui .note.warn { color: rgb(var(--dawn)); }
.atmo-ui .lines { white-space: pre-wrap; }
.atmo-ui .intro { font-size: 13u; margin-bottom: 8u; }
.atmo-ui .notice { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12u; padding: 10u 16u;
  border-radius: 18u; background: rgba(var(--dawn), 0.14); color: var(--text); font-size: 13u; }
.atmo-ui .notice .btn { margin-left: 10u; flex: 0 0 auto; }
.atmo-ui .choice { display: flex; flex-wrap: wrap; margin: 4u -3u 4u; }
/* presets: a preset's row, the question when it lacks settings */
.atmo-ui .preset-row .pn { margin-right: 10u; }
.atmo-ui .tip-line { font-size: 12u; color: var(--muted); margin-top: 2u; }
.atmo-ui .ask { margin: 2u 0 10u; padding: 12u 14u; border-radius: 16u; background: rgba(var(--dawn), 0.12);
  font-size: 13u; line-height: 1.4; color: var(--text-2); }
.atmo-ui .ask .btns { justify-content: flex-start; margin-top: 10u; }
/* Community Presets: an entry, its author line, its description */
.atmo-ui .shared { padding: 12u 0; border-top: 1px solid var(--b1); }
.atmo-ui .shared:first-child { border-top: none; }
.atmo-ui .shared .label { display: flex; align-items: baseline; flex-wrap: wrap; }
.atmo-ui .shared .pn { font-family: var(--f-med); font-size: 15u; color: var(--text); margin-right: 10u; }
.atmo-ui .shared .by { font-size: 12u; color: var(--muted); }
.atmo-ui .shared-desc { font-size: 13u; color: var(--text-2); margin-top: 4u; line-height: 1.4; }
.atmo-ui .shared .more { min-height: 30u; border-top: none; }
.atmo-ui .shared .btns { justify-content: flex-start; margin-top: 4u; }
.atmo-ui .shared .meta { margin-top: 3u; font-size: 12u; color: var(--muted); }
.atmo-ui .shared .differs { margin-top: 4u; font-size: 12u; line-height: 1.35; color: rgb(var(--dawn)); }
/* its heading's icons (search, sort, versions), their menus, the search field, what's shown; the pages */
.atmo-ui .card-head { display: flex; align-items: center; justify-content: space-between; padding: 6u 10u 0 0; }
.atmo-ui .card-head .sec-title { display: flex; align-items: center; padding: 8u 0 0 18u; }
.atmo-ui .tools { display: flex; align-items: center; flex: 0 0 auto; }
.atmo-ui .drop { position: relative; }
.atmo-ui .tool { display: flex; align-items: center; justify-content: center; width: 34u; height: 34u; margin-left: 4u;
  border-radius: 17u; cursor: pointer; color: var(--text-2); background: var(--fill); }
.atmo-ui .tool svg { width: 17u; height: 17u; }
.atmo-ui .tool:hover { color: var(--text); }
.atmo-ui .tool.set { color: var(--on-acc); background: rgb(var(--acc)); }
.atmo-ui .tool.open { color: var(--on-ink); background: rgb(var(--ink)); }
.atmo-ui .menu { position: absolute; top: 40u; right: 0; z-index: 10; width: 264u; padding: 6u; border-radius: 18u;
  border: 1px solid var(--b2); background: var(--card); box-shadow: var(--menu-shadow); }
.atmo-ui .menu .item { display: flex; align-items: flex-start; padding: 8u 12u 8u 8u; border-radius: 12u; cursor: pointer;
  font-size: 13u; color: var(--text-2); }
.atmo-ui .menu .item:hover { background: var(--fill); }
.atmo-ui .menu .item.on { color: var(--text); }
.atmo-ui .menu .tick { flex: 0 0 auto; width: 16u; height: 16u; margin-right: 8u; color: rgb(var(--acc)); }
.atmo-ui .menu .tick svg { width: 16u; height: 16u; }
.atmo-ui .menu .text { flex: 1 1 auto; min-width: 0; }
.atmo-ui .menu .sub { margin-top: 2u; font-size: 11u; line-height: 1.3; color: var(--muted); }
.atmo-ui .menu .sub.warn { color: rgb(var(--dawn)); }
.atmo-ui .searchrow { display: flex; align-items: center; margin: 4u 0 8u; }
.atmo-ui .searchrow .field { flex: 1 1 auto; min-width: 0; }
.atmo-ui .searchrow .tool { flex: 0 0 auto; margin-left: 6u; }
.atmo-ui .status { margin: 0 0 4u; font-size: 12u; }
.atmo-ui .pager { display: flex; align-items: center; justify-content: space-between; margin-top: 4u; padding-top: 10u;
  border-top: 1px solid var(--b1); }
.atmo-ui .pager .muted { flex: 1 1 auto; text-align: center; font-size: 12u; }
.atmo-ui .pager .btn { flex: 0 0 auto; }
/* hotkeys: the keys over the buttons, at the right */
.atmo-ui .hotkey .hk { flex: 0 0 auto; display: flex; flex-direction: column; align-items: flex-end; }
.atmo-ui .hotkey .keys { font-family: var(--f-med); font-size: 13u; margin-bottom: 6u; padding: 4u 11u;
  border-radius: 9u; color: var(--text); background: var(--fill); }
.atmo-ui .hotkey .keys.none { font-family: var(--f-reg); color: var(--muted); }
.atmo-ui .hotkey .keys.learning { color: rgb(var(--dawn)); }

/* a sheet over the app (What's new, the performance report): between the status bar and the dock button */
.atmo-ui .sheet { position: absolute; top: 33u; bottom: 47u; left: 0; right: 0; z-index: 20; background: var(--scrim); }
.atmo-ui .sheet-card { position: absolute; top: 12u; bottom: 12u; left: 12u; right: 12u; max-width: 640u; margin: 0 auto;
  display: flex; flex-direction: column; border-radius: 24u; border: 1px solid var(--b2); background: var(--card);
  box-shadow: var(--menu-shadow); overflow: hidden; }
.atmo-ui .sheet-head { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between;
  padding: 14u 12u 10u 20u; }
.atmo-ui .sheet-head h2 { margin: 0; font-family: var(--f-med); font-weight: normal; font-size: 18u; color: var(--text); }
.atmo-ui .sheet-head .icon-btn { width: 32u; height: 32u; border-radius: 16u; background: var(--fill); color: var(--text-2); }
.atmo-ui .sheet-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 4u 20u 20u; }
.atmo-ui .news-item { margin-bottom: 14u; }
.atmo-ui .news-about { margin-top: 16u; }
.atmo-ui .news-head { font-family: var(--f-med); font-size: 15u; color: var(--text); margin-bottom: 4u; }
.atmo-ui .news-text { font-size: 14u; line-height: 1.45; color: var(--text-2); margin-bottom: 8u; }
.atmo-ui .news-text.small { font-size: 12u; color: var(--muted); }
.atmo-ui .news-version { margin: 12u 0; }
.atmo-ui .news-release { font-family: var(--f-med); font-size: 14u; color: var(--text); margin-bottom: 4u; }
.atmo-ui .news-line { font-size: 13u; line-height: 1.4; color: var(--text-2); margin-bottom: 4u; }
.atmo-ui .news-label { color: var(--text); }
.atmo-ui .q { font-size: 13u; color: var(--text-2); margin-top: 14u; }
.atmo-ui .steps { margin: 6u 0 0; padding-left: 20u; font-size: 14u; line-height: 1.45; color: var(--text-2); }
.atmo-ui .steps li { margin-bottom: 6u; }
.atmo-ui .details { margin: 4u 0 8u; padding: 10u 12u; border-radius: 12u; font-size: 11u; color: var(--text-2);
  white-space: pre-wrap; word-wrap: break-word; background: var(--fill); }

/* the performance report */
.atmo-ui .progress { height: 8u; border-radius: 4u; background: var(--fill); overflow: hidden; margin: 10u 0; }
.atmo-ui .progress > div { height: 100%; border-radius: 4u; background: rgb(var(--acc)); }
.atmo-ui table.kv { width: 100%; border-collapse: collapse; font-size: 13u; }
.atmo-ui table.kv td { padding: 6u 0; border-top: 1px solid var(--b1); }
.atmo-ui table.kv tr:first-child td { border-top: none; }
.atmo-ui table.kv td + td { text-align: right; color: var(--text-2); }
`);
