// The harness's dev server (tools/efb_harness.py runs it with the project's Node): builds the UI module and the
// harness into efb/harness/build, rebuilds on every save, and serves efb/harness with esbuild's live reload
// (the page listens on /esbuild; one context for both, so a change to either is announced there).

const path = require("path");
const esbuild = require(path.join(__dirname, "..", "PackageSources", "AtmoWeather", "node_modules", "esbuild"));

const HERE = __dirname;
const APP = path.join(HERE, "..", "PackageSources", "AtmoWeather");
const PORT = Number(process.env.ATMO_HARNESS_PORT || 8078);

async function main() {
  const ctx = await esbuild.context({
    entryPoints: { AtmoUI: path.join(APP, "src", "ui", "main.ts"), harness: path.join(HERE, "harness.ts") },
    outdir: path.join(HERE, "build"),
    bundle: true,
    format: "iife",
    target: "es2017",
    minify: false,
    logLevel: "info",
    define: { UI_BUILD: JSON.stringify("harness") },
  });
  await ctx.watch();
  const { host, port } = await ctx.serve({ servedir: HERE, host: "127.0.0.1", port: PORT });
  console.log(`harness: http://${host}:${port}/`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
