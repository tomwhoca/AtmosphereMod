// The redesign's look (2026-11-20, after the user's first look in the sim): minimal and compact, in pastel sky and
// cloud colours, in two variants to compare: dark (a dusk-blue ground) and light (a pale sky, deeper overhead, where
// the sim's status bar draws its own dark gradient and white clock). One accent, sky blue; a dawn peach for unsaved
// changes and for waiting; a rose for a delete about to happen; a sage green for what works (connected, the METARs
// coming in); a dusk lilac for the experimental mark. Thin borders and a restrained drop shadow, no glow; nearly
// square corners, rounded only where it marks the design (the switch, the slider's thumb, the status dot, the
// brand). Lengths are written as "16u": u is the tablet's scale against the small one at the first measured window
// (624 px short side), so one design holds at every size and window.
// The engine (CoherentGT 2.0) has no gap, sticky, clamp(), inset: tools/lint_efb.py checks. Its fonts: Roboto in
// four separate faces (Roboto-Light, the tablet's own, -Regular, -Medium, -Bold) and no weights or monospace
// (measured 2026-11-20): weight goes by family (--f-med), not font-weight. The tablet styles some class names
// globally (hidden, disabled, slider, button, title: measured): ours avoid them (tools/lint_efb.py).

import { units } from "./dom";

export const THEMES = ["dark", "light"];

export const UI_CSS = units(`
/* the two variants' colours (accent, dawn, rose, ok and exp as r, g, b for rgba()) */
.atmo-ui {
  --text: #eef2f7; --text-2: #c3ccd8; --muted: #8d99aa; --icon-dim: #66738a;
  --ground: linear-gradient(180deg, #1b2433 0%, #182030 50%, #151b26 100%);
  --b1: rgba(220, 230, 245, 0.07); --b2: rgba(220, 230, 245, 0.1); --b3: rgba(220, 230, 245, 0.15); --b4: rgba(220, 230, 245, 0.24);
  --card: #1f2938; --card-shadow: 0 2u 6u rgba(8, 12, 20, 0.3); --soft: rgba(255, 255, 255, 0.025);
  --track: #33405a; --bar: #182030; --switch-off: #2e3a4d; --knob-off: #8d99aa; --on-acc: #1b2433; --field: #182030;
  --acc: 167, 200, 234; --dawn: 240, 196, 160; --rose: 233, 161, 170; --ok: 150, 210, 168; --exp: 196, 176, 232; }
.atmo-ui.light {
  --text: #22324a; --text-2: #46566e; --muted: #73829a; --icon-dim: #98a6b8;
  --ground: linear-gradient(180deg, #86a3c4 0u, #b8cfe6 45u, #dce8f4 160u, #edf2f8 420u, #f3f6fa 100%);
  --b1: rgba(60, 85, 120, 0.08); --b2: rgba(60, 85, 120, 0.12); --b3: rgba(60, 85, 120, 0.17); --b4: rgba(60, 85, 120, 0.28);
  --card: #fbfcfe; --card-shadow: 0 2u 6u rgba(60, 85, 120, 0.12); --soft: rgba(255, 255, 255, 0.6);
  --track: #c9d6e4; --bar: #e9eff6; --switch-off: #cbd6e3; --knob-off: #ffffff; --on-acc: #ffffff; --field: #ffffff;
  --acc: 94, 144, 198; --dawn: 196, 128, 70; --rose: 192, 88, 104; --ok: 56, 140, 92; --exp: 124, 98, 178; }

.atmo-ui { position: absolute; left: 0; top: 0; right: 0; bottom: 0; overflow: hidden; color: var(--text);
  --f-light: Roboto-Light, Arial, sans-serif; --f-reg: Roboto-Regular, Arial, sans-serif;
  --f-med: Roboto-Medium, Arial, sans-serif; --f-bold: Roboto-Bold, Arial, sans-serif;
  font-family: var(--f-reg); font-weight: normal; font-size: 15u; line-height: 1.3; background: var(--ground); }
.atmo-ui * { box-sizing: border-box; }
.atmo-ui svg { display: block; }
.atmo-ui ::-webkit-scrollbar { width: 4u; height: 4u; }
.atmo-ui ::-webkit-scrollbar-track { background: transparent; }
.atmo-ui ::-webkit-scrollbar-thumb { background: var(--b4); }

/* the frame: the sim's status bar over the top strip; the bottom strip, where the dock button sits, stays empty as
   in the sim's own apps */
.atmo-ui .nav, .atmo-ui .main { position: absolute; }
.atmo-ui.v .nav { top: 33u; left: 0; right: 0; height: 92u; border-bottom: 1px solid var(--b2); }
.atmo-ui.v .main { top: 125u; left: 0; right: 0; bottom: 46u; }
.atmo-ui.h .nav { top: 33u; left: 0; bottom: 46u; width: 168u; border-right: 1px solid var(--b2); }
.atmo-ui.h .main { top: 33u; left: 168u; right: 0; bottom: 46u; }

/* the brand and the mod's switch */
.atmo-ui .brand { display: flex; align-items: center; height: 42u; padding: 0 16u; }
.atmo-ui .brand .mark { width: 16u; height: 16u; margin-right: 9u; flex: 0 0 auto; border-radius: 8u;
  background: linear-gradient(160deg, #eef2f7 0%, rgb(var(--acc)) 60%, rgb(var(--dawn)) 100%); }
.atmo-ui .brand .name { flex: 1 1 auto; font-family: var(--f-med); font-size: 15u; min-width: 0; }
.atmo-ui .modrow { display: flex; align-items: center; }
.atmo-ui .modrow .modlabel { flex: 1 1 auto; color: var(--muted); font-size: 13u; }
.atmo-ui .modrow .state { font-size: 13u; margin-right: 8u; color: var(--text-2); }
.atmo-ui.v .modrow { position: absolute; top: 0; right: 16u; height: 42u; }
.atmo-ui.v .modrow .modlabel { display: none; }
.atmo-ui.h .modrow { height: 36u; margin: 0 8u 6u; padding: 0 8u; border: 1px solid var(--b2); border-radius: 3u;
  background: var(--soft); }

/* navigation: a bar of tabs (vertical), a list (horizontal); the open one marked with a thin sky line */
.atmo-ui .tabs { display: flex; }
.atmo-ui.v .tabs { padding: 0 8u; height: 50u; }
.atmo-ui.h .tabs { flex-direction: column; padding: 2u 8u; }
.atmo-ui .tab { position: relative; display: flex; align-items: center; cursor: pointer; color: var(--muted); }
.atmo-ui.v .tab { flex: 1 1 0; flex-direction: column; justify-content: center; font-size: 11u; }
.atmo-ui.v .tab svg { width: 19u; height: 19u; margin-bottom: 3u; }
.atmo-ui.h .tab { height: 36u; padding: 0 10u; font-size: 14u; }
.atmo-ui.h .tab svg { width: 18u; height: 18u; margin-right: 10u; flex: 0 0 auto; }
.atmo-ui .tab:hover { color: var(--text-2); }
.atmo-ui .tab.on { color: var(--text); }
.atmo-ui .tab.on svg { color: rgb(var(--acc)); }
.atmo-ui .tab.on::after { content: ""; position: absolute; background: rgb(var(--acc)); }
.atmo-ui.v .tab.on::after { left: 22%; right: 22%; bottom: 0; height: 2u; }
.atmo-ui.h .tab.on { background: rgba(var(--acc), 0.1); }
.atmo-ui.h .tab.on::after { left: 0; top: 8u; bottom: 8u; width: 2u; }

/* a page: its head (the title at the left, the preset at the right), then what scrolls */
.atmo-ui .main { display: flex; flex-direction: column; }
.atmo-ui .head { flex: 0 0 auto; display: flex; align-items: center; padding: 10u 16u 8u; }
.atmo-ui .head .titles { flex: 1 1 auto; min-width: 0; padding-right: 12u; }
.atmo-ui .head h1 { margin: 0; font-family: var(--f-med); font-weight: normal; font-size: 20u; }
.atmo-ui .head p { margin: 2u 0 0; color: var(--muted); font-size: 13u; }
.atmo-ui .scroll { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 2u 16u 12u; }

/* the preset, beside every page's title: its name and state, Undo and Save while it has unsaved changes */
.atmo-ui .preset { flex: 0 0 auto; display: flex; align-items: center; height: 40u; padding: 0 4u 0 10u;
  border: 1px solid var(--b3); border-radius: 3u; background: var(--soft); }
.atmo-ui .preset .pinfo { min-width: 0; margin-right: 6u; }
.atmo-ui .preset .plabel { font-size: 10u; letter-spacing: 0.08em; color: var(--muted); }
.atmo-ui .preset .pname { font-family: var(--f-med); font-size: 13u; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; max-width: 170u; }
.atmo-ui .preset .pstate { font-size: 12u; color: var(--muted); margin: 0 6u; white-space: nowrap; }
.atmo-ui .preset.unsaved { border-color: rgba(var(--dawn), 0.55); }
.atmo-ui .preset.unsaved .pstate { color: rgb(var(--dawn)); }
.atmo-ui .preset .btn { height: 28u; padding: 0 10u; font-size: 13u; margin-left: 4u; }
.atmo-ui .preset.saved .btn { display: none; }
.atmo-ui .preset.saved { padding-right: 10u; }

/* cards */
.atmo-ui .card { margin-bottom: 10u; border: 1px solid var(--b2); border-radius: 3u; background: var(--card);
  box-shadow: var(--card-shadow); }
.atmo-ui .card > h2 { margin: 0; padding: 10u 14u 2u; font-family: var(--f-med); font-weight: normal; font-size: 11u;
  letter-spacing: 0.09em; text-transform: uppercase; color: var(--muted); }
.atmo-ui .card .body { padding: 2u 14u 8u; }
.atmo-ui .cols { display: flex; flex-wrap: wrap; margin: 0 -5u; }
.atmo-ui .cols > .card { margin-left: 5u; margin-right: 5u; flex: 1 1 240u; min-width: 0; }
.atmo-ui .muted { color: var(--muted); }
.atmo-ui .mono { font-size: 13u; letter-spacing: 0.02em; word-wrap: break-word; color: var(--text-2); }

/* a setting's row */
.atmo-ui .row { display: flex; align-items: center; min-height: 42u; padding: 5u 0; border-top: 1px solid var(--b1); }
.atmo-ui .row:first-child { border-top: none; }
.atmo-ui .row .text { flex: 1 1 auto; min-width: 0; padding-right: 10u; }
.atmo-ui .row .label { display: flex; align-items: center; }
.atmo-ui .row .tip { font-size: 13u; color: var(--muted); margin-top: 3u; display: none; }
.atmo-ui .row.open .tip { display: block; }
.atmo-ui .row.off { opacity: 0.4; }
.atmo-ui .icon-btn { width: 24u; height: 24u; display: flex; align-items: center; justify-content: center;
  color: var(--icon-dim); cursor: pointer; flex: 0 0 auto; margin-left: 2u; }
.atmo-ui .icon-btn svg { width: 15u; height: 15u; }
.atmo-ui .icon-btn:hover { color: var(--text-2); }
.atmo-ui .icon-btn.faded { opacity: 0; pointer-events: none; }
.atmo-ui .icon-btn.reset { color: rgb(var(--dawn)); }
.atmo-ui .spacer { flex: 0 0 auto; width: 8u; }
.atmo-ui .more { display: flex; align-items: center; cursor: pointer; min-height: 36u; color: var(--muted); font-size: 13u;
  border-top: 1px solid var(--b1); }
.atmo-ui .more svg { width: 15u; height: 15u; margin-left: 4u; }
.atmo-ui .more:hover { color: var(--text-2); }

/* controls */
.atmo-ui .switch { position: relative; width: 38u; height: 20u; border-radius: 10u; flex: 0 0 auto; cursor: pointer;
  background: var(--switch-off); }
.atmo-ui .switch::after { content: ""; position: absolute; left: 3u; top: 3u; width: 14u; height: 14u; border-radius: 7u;
  background: var(--knob-off); }
.atmo-ui .switch.on { background: rgb(var(--acc)); }
.atmo-ui .switch.on::after { left: 21u; background: var(--on-acc); }
/* the value bar: minus | the bar, filled to the value and with it written inside | plus */
.atmo-ui .stepper { display: flex; align-items: center; flex: 1 1 auto; min-width: 0; height: 32u; }
/* (minus and plus take what the engine's whole-pixel widths leave over: the bar's width is set) */
.atmo-ui .sbtn { width: 30u; height: 32u; display: flex; align-items: center; justify-content: center; flex: 1 0 auto;
  cursor: pointer; color: var(--text-2); border: 1px solid var(--b3); background: var(--soft); }
.atmo-ui .sbtn:first-child { border-radius: 3u 0 0 3u; border-right: none; }
.atmo-ui .sbtn:last-child { border-radius: 0 3u 3u 0; border-left: none; }
.atmo-ui .sbtn svg { width: 14u; height: 14u; }
.atmo-ui .sbtn:hover { color: var(--text); background: rgba(var(--acc), 0.12); }
/* (the bar as wide as its control leaves it: 270u less the reset and the two buttons; set here, so the copy of the
   text on the fill can be as wide without measuring: a folded section measured 0, and the engine rounds the fill to
   whole pixels, which a copy sized from the fill would multiply) */
.atmo-ui { --barw: 180u; }
.atmo-ui.h { --barw: 220u; }
.atmo-ui .bar { position: relative; flex: 0 0 auto; width: var(--barw); height: 32u; cursor: pointer; overflow: hidden;
  background: var(--bar); border: 1px solid var(--b3); }
.atmo-ui .bfill { position: absolute; left: 0; top: 0; bottom: 0; background: rgb(var(--acc)); }
.atmo-ui .btext { position: absolute; left: 0; top: 0; bottom: 0; width: 100%; display: flex; align-items: center;
  justify-content: center; font-family: var(--f-med); font-size: 13u; color: var(--text); white-space: nowrap;
  pointer-events: none; }
.atmo-ui .btext.on { color: var(--on-acc); left: -1px; width: var(--barw); }  /* (centred where the other is) */
.atmo-ui .bclip { position: absolute; left: 0; top: 0; bottom: 0; overflow: hidden; pointer-events: none; }
.atmo-ui .control { display: flex; align-items: center; flex: 0 0 auto; width: 270u; }
.atmo-ui .control.switchctl { justify-content: flex-end; }
.atmo-ui .control .icon-btn.reset { margin-left: 6u; }
/* an experimental setting's mark */
.atmo-ui .named { display: inline-flex; align-items: center; }
.atmo-ui .exp { display: inline-flex; margin-left: 6u; color: rgb(var(--exp)); }
.atmo-ui .exp svg { width: 14u; height: 14u; }
.atmo-ui.h .control { width: 310u; }
.atmo-ui .btn { display: inline-flex; align-items: center; justify-content: center; height: 30u; padding: 0 12u;
  border-radius: 3u; cursor: pointer; font-family: var(--f-med); font-size: 13u; color: var(--text);
  border: 1px solid var(--b4); background: transparent; }
.atmo-ui .btn:hover { border-color: rgba(var(--acc), 0.8); }
.atmo-ui .btn.primary { color: var(--on-acc); border-color: rgb(var(--acc)); background: rgb(var(--acc)); }
.atmo-ui .btn.armed { color: rgb(var(--dawn)); border-color: rgb(var(--dawn)); background: transparent; }
.atmo-ui .btn.danger { color: rgb(var(--rose)); border-color: rgba(var(--rose), 0.55); }
.atmo-ui .btn.inert { opacity: 0.35; cursor: default; }
.atmo-ui .btn.chosen { border-color: rgb(var(--acc)); color: rgb(var(--acc)); }
.atmo-ui .btn + .btn { margin-left: 6u; }
.atmo-ui .tag { display: inline-block; font-size: 11u; letter-spacing: 0.05em; color: var(--muted); padding: 1u 6u;
  border: 1px solid var(--b3); border-radius: 2u; white-space: nowrap; }
.atmo-ui .tag.unsaved { color: rgb(var(--dawn)); border-color: rgba(var(--dawn), 0.55); }
.atmo-ui .dot { display: inline-block; width: 7u; height: 7u; border-radius: 4u; margin-right: 7u; flex: 0 0 auto; }
.atmo-ui .dot.ok { background: rgb(var(--ok)); }
.atmo-ui .dot.wait { background: rgb(var(--dawn)); }
.atmo-ui .field { width: 100%; height: 30u; padding: 0 10u; border-radius: 3u; font-family: var(--f-reg); font-size: 14u;
  color: var(--text); background: var(--field); border: 1px solid var(--b3); outline: none; }
.atmo-ui .field:focus { border-color: rgb(var(--acc)); }

/* quick switches (overview) */
.atmo-ui .tiles { display: flex; flex-wrap: wrap; margin: 0 -4u; }
.atmo-ui .tile { flex: 1 1 165u; margin: 4u; display: flex; align-items: center; min-height: 40u; padding: 5u 10u;
  border: 1px solid var(--b2); border-radius: 3u; background: var(--soft); }
.atmo-ui .tile .t { flex: 1 1 auto; min-width: 0; font-size: 14u; padding-right: 8u; }
.atmo-ui .tile .t small { display: block; font-size: 10u; letter-spacing: 0.06em; color: var(--muted); }
.atmo-ui .chips { display: flex; flex-wrap: wrap; margin: 2u -3u 0; }
.atmo-ui .chip { margin: 3u; padding: 4u 10u; border-radius: 3u; cursor: pointer; font-size: 13u;
  border: 1px solid var(--b3); color: var(--text-2); }
.atmo-ui .chip:hover { border-color: rgba(var(--acc), 0.8); }
.atmo-ui .chip.on { border-color: rgb(var(--acc)); color: var(--text); }
.atmo-ui .chip.armed { border-color: rgb(var(--dawn)); color: rgb(var(--dawn)); }

/* the overview's status line: the add-on's connection at the left, the METAR link (pulsing while it polls) at the right */
.atmo-ui .update { display: flex; align-items: center; min-width: 0; margin-left: 10u; color: rgb(var(--acc));
  font-size: 13u; cursor: pointer; }
.atmo-ui .update span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.atmo-ui .update svg { width: 13u; height: 13u; margin-left: 5u; flex: 0 0 auto; }
.atmo-ui .update:hover span { text-decoration: underline; }
.atmo-ui .statusline .body { display: flex; align-items: center; justify-content: space-between; padding: 9u 14u; }
/* (a vertical tablet hasn't room for the newer release's notice beside the link: the link goes below) */
.atmo-ui.v .statusline .body.ahead { flex-direction: column; align-items: flex-start; }
.atmo-ui.v .statusline .body.ahead .linkrow { margin-left: 0; margin-top: 6u; }
.atmo-ui .linkrow { display: flex; align-items: center; color: var(--text-2); font-size: 13u; min-width: 0; margin-left: 12u; }
.atmo-ui .linkrow svg { width: 15u; height: 15u; margin-right: 7u; color: var(--muted); flex: 0 0 auto; }
.atmo-ui .linkrow .live { width: 7u; height: 7u; border-radius: 4u; margin-right: 7u; flex: 0 0 auto;
  background: rgb(var(--ok)); -webkit-animation: atmo-pulse 1.6s ease-in-out infinite; animation: atmo-pulse 1.6s ease-in-out infinite; }
.atmo-ui .linkrow.fail .live { background: rgb(var(--dawn)); -webkit-animation: none; animation: none; }
@-webkit-keyframes atmo-pulse { 0% { opacity: 1; } 50% { opacity: 0.2; } 100% { opacity: 1; } }
@keyframes atmo-pulse { 0% { opacity: 1; } 50% { opacity: 0.2; } 100% { opacity: 1; } }
.atmo-ui .metar-raw { font-size: 12u; color: var(--muted); word-wrap: break-word; margin-bottom: 6u; }
.atmo-ui .compare { margin-top: 8u; padding-top: 8u; border-top: 1px solid var(--b1); font-size: 13u; color: var(--text-2); }
.atmo-ui .compare p { margin: 0 0 4u; }
.atmo-ui .compare b { font-family: var(--f-med); font-weight: normal; color: var(--text); }

/* the performance report */
.atmo-ui .progress { height: 3u; background: var(--track); overflow: hidden; margin: 8u 0; }
.atmo-ui .progress > div { height: 100%; background: rgb(var(--acc)); }
.atmo-ui table.kv { width: 100%; border-collapse: collapse; font-size: 13u; }
.atmo-ui table.kv td { padding: 4u 0; border-top: 1px solid var(--b1); }
.atmo-ui table.kv td + td { text-align: right; color: var(--text-2); }
`);
