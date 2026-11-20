// The UI module's stylesheet. Every length goes through --u, the tablet's size against the small one (1, 1.19,
// 1.38: the sim scales its own tablet chrome alike), so a design drawn at the small size holds at all three.
// The engine (CoherentGT 2.0) has no gap, sticky, clamp(), aspect-ratio or inset: tools/lint_efb.py checks.

export const UI_CSS = `
.atmo-ui { position: absolute; left: 0; top: 0; right: 0; bottom: 0; overflow: hidden;
  display: flex; flex-direction: column; color: #e8ecf2; background: #12161d;
  font-size: calc(20px * var(--u)); line-height: 1.35; }
.atmo-ui * { box-sizing: border-box; }
.atmo-ui-status { flex: 0 0 auto; height: calc(33px * var(--u)); }
.atmo-ui-dock { flex: 0 0 auto; height: calc(46px * var(--u)); }
.atmo-ui-body { flex: 1 1 auto; overflow-y: auto; overflow-x: hidden; padding: 0 calc(24px * var(--u)); }

/* (the layout check of the redesign's harness: phase 1) */
.atmo-ui-check .atmo-ui-status, .atmo-ui-check .atmo-ui-dock {
  background: repeating-linear-gradient(45deg, rgba(255, 196, 0, 0.18) 0, rgba(255, 196, 0, 0.18) calc(8px * var(--u)),
    transparent calc(8px * var(--u)), transparent calc(16px * var(--u)));
  font-size: calc(14px * var(--u)); color: #ffd25a; display: flex; align-items: center; justify-content: center; }
.atmo-ui-title { font-size: calc(30px * var(--u)); font-weight: 600; margin: calc(16px * var(--u)) 0 calc(4px * var(--u)); }
.atmo-ui-sub { opacity: 0.6; font-size: calc(17px * var(--u)); margin-bottom: calc(16px * var(--u)); }
.atmo-ui-facts { display: flex; flex-wrap: wrap; margin: 0 calc(-6px * var(--u)); }
.atmo-ui-fact { flex: 1 1 calc(260px * var(--u)); margin: calc(6px * var(--u)); padding: calc(12px * var(--u)) calc(14px * var(--u));
  background: #1b212b; border-radius: calc(10px * var(--u)); min-width: 0; }
.atmo-ui-fact b { display: block; font-size: calc(14px * var(--u)); font-weight: 500; opacity: 0.55; text-transform: uppercase;
  letter-spacing: 0.04em; margin-bottom: calc(4px * var(--u)); }
.atmo-ui-fact span { word-wrap: break-word; }
.atmo-ui-controls { margin: calc(18px * var(--u)) 0; }
.atmo-ui-row { display: flex; align-items: center; min-height: calc(56px * var(--u)); border-bottom: 1px solid #262e3a; }
.atmo-ui-row > .atmo-ui-label { flex: 1 1 auto; min-width: 0; }
.atmo-ui-btn { display: inline-block; padding: calc(10px * var(--u)) calc(18px * var(--u)); border-radius: calc(8px * var(--u));
  background: #2f6fed; color: #fff; cursor: pointer; }
.atmo-ui-btn:hover { background: #4a82f0; }
.atmo-ui-toggle { flex: 0 0 auto; width: calc(56px * var(--u)); height: calc(30px * var(--u)); border-radius: calc(15px * var(--u));
  background: #3a4352; position: relative; cursor: pointer; }
.atmo-ui-toggle::after { content: ""; position: absolute; top: calc(3px * var(--u)); left: calc(3px * var(--u));
  width: calc(24px * var(--u)); height: calc(24px * var(--u)); border-radius: 50%; background: #fff; }
.atmo-ui-toggle.on { background: #2f6fed; }
.atmo-ui-toggle.on::after { left: calc(29px * var(--u)); }
.atmo-ui-slider { flex: 0 0 auto; width: calc(220px * var(--u)); height: calc(36px * var(--u)); position: relative; cursor: pointer;
  margin-right: calc(12px * var(--u)); }
.atmo-ui-slider .track { position: absolute; left: 0; right: 0; top: calc(16px * var(--u)); height: calc(4px * var(--u));
  background: #3a4352; border-radius: calc(2px * var(--u)); }
.atmo-ui-slider .fill { position: absolute; left: 0; top: calc(16px * var(--u)); height: calc(4px * var(--u)); background: #2f6fed;
  border-radius: calc(2px * var(--u)); }
.atmo-ui-slider .thumb { position: absolute; top: calc(6px * var(--u)); width: calc(24px * var(--u)); height: calc(24px * var(--u));
  margin-left: calc(-12px * var(--u)); border-radius: 50%; background: #fff; }
.atmo-ui-value { flex: 0 0 auto; width: calc(64px * var(--u)); text-align: right; }
`;
