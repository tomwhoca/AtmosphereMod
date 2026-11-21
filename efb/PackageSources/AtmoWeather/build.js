// Bundles src/AtmoWeather.tsx into dist/ for html_ui/efb_ui/efb_apps/AtmoWeather.
// Trimmed from the SDK's EFB template (third_party/msfs_efb_sample): no Sass or
// PostCSS, the icon is copied directly. Type checking runs first (npm run build).

const esbuild = require("esbuild");
const fs = require("fs");
const path = require("path");
const globalExternals = require("@fal-works/esbuild-plugin-global-externals");

fs.rmSync(path.join(__dirname, "dist"), { recursive: true, force: true });
fs.mkdirSync(path.join(__dirname, "dist", "Assets"), { recursive: true });
fs.copyFileSync(path.join(__dirname, "src", "Assets", "app-icon.svg"), path.join(__dirname, "dist", "Assets", "app-icon.svg"));

esbuild
  .build({
    entryPoints: ["src/AtmoWeather.tsx"],
    keepNames: true,
    bundle: true,
    outdir: "dist",
    sourcemap: false,
    minify: false,
    // (drops the branches the defines below turn off: the developers' reload hook isn't in a release build at all)
    minifySyntax: true,
    logLevel: "info",
    target: "es2017",
    // VERSION: the mod's (tools/build_efb.py passes it from addon/CMakeLists.txt)
    define: { BASE_URL: `"coui://html_ui/efb_ui/efb_apps/AtmoWeather"`,
              VERSION: JSON.stringify(process.env.ATMO_VERSION || "dev"),
              // the developers' reload hook
              DEV: process.env.ATMO_DEV === "1" ? "true" : "false" },
    plugins: [
      // The EFB provides the MSFS SDK as a global; bundling our own copy would
      // create a second, disconnected instance.
      globalExternals.globalExternals({
        "@microsoft/msfs-sdk": { varName: "msfssdk", type: "cjs" },
      }),
    ],
  })
  .catch(() => process.exit(1));

// The UI module, its own bundle (window.AtmoUI): the app loads it, the harness and the developers' in-sim reload
// (tools/efb_reload.py) evaluate it again.
esbuild
  .build({
    entryPoints: ["src/ui/main.ts"],
    bundle: true,
    outfile: "dist/AtmoUI.js",
    format: "iife",
    minify: false,
    // (drops what DEV turns off: the developers' hook for tools/shots.py isn't in a release build at all)
    minifySyntax: true,
    logLevel: "info",
    target: "es2017",
    define: { UI_BUILD: JSON.stringify(new Date().toISOString()),
              DEV: process.env.ATMO_DEV === "1" ? "true" : "false" },
  })
  .catch(() => process.exit(1));
