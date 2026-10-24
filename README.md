# AtmosphereMod

AtmosphereMod is an experimental ReShade add-on for **Microsoft Flight
Simulator 2024**. It aims to give the sim more realistic atmosphere and cloud
rendering, and consists of several components:

- **Haze.** MSFS 2024 does show atmospheric haze, to a certain extent. Visually
  it isn't convincing, and it is partly wrong in its calculation and its
  atmospheric behaviour:
  - it is the same haze on every flight, whatever the weather report says;
  - it is grey at every wavelength;
  - it thins out evenly with height, with no top, so there is no haze layer,
    no haze dome over the ground and no haze line on the horizon from cruise
    altitude.

  The mod replaces it with a layered haze, coloured by the kind of particles
  in it and set from the weather. It applies the same haze to the sky, the
  terrain and the clouds.
- **Sky colours.** MSFS colours its sky with its own scattering values, whose
  "ozone" absorbs mostly red light. Real ozone absorbs in the green and
  orange, which is what makes the deep blue sky overhead and the blue and
  purple of dusk. The mod uses real-world values, plus a daylight white
  balance so that sunlight still reads as white.
- **Cloud lighting.** MSFS lights its volumetric clouds with a mild forward
  scattering and simple stand-ins for the light bounced inside them, which
  gives evenly lit, "cotton-ball" clouds. Silver linings are weak, and there
  is no glory and no shadow of your aircraft on the clouds. The mod changes
  how MSFS's own cloud renderer lights the clouds, to look more like real
  ones.
- **Cloud forming** (very experimental). This is just the beginning of
  experimenting with cloud shapes. MSFS's live-weather clouds are far thinner
  than real ones, puffy all over, and only loosely follow the real weather
  reports. The mod makes clouds more solid with flat bases, and pulls the
  clouds near weather stations toward the layers their METARs report.
- **METAR passing.** MSFS 2024 gives the METARs of its live weather only to
  JavaScript running inside the sim, not to add-ons like this one. A small
  EFB app, included with the mod, fetches them and passes them on. They drive
  the haze and the cloud forming.

> **Use at your own risk.** AtmosphereMod is an unofficial, experimental mod,
> provided as is, without any warranty. It has been developed and tested on a
> single PC. On other hardware or drivers it may not work, may show graphical
> errors, may crash the sim or may cost performance. See the
> [disclaimer](#disclaimer).

> **Draft.** Not released yet.

## Screenshots

Taken on 24 October 2026 with live weather and the shipping settings. Each
pair was taken a few seconds apart, switching with Ctrl+Shift+F9. Under each
pair is the METAR the sim gave the mod at that moment. For San Francisco the
time of day was set to sunset.

### Haze

<table>
  <tr>
    <th width="50%">Mod off (MSFS 2024)</th>
    <th width="50%">Mod on</th>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-niamey-off.jpg" alt="Mod off: Niamey, dust at 6 km"></td>
    <td><img src="Screenshots/haze-niamey-on.jpg" alt="Mod on: Niamey, dust at 6 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Niamey</b>, dust at 6 km: the haze thickens to the reported visibility. MSFS shows its usual light, lavender-tinted haze.<br><sub>METAR <code>DRRN 240900Z 16005KT 120V200 6000 NSC 34/11 Q1014 TEMPO 4000 DU</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-dakar-off.jpg" alt="Mod off: Dakar, dust at 1.6 km"></td>
    <td><img src="Screenshots/haze-dakar-on.jpg" alt="Mod on: Dakar, dust at 1.6 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Dakar</b>, dust at 1.6 km: a dense, brownish dust haze over the city and the sea.<br><sub>METAR <code>GOBD 240930Z 05011KT 1600 DU NSC 30/18 Q1013 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-mumbai-off.jpg" alt="Mod off: Mumbai, humid haze at 1.8 km"></td>
    <td><img src="Screenshots/haze-mumbai-on.jpg" alt="Mod on: Mumbai, humid haze at 1.8 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Mumbai</b>, humid haze at 1.8 km: a white haze that glows toward the sun.<br><sub>METAR <code>VABB 241000Z 29010KT 1800 HZ NSC 34/24 Q1007 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-delhi-off.jpg" alt="Mod off: Delhi, haze at 5 km"></td>
    <td><img src="Screenshots/haze-delhi-on.jpg" alt="Mod on: Delhi, haze at 5 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Delhi</b>, haze at 5 km: a grey haze in place of MSFS's lavender one.<br><sub>METAR <code>VIDP 241000Z 15005KT 5000 HZ FEW100 31/16 Q1010 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-amman-off.jpg" alt="Mod off: Amman, dry haze at 5 km"></td>
    <td><img src="Screenshots/haze-amman-on.jpg" alt="Mod on: Amman, dry haze at 5 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Amman</b>, dry haze at 5 km: a pale blue sky with a few cumulus, as reported.<br><sub>METAR <code>OJAI 241000Z 12005KT 5000 HZ FEW035 29/12 Q1017 NOSIG</code></sub></td>
  </tr>
</table>

### Sky colours

<table>
  <tr>
    <th width="50%">Mod off (MSFS 2024)</th>
    <th width="50%">Mod on</th>
  </tr>
  <tr>
    <td><img src="Screenshots/sky-san-francisco-sunset-off.jpg" alt="Mod off: San Francisco at sunset, the sun 2&deg; high"></td>
    <td><img src="Screenshots/sky-san-francisco-sunset-on.jpg" alt="Mod on: San Francisco at sunset, the sun 2&deg; high"></td>
  </tr>
  <tr>
    <td colspan="2"><b>San Francisco</b> at sunset, the sun 2&deg; high: the clouds glow orange and red under a purple sky.<br><sub>METAR <code>KSFO 240056Z 28016KT 10SM FEW035 18/11 A3002 RMK AO2 SLP165 T01780111</code></sub></td>
  </tr>
</table>

### Cloud lighting: the glory

<table>
  <tr>
    <th width="50%">Mod off (MSFS 2024)</th>
    <th width="50%">Mod on</th>
  </tr>
  <tr>
    <td><img src="Screenshots/glory-stockholm-1-off.jpg" alt="Mod off: Near Stockholm"></td>
    <td><img src="Screenshots/glory-stockholm-1-on.jpg" alt="Mod on: Near Stockholm"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Near Stockholm</b>: the glory around the aircraft's shadow, on the deck the METARs report. MSFS shows scattered cumulus here.<br><sub>METAR <code>ESCM 241050Z AUTO 12009KT 9999 SCT010/// BKN015/// BKN076/// 11/09 Q1012</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/glory-stockholm-2-off.jpg" alt="Mod off: The same flight, two and a half minutes later."></td>
    <td><img src="Screenshots/glory-stockholm-2-on.jpg" alt="Mod on: The same flight, two and a half minutes later."></td>
  </tr>
  <tr>
    <td colspan="2">The same flight, two and a half minutes later.<br><sub>METAR <code>ESCM 241050Z AUTO 12009KT 9999 SCT010/// BKN015/// BKN076/// 11/09 Q1012</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/glory-stockholm-3-off.jpg" alt="Mod off: And half a minute after that."></td>
    <td><img src="Screenshots/glory-stockholm-3-on.jpg" alt="Mod on: And half a minute after that."></td>
  </tr>
  <tr>
    <td colspan="2">And half a minute after that.<br><sub>METAR <code>ESCM 241050Z AUTO 12009KT 9999 SCT010/// BKN015/// BKN076/// 11/09 Q1012</code></sub></td>
  </tr>
</table>

### Cloud lighting and forming

<table>
  <tr>
    <th width="50%">Mod off (MSFS 2024)</th>
    <th width="50%">Mod on</th>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-tenerife-off.jpg" alt="Mod off: Tenerife North"></td>
    <td><img src="Screenshots/clouds-tenerife-on.jpg" alt="Mod on: Tenerife North"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Tenerife North</b>: scattered cloud, as reported, lit bright from inside. MSFS shows a dark overcast.<br><sub>METAR <code>GCXO 241030Z 11007KT 070V160 9999 SCT009 SCT015 20/18 Q1027 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-bratislava-off.jpg" alt="Mod off: Bratislava"></td>
    <td><img src="Screenshots/clouds-bratislava-on.jpg" alt="Mod on: Bratislava"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Bratislava</b>: firm, sunlit cumulus with clear air between the layers. MSFS's clouds blur into grey.<br><sub>METAR <code>LZIB 241030Z 07003KT 050V110 9999 FEW008 SCT033 11/08 Q1019 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-cologne-off.jpg" alt="Mod off: Cologne, two broken layers"></td>
    <td><img src="Screenshots/clouds-cologne-on.jpg" alt="Mod on: Cologne, two broken layers"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Cologne</b>, two broken layers: wide, flat-based clouds instead of separate puffs.<br><sub>METAR <code>EDDK 241120Z AUTO 24014KT 9999 BKN034 BKN042 11/04 Q1014 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-tbilisi-off.jpg" alt="Mod off: Tbilisi"></td>
    <td><img src="Screenshots/clouds-tbilisi-on.jpg" alt="Mod on: Tbilisi"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Tbilisi</b>: firmer cumulus with deeper shading, and more blue sky between.<br><sub>METAR <code>UGTB 241100Z 22004KT 160V310 9999 FEW061 19/12 Q1022 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-stockholm-cockpit-off.jpg" alt="Mod off: Stockholm Arlanda, from the cockpit"></td>
    <td><img src="Screenshots/clouds-stockholm-cockpit-on.jpg" alt="Mod on: Stockholm Arlanda, from the cockpit"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Stockholm Arlanda</b>, from the cockpit: scattered cloud with blue sky between, as reported. MSFS closes it to an overcast.<br><sub>METAR <code>ESSA 241050Z 11009KT 9999 SCT011 10/08 Q1012 TEMPO BKN012</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-cape-town-off.jpg" alt="Mod off: Cape Town"></td>
    <td><img src="Screenshots/clouds-cape-town-on.jpg" alt="Mod on: Cape Town"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Cape Town</b>: broad, flat-based clouds over Table Bay instead of round puffs.<br><sub>METAR <code>FACT 241000Z 17011KT 140V200 9999 BKN016 20/14 Q1020 NOSIG</code></sub></td>
  </tr>
</table>

## What it does

Ctrl+Shift+F9 switches between the mod and stock at any time, for comparison.
The settings named below are in `tunables.ini` ([Settings](#settings)).

### Haze

What it can do:
- **A haze layer with a top.**
  - Most of the haze sits in the boundary layer, the lowest one or two
    kilometres. Its top has an adjustable height and sharpness (`T1`–`T3`).
  - From cruise altitude you see the haze dome over the ground and a sharp
    haze line on the horizon, with clear air above.
  - A thin haze in the free troposphere above it thins out with height
    (`T4`, `T5`).
- **Haze by particle type.**
  - Its colour: fine continental haze is slightly bluish, humid or maritime
    haze white, smoke and dust brownish (`T10`, with absorbing brown haze
    `T6`).
  - Its glare: the forward-scattering glow toward the sun (`T9`), plus a weak
    glow away from it (`T7`).
- **Haze from the weather** (`T13 = 1`). These come from the METAR nearest
  the aircraft, and changes are smoothed over about 30 seconds:
  - the visibility;
  - the humidity, from the temperature and dew point;
  - weather codes such as mist (BR), haze (HZ), smoke (FU) and dust (DU).

  Together they set the amount of haze, the layer's top and its type.
- **The same haze everywhere.** It applies to:
  - the sky;
  - the terrain and buildings;
  - the clouds, where distant ones turn creamy as blue light is scattered
    out (`T11`);
  - reflections, and the sky light the clouds are lit with.

  At high altitude it also reaches distant low clouds beyond the sim's own
  range of about 357 km (`T12`).

What it can't do:
- **Fog and mist layers.** The sim draws low fog and mist as a separate
  system, which the mod leaves alone. In fog or very low visibility, most of
  what you see is still MSFS's own fog.
- **Changes across the landscape.** The haze varies with height only. It
  can't show a smog plume over one city or haze in one valley; it follows the
  nearest METAR.
- **Without METARs.** If the EFB app isn't installed, or the sim gives no
  METARs, the fixed values `T1`–`T6` are used.

### Sky colours

What it can do:
- **Real-world scattering** (`T8 = 1`): the Rayleigh and ozone values of the
  published reference models (Bruneton, Hillaire), so the sky is deeper blue
  and dusk turns blue and purple as it does outside.
- **A daylight white balance** (`C22`):
  - Sunlight is slightly warm on its own. Eyes and cameras adapt to that,
    but the sim shows it against a fixed white. With real-world sky colours,
    cloud interiors then look beige-pink.
  - The white balance makes sunlight read as white, as you'd see it.
  - It shifts the sky, the clouds and the sunlit terrain alike.

What it can't do:
- The brightness and colour of the sun and moon themselves, the stars and the
  night-time light pollution stay the sim's.
- It doesn't change the sim's exposure or the image's overall colour grading.

### Cloud lighting

What it can do, inside MSFS's own cloud renderer:
- **Silver linings.** A sharper forward-scattering lobe (`C1`, `C2`) brightens
  cloud edges toward the sun. Inside a cloud, the sun becomes a bright glow.
- **Light bounced inside the clouds.** Cloud sides and interiors are brighter,
  as in real clouds lit by light scattered many times (`C3`–`C5`).
- **The "powder" effect.** With the sun behind you, thin edges look grey and
  dense cores white, which outlines each cloud turret (`C6`, `C7`).
- **A glory.** Coloured rings appear around your shadow on sunlit cloud tops,
  sized by the droplet size (`C9`–`C11`).
- **Your aircraft's shadow on the clouds** (`C12`):
  - with Raytraced Shadows off, it is taken from the sun's shadow map;
  - with them on, it is traced from the sim's ray-tracing scene;
  - its edges soften with distance, as the sun's disc blurs them.
- **Crisper cloud edges** (`C8`, off by default).

What it can't do:
- The clouds' shapes, their resolution and the way MSFS steps through them
  stay the sim's. The mod changes how they are lit.
- Rain shafts, rainbows, lightning and the light of cities on cloud bases at
  night are the sim's.

### Cloud forming (very experimental)

What it can do:
- **Solid clouds** (`C16`, `C17`):
  - MSFS's clouds are far thinner than real ones, so their bases and edges
    are see-through and the sun glows through an overcast.
  - The mod makes low clouds and cumulus solid from the base up, without
    making them darker.
- **Translucent high clouds** (`C18`, `C19`). Cirrus and thin mid-level
  sheets keep a translucent, soft look; `C19` makes cirrus more visible.
- **Flat cloud bases** (`C28`–`C31`):
  - MSFS roughens every cloud surface with the same round puffs, 100 to 250 m
    across, which look like popcorn under solid cloud bases.
  - The mod fades them out toward each cloud's base, so bases are flat and
    tops keep their billows.
  - Optionally it adds finer billows on the tops (`C30`, off by default).
- **METAR anchoring** (`C20`, `C21`, live weather). Near weather stations, the
  sim's clouds are pulled toward the layers the METARs report:
  - **What follows the report:** the cover (FEW, SCT, BKN, OVC), the base
    height, and a cloud type:
    - stratus and stratocumulus decks;
    - cumulus;
    - altocumulus and altostratus;
    - cirrus;
    - CB and TCU, as a base layer with isolated towers.
  - **Several stations at once:** up to 8 stations are blended by distance,
    and stations ahead of you are looked up before you get there.
  - **Gradual changes:** a new station fades in over 3 minutes, and a new
    report crossfades over 5 minutes.

What it can't do:
- **Cloud type and depth aren't reported.** A METAR gives only each layer's
  cover and base. Type and depth are inferred from the height, the cover, the
  temperature and dew point and the time of day. So the clouds near a station
  match its report, not necessarily the real sky.
- **Only what a METAR can see.** Above about 12,000 ft over the station (5,000
  ft with CAVOK or NSC, or the top of an overcast), the sim's own clouds stay.
  Far from any station, the sim's own weather shows.
- **Live weather only.** Weather presets and third-party weather engines (such
  as Active Sky) keep their own cloud layers.
- **Experimental.** Shapes and transitions can look wrong in some weather.
  `C20 = 0` switches the anchoring off.

### METAR passing

**Why it's needed.** MSFS 2024 makes the METARs of its live weather available
only to JavaScript running inside the sim, through the SDK's facility loader.
SimConnect's weather observation functions return errors in MSFS 2024, so an
add-on outside the sim can't read them directly.

**What the EFB app does.**
- The app is called "AtmosphereMod" and is installed into your Community
  folder.
- It runs in the background on every aircraft's tablet (EFB) from the start
  of the flight. You don't need to open it; opening it shows its status and
  the last METAR it passed on.
- When the add-on asks, it fetches a METAR from the sim and sends it to the
  add-on over the sim's CommBus, through SimConnect.

**Which METARs.**
- Every minute: the METAR nearest the aircraft, for the haze and the clouds.
- Every 15 seconds, in turn: one more, nearest a point ahead of or beside the
  aircraft (50 and 110 km ahead, 60 km to either side), so the cloud
  anchoring knows the stations ahead.
- For each new station, the add-on asks SimConnect for the station's position
  and elevation.

These are the sim's own METARs, describing the same weather MSFS renders. No
outside weather service or Active Sky is involved. Everything the add-on does
with them is logged in `%LOCALAPPDATA%\AtmosphereMod\logs\weather.log`.

**What it can't do.**
- Without the EFB app, the haze uses fixed values and the cloud anchoring
  stays off.
- Where stations are far apart (over oceans, in remote areas), the anchoring
  is coarse.

## Requirements

- Microsoft Flight Simulator 2024 on PC (MS Store / Xbox app or Steam),
  running DirectX 12.
- [ReShade](https://reshade.me) **6.8 or newer, the build "with full add-on
  support"**, installed for `FlightSimulator2024.exe`. The standard ReShade
  build only loads approved add-ons.
- Windows 10 or 11 and a DirectX 12 graphics card.
- [Python](https://www.python.org/downloads/) 3.10 or newer, for the
  installer (unless the release bundles its own).
- Microsoft's DirectX Shader Compiler, for the installer. It uses the Windows
  SDK's copy if you have one; otherwise it offers to download Microsoft's
  official release (about 26 MB, once).
- For the METAR features: the included EFB app (installed into your Community
  folder). It runs in the background in any aircraft and passes the sim's own
  METARs to the add-on.

### Tested systems

So far the mod has been tested on one PC only:

| | |
|---|---|
| Graphics card | NVIDIA GeForce RTX 5080, driver 617.14 |
| CPU | AMD Ryzen 9 7900X3D |
| Windows | Windows 11 Home |
| Sim | MSFS 2024 1.9.18.0, MS Store version, DirectX 12 |
| ReShade | 6.8.0 with full add-on support |

Nothing is known yet about other systems. If you try it, please report how it
went, whether it works or not, on the
[issues page](https://github.com/tomwhoca/AtmosphereMod/issues). Include your
graphics card and driver version, Windows version, and whether you run the MS
Store or Steam version. Systems that have been reported will be added here.

## Installation

1. Install ReShade with full add-on support for MSFS 2024, and start the sim
   once with it.
2. Close the sim, unzip the AtmosphereMod release anywhere, and double-click
   **`Install AtmosphereMod.cmd`**. The installer:
   - finds your MSFS 2024 installation, ReShade next to it, and your
     Community folder (it shows what it found and asks before changing
     anything);
   - extracts the sim's shaders from **your own copy of the game** and patches
     them on your PC, in a work folder it deletes afterwards (the mod contains
     none of the game's files);
   - puts the patched shaders and `tunables.ini` into
     `%LOCALAPPDATA%\AtmosphereMod`;
   - installs the add-on next to ReShade, the EFB app into your Community
     folder, and an `[ATMOSPHEREMOD]` section into `ReShade.ini` (after making
     a backup, `ReShade.ini.atmospheremod.bak`).
3. Start the sim.

Windows may warn you before running the `.cmd` files, because they were
downloaded from the internet and aren't signed:
- "Windows protected your PC": click **More info**, then **Run anyway**;
- "The publisher could not be verified": click **Run**.

To avoid the warnings, right-click the zip before unzipping it, choose
**Properties**, tick **Unblock** and click **OK**. Only do this with a release
downloaded from the
[AtmosphereMod GitHub page](https://github.com/tomwhoca/AtmosphereMod/releases).

The game's own files stay untouched: the add-on swaps shaders in memory as the
sim creates them. The installer's log is in
`%LOCALAPPDATA%\AtmosphereMod\logs\install.log`.

### After a sim update

Run the installer again. It patches the updated shaders and keeps your
`tunables.ini`, adding any settings that are new in this version. Shaders
that the update changed in ways the patcher doesn't recognise are skipped and
listed. Those parts simply fall back to the sim's own shaders, so an update
can't break the sim.

## Using it

All hotkeys use **Ctrl+Shift**:

| Key | Action |
|---|---|
| F9 | switch between the mod and stock |
| F10 | reload the settings (`tunables.ini`) and shaders, without restarting |
| F11 | write a diagnostic capture (useful for bug reports) |

### Settings

Everything is set in `tunables.ini` and takes effect with Ctrl+Shift+F10.
Each setting is explained in the file. The ones you're most likely to touch:

| Setting | What it does |
|---|---|
| `T0` | atmosphere: 0 = stock, 1 = the mod |
| `T13` | haze from the sim's METARs (1), or the fixed values `T1`–`T6` (0) |
| `T3`, `T4` | haze in the boundary layer and above it (when `T13` = 0) |
| `T8` | sky colours: 0 = the sim's, 1 = real-world physics |
| `C22` | daylight white balance (0 = off, 1 = on) |
| `C0` | cloud lighting: 0 = stock, 1 = the mod |
| `C1`, `C2` | silver lining: how tight around the sun, how strong |
| `C9` | glory strength (0 = off) |
| `C12` | darkness of your aircraft's shadow on the clouds (0 = off) |
| `C16`, `C17` | cloud thickness (0 = the sim's) and how solid the bases are |
| `C19` | thickness of high, icy clouds (cirrus) |
| `C20`, `C21` | METAR anchoring: strength (0 = off) and each station's radius (km) |
| `C28` | smooth, flat cloud bases (0 = the sim's puffs) |

## Uninstalling

Close the sim and double-click **`Uninstall AtmosphereMod.cmd`**. It removes the
add-on, the EFB app and the `[ATMOSPHEREMOD]` section from `ReShade.ini`, and
asks before deleting `%LOCALAPPDATA%\AtmosphereMod` (the patched shaders, logs
and your `tunables.ini`). ReShade itself stays installed.

## Known limitations

- METAR anchoring works with live weather only. Weather presets and
  third-party weather engines keep their own cloud layers.
- After a sim update, parts of the mod fall back to stock until it is
  re-patched.
- DirectX 12 only. The performance cost hasn't been measured on other systems
  yet.

## Disclaimer

- AtmosphereMod is unofficial and experimental. It is provided "as is",
  without warranty of any kind, express or implied.
- **You install and use it at your own risk.**
  - It has been developed and tested on a single PC: one CPU, one graphics
    card and driver, one Windows installation.
  - Other hardware, drivers, Windows versions, sim versions and add-ons
    haven't been tested. With them it may not work, or may cause graphical
    errors, crashes or lower performance.
- To the extent permitted by law, the author is not liable for any damage,
  data loss or other harm arising from installing or using it.
- What it changes on your PC:
  - a ReShade add-on next to the sim;
  - an EFB app in your Community folder;
  - an `[ATMOSPHEREMOD]` section in `ReShade.ini`, backed up first;
  - its own folder, `%LOCALAPPDATA%\AtmosphereMod`.

  It changes none of the game's files, and the uninstaller removes what it
  installed.
- If something looks wrong, Ctrl+Shift+F9 switches back to the stock shaders
  at once. To remove the mod completely, run the uninstaller.
- Not affiliated with or endorsed by Microsoft or Asobo Studio. The mod
  contains none of the game's files: it patches only the shaders of your own
  installation, on your own PC.

## Licence

AtmosphereMod is freeware under the [AtmosphereMod Freeware Licence](LICENSE).
In short:
- free to download and use for your personal, non-commercial use, and to show
  in screenshots, videos and streams;
- no re-uploads or redistribution, no modified versions, and no selling,
  without the author's written permission;
- the shaders the installer makes from your copy of the game stay on your PC.

Third-party components keep their own licences (`THIRD-PARTY-NOTICES.txt`).

## Credits

- [ReShade](https://reshade.me) by Patrick Mours (add-on API headers,
  BSD 3-Clause; see `third_party/reshade/LICENSE.md`).
- The atmosphere builds on Eric Bruneton and Fabrice Neyret, "Precomputed
  Atmospheric Scattering" (2008), and Sébastien Hillaire, "A Scalable and
  Production Ready Sky and Atmosphere Rendering Technique" (2020).
- The Microsoft Flight Simulator 2024 SDK: SimConnect and the EFB framework.
