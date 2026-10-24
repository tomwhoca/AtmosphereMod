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
    logLevel: "info",
    target: "es2017",
    define: { BASE_URL: `"coui://html_ui/efb_ui/efb_apps/AtmoWeather"` },
    plugins: [
      // The EFB provides the MSFS SDK as a global; bundling our own copy would
      // create a second, disconnected instance.
      globalExternals.globalExternals({
        "@microsoft/msfs-sdk": { varName: "msfssdk", type: "cjs" },
      }),
    ],
  })
  .catch(() => process.exit(1));
