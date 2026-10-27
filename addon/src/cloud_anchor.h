// METAR anchoring of the clouds: a station's reported
// layers turned into what the patched ch_cloudDensity blends into MSFS's live
// weather map near the station: one row per weather-map slice
// (tools/patch_dxil.py apply_metar_anchor; the stations: cloud_stations.h).
//
// A METAR gives each layer's cover and base only, so the kind and depth are
// inferred from height, cover, the temperature/dew-point spread and the time
// of day; the cover becomes the weather-map value C that gives that much of
// the sky covered (calibrated against the sim's own shape noise:
// 1/8 0.17 ... 4/8 0.42, 6/8 0.55, 7/8 0.68, 8/8 1.0).
// Above what the METAR can describe (12,000 ft above the station, 5,000 ft
// with CAVOK/NSC, or the top of an overcast) MSFS's own map is kept.

#pragma once

#include "haze_mapping.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace anchor
{
constexpr double kFt = 0.3048;
constexpr double kObservedFt = 12000.0;  // what a METAR describes above the station (AUTO: 12,000 ft)
constexpr double kNscFt = 5000.0;        // CAVOK / NSC: no cloud below 5,000 ft
// A CB/TCU layer: its base part at the reported cover, its towers above as
// isolated cells, ~2 oktas (coverage calibration: 2/8 0.26), and only up to
// what the METAR can see: a 0.3-10 km block at the reported C (BKN010CB at
// EKCH) replaced MSFS's clouds through the cruise levels.
constexpr double kConvectiveBaseM = 1000.0;
constexpr float kTowerValue = 0.26f;

// The generator's weather-map slices: slice k spans 15.625 k^2 .. 15.625 (k + 1)^2 m
// MSL; it reads 31 of the 32 rows (patch_dxil.py WEATHER_SLICES, ANCHOR_ROWS).
constexpr size_t kSlices = 32;
constexpr int kWeatherSlices = 31;
// A deck's top slice (if it has more than one) at most this C and from MSFS's
// cellular channel: ~75 % covered over an overcast, so the top has
// lumps and dimples one slice high instead of being a plane at a slice boundary
// (patch_dxil.py DECK_TOP_C).
constexpr float kDeckTopC = 0.91f;

// Weather-map value for a METAR cover (MetarCloudLayerCoverage: 3 FEW .. 6 OVC).
inline double cover_value(int cover)
{
    switch (cover)
    {
    case 3: return 0.21;  // FEW, 1-2 oktas
    case 4: return 0.38;  // SCT, 3-4
    case 5: return 0.58;  // BKN, 5-7
    case 6: return 1.0;   // OVC
    default: return 0.0;
    }
}

// MSFS domes cumuliform and convective layers; stratiform ones become
// flat-topped decks, smooth ones (stratus, altostratus) also from MSFS's
// smooth noise channel instead of its cellular ones (apply_metar_anchor).
enum Kind : uint32_t { kCumuliform = 0, kStratiform = 1, kConvective = 2, kSmoothDeck = 3 };

struct Layer
{
    float base_m = 0.0f, top_m = 0.0f;  // MSL
    float value = 0.0f;                 // weather-map C
    Kind kind = kCumuliform;
    std::string name;                   // for the log
};

// One slice's row: C, how much the station describes the slice (a: 1 below
// what the METAR can see, 0 above), how flat-topped (deck) and how far toward
// the smooth noise channel (smooth).
using Row = std::array<float, 4>;
using Rows = std::array<Row, kSlices>;

// The layer covers at least half of slice k (or of itself, if thinner).
inline bool slice_in(const Layer &l, int k)
{
    if (k < 0 || k >= kWeatherSlices)
        return false;
    const double lo = 15.625 * k * k, hi = 15.625 * (k + 1) * (k + 1);
    const double overlap = std::max(0.0, std::min<double>(l.top_m, hi) - std::max<double>(l.base_m, lo));
    return overlap / std::min(std::max<double>(l.top_m - l.base_m, 1.0), hi - lo) >= 0.5;
}

// The storm cells' evidence (patch_dxil.py apply_cb_towers): how clearly the
// METAR shows a thunderstorm near the station, in the row the generator's
// anchoring doesn't read (the last), as (evidence, 1, 0, 0) so it crossfades
// with the METAR. Thunder at the station, in its vicinity, a CB or TCU layer;
// a CB or TCU layer by its cover (storm_cover): FEW CB 0.5, SCT 0.6, BKN/OVC
// 0.7 (FEW CB is one or two cells around the station, not a field of them).
// The generator lifts thunder's cells (above kStormCb) toward the tropopause.
constexpr size_t kStormRow = kSlices - 1;
constexpr float kStormThunder = 1.0f, kStormVicinity = 0.8f, kStormCb = 0.7f, kStormTcu = 0.35f;

// A CB/TCU layer's evidence scale by its cover (MetarCloudLayerCoverage: 3 FEW .. 6 OVC).
inline float storm_cover(int cover)
{
    return cover <= 3 ? 5.0f / 7.0f : cover == 4 ? 6.0f / 7.0f : 1.0f;
}
static_assert(kStormRow >= static_cast<size_t>(kWeatherSlices));

struct Anchor
{
    bool valid = false;
    double lat_deg = 0.0, lon_deg = 0.0, elevation_m = 0.0;
    float ceiling_m = 0.0f;  // MSL: above it MSFS's own map is kept
    std::vector<Layer> layers;
    std::string station;
    float storm = 0.0f;  // the storm cells' evidence (kStormRow), also without a cloud group

    // Each slice takes the largest C of the layers in it, 0 between layers
    // below the ceiling; a deck's top slice is capped at kDeckTopC.
    Rows rows() const
    {
        Rows out = {};
        out[kStormRow] = { storm, 1.0f, 0.0f, 0.0f };
        if (!valid)
            return out;
        for (int k = 0; k < kWeatherSlices; ++k)  // (row kStormRow: the storm evidence, above)
        {
            const double centre = 15.625 * (k + 0.5) * (k + 0.5);
            float best = 0.0f, deck = 0.0f, smooth = 0.0f;
            bool covered = false;
            for (const Layer &l : layers)
            {
                if (!slice_in(l, k))
                    continue;
                covered = true;
                const bool flat = l.kind == kStratiform || l.kind == kSmoothDeck;
                const bool top = flat && !slice_in(l, k + 1) && slice_in(l, k - 1);
                best = std::max(best, top ? std::min(l.value, kDeckTopC) : l.value);
                deck = std::max(deck, flat ? 1.0f : 0.0f);
                smooth = std::max(smooth, l.kind == kSmoothDeck && !top ? 1.0f : 0.0f);
            }
            const bool described = covered || (centre < ceiling_m && k < kWeatherSlices);
            out[k] = { best, described ? 1.0f : 0.0f, deck, smooth };
        }
        return out;
    }

    std::string describe() const
    {
        char line[256];
        snprintf(line, sizeof(line), "cloud anchor %s (%.3f, %.3f, %.0f m): ", station.c_str(), lat_deg, lon_deg,
                 elevation_m);
        std::string out = line;
        if (layers.empty())
            out += "clear";
        for (size_t j = 0; j < layers.size(); ++j)
        {
            const Layer &l = layers[j];
            static const char *const kKinds[] = { "domed", "flat deck", "convective", "smooth flat deck" };
            snprintf(line, sizeof(line), "%s%s %.0f-%.0f m C %.2f (%s)", j ? ", " : "", l.name.c_str(), l.base_m,
                     l.top_m, l.value, kKinds[l.kind & 3]);
            out += line;
        }
        snprintf(line, sizeof(line), "; MSFS's own above %.0f m MSL", ceiling_m);
        out += line;
        if (storm > 0.0f)
        {
            snprintf(line, sizeof(line), "; storm evidence %.2f", storm);
            out += line;
        }
        return out;
    }
};

// local_hour: for the daytime depth of convective cloud.
inline Anchor from_metar(const haze::Metar &m, double lat_deg, double lon_deg, double elevation_m, double local_hour)
{
    Anchor a;
    a.station = m.icao;
    a.lat_deg = lat_deg;
    a.lon_deg = lon_deg;
    a.elevation_m = elevation_m;
    if (m.valid)
    {
        float layer = 0.0f;  // the most covering CB / TCU layer's
        for (const haze::CloudLayer &cl : m.layers)
            if ((cl.type == 1 || cl.type == 0) && cl.cover >= 3)
                layer = std::max(layer, (cl.type == 1 ? kStormCb : kStormTcu) * storm_cover(cl.cover));
        a.storm = m.thunder ? kStormThunder : m.thunder_vicinity ? kStormVicinity : layer;
    }
    if (!m.valid || !m.clouds_reported)
        return a;  // no cloud group: nothing to anchor to
    const double spread = std::isfinite(m.temp_c) && std::isfinite(m.dew_c) ? m.temp_c - m.dew_c : 5.0;
    const bool daytime = local_hour >= 9.0 && local_hour <= 19.0;
    double ceiling_ft = m.no_significant_cloud ? kNscFt : kObservedFt;
    std::vector<haze::CloudLayer> layers = m.layers;
    std::sort(layers.begin(), layers.end(), [](const auto &x, const auto &y) { return x.base_ft < y.base_ft; });
    for (const haze::CloudLayer &cl : layers)
    {
        if (cl.cover < 3 || cl.cover > 6)
            continue;
        const double base_agl = cl.base_ft * kFt;
        const char *const note = cl.base_estimated ? " (base estimated)" : "";
        double depth;
        Layer l;
        l.value = static_cast<float>(cover_value(cl.cover));
        static const char *const kCover[] = { "", "", "", "FEW", "SCT", "BKN", "OVC" };
        if (cl.type >= 0 && cl.type <= 2)  // CB, TCU, ACC: MSFS's domes; the ceiling isn't raised for their tops
        {
            depth = cl.type == 1 ? std::max(4000.0, 10000.0 - base_agl) : (cl.type == 0 ? 3500.0 : 800.0);
            l.kind = kConvective;
            l.name = std::string(kCover[cl.cover]) +
                     (cl.type == 1 ? "CB cumulonimbus" : cl.type == 0 ? "TCU towering cumulus" : "ACC altocumulus") + note;
            const double base_part = std::min(depth, kConvectiveBaseM);
            l.base_m = static_cast<float>(elevation_m + base_agl);
            l.top_m = static_cast<float>(elevation_m + base_agl + base_part);
            a.layers.push_back(l);
            if (depth > base_part)
            {
                Layer towers = l;
                towers.base_m = l.top_m;
                towers.top_m = static_cast<float>(elevation_m + base_agl + depth);
                towers.value = std::min(l.value, kTowerValue);
                towers.name = std::string(cl.type == 1 ? "CB" : "TCU") + " towers";
                a.layers.push_back(towers);
            }
            if (cl.cover == 6)  // nothing above an overcast is observed
            {
                ceiling_ft = std::min(ceiling_ft, (base_agl + base_part) / kFt);
                break;
            }
            continue;
        }
        else if (base_agl < 600.0)
        {
            const bool deck = cl.cover >= 5;
            depth = deck ? 450.0 : 250.0;
            l.kind = deck ? kSmoothDeck : kStratiform;
            l.name = std::string(kCover[cl.cover]) + (deck ? " stratus" : " stratus fractus");
        }
        else if (base_agl < 2500.0)
        {
            // fair-weather cumulus by day with some spread; a stratocumulus deck when overcast or moist
            const bool deck = cl.cover == 6 || (cl.cover == 5 && spread < 3.0);
            l.kind = deck ? kStratiform : kCumuliform;
            depth = deck ? 600.0 : (daytime ? 1200.0 : 700.0);
            l.name = std::string(kCover[cl.cover]) + (deck ? " stratocumulus" : " cumulus");
        }
        else if (base_agl < 6000.0)
        {
            depth = cl.cover == 6 ? 1000.0 : 700.0;
            l.kind = cl.cover >= 5 ? kSmoothDeck : kStratiform;
            l.name = std::string(kCover[cl.cover]) + (cl.cover >= 5 ? " altostratus" : " altocumulus");
        }
        else
        {
            depth = 900.0;
            l.kind = kStratiform;
            l.name = std::string(kCover[cl.cover]) + " cirrus";
        }
        l.name += note;
        l.base_m = static_cast<float>(elevation_m + base_agl);
        l.top_m = static_cast<float>(elevation_m + base_agl + depth);
        ceiling_ft = std::max(ceiling_ft, (base_agl + depth) / kFt);
        if (cl.cover == 6)  // nothing above an overcast is observed
            ceiling_ft = std::min(ceiling_ft, (base_agl + depth) / kFt);
        a.layers.push_back(l);
        if (cl.cover == 6)
            break;
    }
    a.ceiling_m = static_cast<float>(elevation_m + ceiling_ft * kFt);
    // convective tops above what the METAR sees: MSFS's own
    for (Layer &l : a.layers)
        if (l.kind == kConvective)
            l.top_m = std::min(l.top_m, a.ceiling_m);
    std::erase_if(a.layers, [](const Layer &l) { return l.top_m <= l.base_m; });
    a.valid = true;
    return a;
}
} // namespace anchor
