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

## Screenshots

Taken on 26 October 2026 with live weather, each pair a few seconds apart
with the mod off and on.
The settings were the shipping ones except for solid clouds 4 (2 by default),
crisper cloud edges 2 (off by default) and small changes to the cloud
lighting.

### Haze

<table>
  <tr>
    <th width="50%">Mod off (MSFS 2024)</th>
    <th width="50%">Mod on</th>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-monterrey-off.jpg" alt="Mod off: Monterrey, humid haze at 8 km"></td>
    <td><img src="Screenshots/haze-monterrey-on.jpg" alt="Mod on: Monterrey, humid haze at 8 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Monterrey</b>, humid haze at 8 km: a bright, milky haze, with the mountains fading into it. MSFS shows clear air under a dark overcast.<br><sub>METAR <code>MMMY 261340Z 00000KT 5SM BKN020 BKN080 20/19 A3022 RMK 8/530 HZY</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-tenerife-south-off.jpg" alt="Mod off: Tenerife South, haze at 9 km"></td>
    <td><img src="Screenshots/haze-tenerife-south-on.jpg" alt="Mod on: Tenerife South, haze at 9 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Tenerife South</b>, haze at 9 km: a pale haze over the coast, with the distance fading as reported. MSFS's air is clear.<br><sub>METAR <code>GCTS 261400Z 11008KT 9000 NSC 27/22 Q1016 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-keflavik-off.jpg" alt="Mod off: Keflavík, drizzle and mist at 2 km"></td>
    <td><img src="Screenshots/haze-keflavik-on.jpg" alt="Mod on: Keflavík, drizzle and mist at 2 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Keflavík</b>, drizzle and mist at 2 km: the mist closes in as reported. MSFS shows blue sky and clouds beyond it.<br><sub>METAR <code>BIKF 261330Z 16003KT 2000 -DZ BR FEW002 BKN005 04/03 Q0983</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-nouakchott-off.jpg" alt="Mod off: Nouakchott, 6 km in very dry air"></td>
    <td><img src="Screenshots/haze-nouakchott-on.jpg" alt="Mod on: Nouakchott, 6 km in very dry air"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Nouakchott</b>, 6 km in very dry air (the METAR has no weather code; the mod infers dust from the humidity): the haze closes in to the reported visibility. MSFS's view is clear to the horizon.<br><sub>METAR <code>GQNO 261400Z 12014KT 6000 NSC 41/07 Q1011 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/haze-itaituba-off.jpg" alt="Mod off: Itaituba, smoke and mist at 3 km"></td>
    <td><img src="Screenshots/haze-itaituba-on.jpg" alt="Mod on: Itaituba, smoke and mist at 3 km"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Itaituba</b>, in the Amazon, smoke and mist at 3 km: the haze closes in to the reported visibility. MSFS keeps a shallow haze layer below.<br><sub>METAR <code>SBIH 261400Z 12004KT 3000 BR FU SCT017 BKN080 27/24 Q1014</code></sub></td>
  </tr>
</table>

### Sky colours and low sun

<table>
  <tr>
    <th width="50%">Mod off (MSFS 2024)</th>
    <th width="50%">Mod on</th>
  </tr>
  <tr>
    <td><img src="Screenshots/sky-san-francisco-dawn-off.jpg" alt="Mod off: San Francisco before sunrise"></td>
    <td><img src="Screenshots/sky-san-francisco-dawn-on.jpg" alt="Mod on: San Francisco before sunrise"></td>
  </tr>
  <tr>
    <td colspan="2"><b>San Francisco</b> before sunrise: the sky and the bay turn pink and violet, with the clouds dark against them. MSFS's dawn is blue-grey.<br><sub>METAR <code>KSFO 261356Z 17003KT 10SM FEW009 BKN027 13/12 A3014 RMK AO2 SLP205 T01330117 $</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/sky-jackson-hole-sunrise-off.jpg" alt="Mod off: Jackson Hole at sunrise"></td>
    <td><img src="Screenshots/sky-jackson-hole-sunrise-on.jpg" alt="Mod on: Jackson Hole at sunrise"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Jackson Hole</b> at sunrise: the underside of the overcast glows orange above the Tetons. MSFS's is a flat yellow.<br><sub>METAR <code>KJAC 261356Z 00000KT 10SM BKN041 OVC070 01/M01 A3022 RMK AO2 SLP244 T00111006</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/sky-tbilisi-off.jpg" alt="Mod off: Tbilisi in the late afternoon"></td>
    <td><img src="Screenshots/sky-tbilisi-on.jpg" alt="Mod on: Tbilisi in the late afternoon"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Tbilisi</b> in the late afternoon: the low sun lights the cumulus in warm colours, with deeper shading. MSFS's clouds stay white.<br><sub>METAR <code>UGTB 261300Z 16004KT 9999 SCT025 BKN039 15/11 Q1022 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/sky-santorini-off.jpg" alt="Mod off: Santorini in the late afternoon"></td>
    <td><img src="Screenshots/sky-santorini-on.jpg" alt="Mod on: Santorini in the late afternoon"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Santorini</b> in the late afternoon: a warm, hazy sky over the caldera, with a flat band of low cloud, as reported. MSFS covers the sky in white cloud.<br><sub>METAR <code>LGSR 261350Z 24007KT 9999 SCT020 20/12 Q1009</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/sky-bergen-off.jpg" alt="Mod off: Bergen in the low afternoon sun"></td>
    <td><img src="Screenshots/sky-bergen-on.jpg" alt="Mod on: Bergen in the low afternoon sun"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Bergen</b> in the low afternoon sun: broken cloud with blue sky between, and the sun bright on the fjord. MSFS shows a dark shower cloud.<br><sub>METAR <code>ENBR 261350Z 24009KT 180V280 9999 VCSH BKN040CB 10/03 Q0998 TEMPO 24010G20KT RMK WIND 1200FT 28020G31KT</code></sub></td>
  </tr>
</table>

### Cloud lighting

With the mod only: MSFS has neither a glory nor its strong silver lining.

<table>
  <tr>
    <td width="50%"><img src="Screenshots/glory-london.jpg" alt="The glory around the aircraft's shadow on the cloud tops, from the cabin window"></td>
    <td width="50%"><img src="Screenshots/cloud-lighting-london.jpg" alt="The cloud tops glowing toward the low sun, from the cockpit"></td>
  </tr>
  <tr>
    <td><b>The glory</b>, climbing out of London above the overcast: coloured rings around the aircraft's shadow on the cloud tops, seen from the cabin window.</td>
    <td><b>Toward the low sun</b>, over the same deck: the cloud tops glow with forward-scattered light, the sun 12&deg; high.</td>
  </tr>
  <tr>
    <td colspan="2"><sub>METAR <code>EGWU 261450Z 21010KT 6000 -DZ FEW005 OVC008 15/15 Q1006 TEMPO 9999 NSW</code></sub></td>
  </tr>
</table>

### Clouds

<table>
  <tr>
    <th width="50%">Mod off (MSFS 2024)</th>
    <th width="50%">Mod on</th>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-palanga-off.jpg" alt="Mod off: Palanga"></td>
    <td><img src="Screenshots/clouds-palanga-on.jpg" alt="Mod on: Palanga"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Palanga</b>: the broken layers and the CB the METAR reports, a dark deck with light under its edge. MSFS shows scattered cumulus.<br><sub>METAR <code>EYPA 261320Z 23008KT 170V280 9999 SCT019 BKN023 BKN029CB 12/08 Q1011</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-westman-islands-off.jpg" alt="Mod off: Westman Islands"></td>
    <td><img src="Screenshots/clouds-westman-islands-on.jpg" alt="Mod on: Westman Islands"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Westman Islands</b>: a field of altocumulus with blue gaps, as reported at 16,000 ft. MSFS draws heavy grey layers.<br><sub>METAR <code>BIVM 261400Z 36005KT 9999 FEW014 BKN160 03/00 Q0982</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-clermont-ferrand-off.jpg" alt="Mod off: Clermont-Ferrand"></td>
    <td><img src="Screenshots/clouds-clermont-ferrand-on.jpg" alt="Mod on: Clermont-Ferrand"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Clermont-Ferrand</b>, layered stratocumulus: smooth, flat bases with soft edges. MSFS's clouds are streaky grey.<br><sub>METAR <code>LFLC 261400Z AUTO 15003KT 090V230 9999 SCT029 BKN041 BKN047 16/09 Q1020 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-shannon-off.jpg" alt="Mod off: above Shannon"></td>
    <td><img src="Screenshots/clouds-shannon-on.jpg" alt="Mod on: above Shannon"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Above Shannon</b>, on the stratocumulus deck the METAR reports: smooth, soft tops. MSFS shows a field of lumpy cumulus.<br><sub>METAR <code>EINN 261400Z 17018KT 9999 -RA FEW018 BKN020 BKN035 17/14 Q0990 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-bordeaux-off.jpg" alt="Mod off: Bordeaux"></td>
    <td><img src="Screenshots/clouds-bordeaux-on.jpg" alt="Mod on: Bordeaux"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Bordeaux</b>: broken cumulus with blue sky between, as reported. MSFS closes it to a dark overcast.<br><sub>METAR <code>LFBD 261400Z AUTO VRB03KT 9999 BKN028 20/15 Q1019 NOSIG</code></sub></td>
  </tr>
  <tr>
    <td><img src="Screenshots/clouds-horta-off.jpg" alt="Mod off: Horta, Azores"></td>
    <td><img src="Screenshots/clouds-horta-on.jpg" alt="Mod on: Horta, Azores"></td>
  </tr>
  <tr>
    <td colspan="2"><b>Horta</b>, Azores: firm cumulus over the island with blue sky between, and a deeper blue sea.<br><sub>METAR <code>LPHR 261400Z 34011KT 300V040 9999 FEW025 BKN060 19/12 Q1018</code></sub></td>
  </tr>
</table>

### Settings on the EFB tablet

The AtmosphereMod app's four tabs (see [Settings](#settings)), here with a few
settings changed from the defaults.

<table>
  <tr>
    <td width="50%"><a href="Screenshots/efb-haze.png"><img src="Screenshots/efb-haze.png" alt="EFB app: the Haze tab"></a></td>
    <td width="50%"><a href="Screenshots/efb-sky-light.png"><img src="Screenshots/efb-sky-light.png" alt="EFB app: the Sky &amp; light tab"></a></td>
  </tr>
  <tr>
    <td><b>Haze</b>: the atmosphere and the METAR's haze; the manual haze is folded away.</td>
    <td><b>Sky &amp; light</b>: real-world sky colours and the daylight white balance.</td>
  </tr>
  <tr>
    <td><a href="Screenshots/efb-cloud-lighting.png"><img src="Screenshots/efb-cloud-lighting.png" alt="EFB app: the Cloud lighting tab"></a></td>
    <td><a href="Screenshots/efb-clouds.png"><img src="Screenshots/efb-clouds.png" alt="EFB app: the Clouds tab"></a></td>
  </tr>
  <tr>
    <td><b>Cloud lighting</b>, with its "More" section open: silver lining, glory, your aircraft's shadow.</td>
    <td><b>Clouds</b>, with "More" open: the METAR anchoring, solid clouds, cloud types and smooth bases; under "More", each cloud type's thickness.</td>
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
- **Cloud types** (`C23`–`C27`, live weather). Real clouds aren't all equally
  solid: cumulus are dense with crisp edges, while stratiform clouds hold less
  water, spread more evenly, and have softer edges.
  - MSFS's weather has no cloud types, so the mod infers them from each
    layer's height, depth and cover. Thin broken or overcast decks become
    stratocumulus, stratus, altocumulus or altostratus. Scattered low clouds
    and deep clouds stay cumulus, and cirrus keeps its own look.
  - Each stratiform type gets its own share of the solid-cloud thickness
    (`C24`–`C27`: 60, 50, 40 and 30 % by default) and the sim's soft edges.
    Stratus and stratocumulus decks stay grey and solid from below, as real
    ones are. Altocumulus, and altostratus even more, also get a softer base,
    so a thin altostratus is a veil the sun can shine through. Cumulus keeps
    the full thickness and gets crisp edges.
  - `C23 = 0` draws every cloud like cumulus again.
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
  of the flight. You don't need to open it for the METARs; opening it shows the
  mod's settings (see [Settings](#settings)), its status and the last METAR it
  passed on.
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
- Nothing else to download: the installer brings its own Python and
  Microsoft's DirectX Shader Compiler.
- For the METAR features and the settings on the EFB tablet: the included EFB
  app (installed into your Community folder). It runs in the background in any
  aircraft and passes the sim's own METARs to the add-on.

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
2. Download the latest `AtmosphereMod-<version>.zip` from the
   [Releases page](https://github.com/tomwhoca/AtmosphereMod/releases).
   Close the sim, unzip it anywhere (not into the Community folder), and
   double-click **`Install AtmosphereMod.cmd`**. The installer:
   - finds your MSFS 2024 installation, ReShade next to it, and your
     Community folder (it shows what it found and asks before changing
     anything). If it can't find the sim, it asks for its folder: the one
     with `FlightSimulator2024.exe`, where you installed ReShade (you can
     also drag `FlightSimulator2024.exe` into the installer's window). If it
     can't find your Community folder, it asks for that too;
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
[AtmosphereMod GitHub page](https://github.com/tomwhoca/AtmosphereMod/releases)
or its flightsim.to page.

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

The main settings are in the **AtmosphereMod app on your EFB tablet**, as
switches and sliders in four tabs: Haze, Sky & light, Cloud lighting and Clouds.
Changes apply at once and are saved to `tunables.ini`. While the haze comes
from the METAR, the manual haze sliders are greyed out and show what the METAR
set. The app's "Reload tunables.ini" button does what Ctrl+Shift+F10 does.

Every setting, including the ones the app doesn't show, is in `tunables.ini`,
where each one is explained; edits there take effect with Ctrl+Shift+F10. The
ones you're most likely to touch:

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
| `C23` | cloud types: 0 = every cloud like cumulus, 1 = softer stratiform decks |
| `C24`–`C27` | thickness of stratocumulus, stratus, altocumulus and altostratus (share of `C16`) |
| `C28` | smooth, flat cloud bases (0 = the sim's puffs) |

## Uninstalling

Close the sim and double-click **`Uninstall AtmosphereMod.cmd`**. It removes the
add-on, the EFB app and the `[ATMOSPHEREMOD]` section from `ReShade.ini`, and
asks before deleting `%LOCALAPPDATA%\AtmosphereMod` (the patched shaders, logs
and your `tunables.ini`). ReShade itself stays installed.

## Known limitations

- METAR anchoring and cloud types work with live weather only. Weather presets
  and third-party weather engines keep their own cloud layers, drawn like
  cumulus.
- After a sim update, parts of the mod fall back to stock until it is
  re-patched.
- DirectX 12 only. The performance cost hasn't been measured on other systems
  yet.

## Support the mod

AtmosphereMod is free. If you like it and would like to support its
development, you can [buy me a coffee](https://buymeacoffee.com/tomwhoca).
Thank you!

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
- Bundled with the installer, unchanged and under their own licences: Python
  (Python Software Foundation) and Microsoft's DirectX Shader Compiler (LLVM
  licence).
