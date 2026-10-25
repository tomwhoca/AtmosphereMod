// METAR + aircraft state -> haze tunables.
//
// Header-only so the add-on (weather_feed.cpp) and tools/metarprobe share it.
// Input is the JSON the AtmosphereMod EFB app sends over CommBus: the sim's
// own METAR as parsed by the SDK (temp, dew, vis, phenomena, layers).
//
// The physics, in short:
//  - Surface aerosol extinction: a visibility below 10 km pins it
//    (sigma = 3.0 / V, 5 % contrast, minus Rayleigh), unless rain, snow or fog
//    is what lowers it (the sim draws those itself). Otherwise it is a dry
//    value for the air-mass type, grown with humidity:
//    f(RH) = ((1 - RH) / 0.6)^-0.5 (1 at 40 %, 2.4 at 90 %).
//  - Air-mass type from the phenomena: smoke FU, dust DU/SA/DS/SS/PO,
//    volcanic ash VA, dry haze HZ, else continental. Mist BR and humidity make
//    particles larger: whiter (lower Angstrom), more forward scattering, less
//    absorbing. Visibility below 10 km with no code at all is inferred from
//    humidity: continental when humid, grey haze around 50 %, dust and
//    pollution (yellow-brown) when dry.
//  - Boundary-layer top: ground + a depth that follows the day (0.5 km
//    inversion at night, 1.5 km in the afternoon), or the base of a low
//    FEW/SCT/BKN layer in the daytime (cumulus sit on top of the mixed layer).
//  - Rain and snow wash the air clean.

#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <iterator>
#include <regex>
#include <string>
#include <vector>

namespace haze
{
// Stock MSFS surface Mie extinction (1/km, SkyAtmosphere c3, from an in-game
// capture); T3 and T4 are multiples of it.
constexpr double kStockMieExtinction = 8.444e-3;
// MSFS Rayleigh scattering at 550 nm, sea level (1/km).
constexpr double kRayleigh550 = 12.29e-3;
// The continental g of the type table. In weather mode the add-on applies the
// derived g as an offset from this to the user's T9 (weather_feed.cpp).
constexpr double kReferenceG = 0.70;

enum class AirMass { continental, haze, smoke, dust, ash };

// A reported cloud layer: base (ft above the station), MetarCloudLayerCoverage
// (3 FEW, 4 SCT, 5 BKN, 6 OVC), MetarCloudLayerType (-1, 0 TCU, 1 CB, 2 ACC).
struct CloudLayer
{
    double base_ft = 0.0;
    int cover = 0;
    int type = -1;
    bool base_estimated = false;  // reported without a base (FEW///CB): from the dew-point spread
};

// A cumulus base from the temperature/dew-point spread (Espy): the lifted
// condensation level, ~125 m (410 ft) per degree C, for layers an automatic
// station reports without a base.
constexpr double kBaseFtPerSpreadC = 410.0;
constexpr double kEstimatedBaseMinFt = 500.0, kEstimatedBaseMaxFt = 8000.0;

struct Metar
{
    bool valid = false;
    std::string icao, raw;
    double temp_c = NAN, dew_c = NAN;
    double vis_m = NAN;           // NAN when not reported
    bool vis_open = false;        // 9999 / 10SM / CAVOK: "at least"
    bool mist = false, fog = false, precip = false, thunder = false;
    bool haze = false, smoke = false, dust = false, ash = false;
    double low_layer_ft = NAN;    // base of the lowest FEW/SCT/BKN layer below 10,000 ft
    std::vector<CloudLayer> layers;
    bool cavok = false;
    double request_lat = NAN, request_lon = NAN;  // where the EFB app asked for it (the aircraft then)
    // The sky is described: layers, CAVOK, or SKC/CLR/NSC/NCD (not with VV,
    // an obscured sky). NSC/NCD/CAVOK: nothing significant below 5,000 ft.
    bool clouds_reported = false, no_significant_cloud = false;
};

struct Inputs
{
    Metar metar;
    double ground_km = 0.0;   // terrain under the aircraft
    double local_hour = 12.0;
};

struct Tunables
{
    // T1..T6, T9, T10 of shaders/tunables.ini.
    float bl_top_km = 1.5f, bl_edge_km = 0.15f, bl_haze = 4.0f;
    float ft_haze = 0.6f, ft_scale_km = 2.5f, brown = 0.05f;
    float g = 0.7f, angstrom = 1.3f;
    double rh = NAN, sigma = NAN;
    AirMass air = AirMass::continental;  // nearest type when inferred
    bool inferred = false;               // low visibility without a code: type from humidity
};

namespace detail
{
inline std::string match(const std::string &text, const char *pattern)
{
    std::smatch m;
    return std::regex_search(text, m, std::regex(pattern)) ? m[1].str() : std::string();
}

inline double number(const std::string &text, const char *pattern)
{
    const std::string s = match(text, pattern);
    return s.empty() ? NAN : std::stod(s);
}

// Saturation vapour pressure ratio (Magnus over water); the constant cancels.
inline double es(double c)
{
    return std::exp(17.625 * c / (243.04 + c));
}

inline double smoothstep(double a, double b, double x)
{
    const double t = std::clamp((x - a) / (b - a), 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}
} // namespace detail

// Parses the EFB app's message: {"time":..., "metar":{...}} or "metar":null.
inline Metar parse_metar_json(const std::string &json)
{
    using detail::match;
    using detail::number;
    Metar m;
    if (json.find("\"metar\":{") == std::string::npos)
        return m;
    m.icao = match(json, "\"icao\":\"([^\"]*)\"");
    m.request_lat = number(json, "^\\{\"time\":\"[^\"]*\",\"lat\":(-?[0-9.]+)");
    m.request_lon = number(json, "^\\{\"time\":\"[^\"]*\",\"lat\":-?[0-9.]+,\"lon\":(-?[0-9.]+)");
    m.raw = match(json, "\"metarString\":\"([^\"]*)\"");
    m.temp_c = number(json, "\"temp\":(-?[0-9.]+)");
    m.dew_c = number(json, "\"dew\":(-?[0-9.]+)");
    // (the sim marks a value not reported with INT_MIN, -2147483648)
    if (m.temp_c < -150.0)
        m.temp_c = NAN;
    if (m.dew_c < -150.0)
        m.dew_c = NAN;
    const bool cavok = json.find("\"cavok\":true") != std::string::npos;
    m.cavok = cavok;
    double vis = number(json, "\"vis\":([0-9.]+)");
    if (number(json, "\"visUnits\":(-?[0-9]+)") == 1)
        vis *= 1609.344;  // statute miles
    if (cavok)
        vis = 10000.0;
    m.vis_m = vis;
    m.vis_open = cavok || vis >= 9999.0;

    // Phenomena: {"__Type":"Phenom", ..., "phenom":[15], ..., "ts":true, "vicinity":false}.
    const std::regex object("\\{\"__Type\":\"Phenom\"[^}]*\\}");
    for (auto it = std::sregex_iterator(json.begin(), json.end(), object); it != std::sregex_iterator(); ++it)
    {
        const std::string obj = it->str();
        if (obj.find("\"vicinity\":true") != std::string::npos)
            continue;  // VC: near, not at, the station
        if (obj.find("\"ts\":true") != std::string::npos)
            m.thunder = m.precip = true;
        const std::string list = match(obj, "\"phenom\":\\[([0-9,]*)\\]");
        for (size_t start = 0; start < list.size();)
        {
            const size_t end = std::min(list.find(',', start), list.size());
            switch (std::stoi(list.substr(start, end - start)))  // MetarPhenomenonType
            {
            case 1: m.mist = true; break;
            case 6: m.fog = true; break;
            case 7: m.smoke = true; break;
            case 10: m.haze = true; break;
            case 23: m.ash = true; break;
            case 2: case 3: case 13: case 16: case 21: m.dust = true; break;
            case 4: case 8: case 9: case 11: case 12: case 15: case 17: case 18: case 19: case 22:
                m.precip = true;
                break;
            default: break;
            }
            start = end + 1;
        }
    }

    // Layers without a base: an automatic station's FEW///CB arrives as alt 0,
    // like a real FEW000, so the raw report decides; per cover, its "///" bases.
    std::array<int, 7> unknown_bases = {};
    {
        static const char *const covers[] = { "FEW", "SCT", "BKN", "OVC" };
        const std::string padded = " " + m.raw + " ";
        for (int c = 3; c <= 6; ++c)
        {
            const std::regex unknown(std::string(" ") + covers[c - 3] + "///(CB|TCU|///)? ");
            unknown_bases[c] = static_cast<int>(
                std::distance(std::sregex_iterator(padded.begin(), padded.end(), unknown), std::sregex_iterator()));
        }
    }
    const double spread = m.temp_c - m.dew_c;
    // Lowest FEW (3), SCT (4) or BKN (5) layer: {"alt":15,"cover":3,...}, alt in hundreds of feet.
    const std::regex layer("\\{\"__Type\":\"Clouds\"[^}]*\\}");
    for (auto it = std::sregex_iterator(json.begin(), json.end(), layer); it != std::sregex_iterator(); ++it)
    {
        double alt = number(it->str(), "\"alt\":([0-9.]+)") * 100.0;
        const double cover = number(it->str(), "\"cover\":([0-9]+)");
        const double type = number(it->str(), "\"type\":(-?[0-9]+)");
        bool estimated = false;
        if (alt == 0.0 && cover >= 3 && cover <= 6 && unknown_bases[static_cast<int>(cover)] > 0)
        {
            --unknown_bases[static_cast<int>(cover)];
            if (!std::isfinite(spread))
                continue;  // no way to place it: the sim's own clouds stay
            alt = std::clamp(spread * kBaseFtPerSpreadC, kEstimatedBaseMinFt, kEstimatedBaseMaxFt);
            estimated = true;
        }
        if (cover >= 3 && cover <= 5 && alt < 10000.0 && !(alt >= m.low_layer_ft))
            m.low_layer_ft = alt;
        if (cover >= 3 && cover <= 6 && std::isfinite(alt))
            m.layers.push_back({ alt, static_cast<int>(cover), std::isfinite(type) ? static_cast<int>(type) : -1,
                                 estimated });
    }
    // the sky condition, from the raw report where the JSON has no layer for it
    const std::string raw = " " + m.raw + " ";
    auto token = [&raw](const char *t) { return raw.find(std::string(" ") + t + " ") != std::string::npos; };
    const bool obscured = std::regex_search(raw, std::regex(" VV[0-9/]{3} "));
    m.no_significant_cloud = cavok || token("NSC") || token("NCD");
    m.clouds_reported = !obscured && (!m.layers.empty() || m.no_significant_cloud || token("SKC") || token("CLR"));
    m.valid = !m.icao.empty();
    return m;
}

inline Tunables derive(const Inputs &in)
{
    const Metar &m = in.metar;
    Tunables t;

    double rh = 0.6;
    if (std::isfinite(m.temp_c) && std::isfinite(m.dew_c))
        rh = std::clamp(detail::es(m.dew_c) / detail::es(m.temp_c), 0.05, 1.0);
    const double humid = std::clamp((rh - 0.6) / 0.35, 0.0, 1.0);  // 0 below 60 %, 1 at 95 %
    const double growth = std::pow((1.0 - std::min(rh, 0.95)) / 0.6, -0.5);

    t.air = m.smoke ? AirMass::smoke : m.dust ? AirMass::dust : m.ash ? AirMass::ash
          : m.haze ? AirMass::haze : AirMass::continental;
    struct Type { double sigma_dry, angstrom, g, brown, ft_haze, ft_scale; };
    static constexpr Type kTypes[] = {
        { 0.030, 1.3, 0.70, 0.05, 0.6, 2.5 },  // continental
        { 0.080, 1.4, 0.70, 0.15, 0.8, 2.5 },  // dry haze HZ
        { 0.120, 1.8, 0.62, 0.50, 1.5, 4.0 },  // smoke FU
        { 0.100, 0.2, 0.75, 0.35, 1.5, 4.0 },  // dust DU / sand
        { 0.100, 0.4, 0.72, 0.30, 1.0, 3.0 },  // volcanic ash VA
    };
    Type type = kTypes[static_cast<int>(t.air)];

    // Visibility below 10 km with no code for it (no HZ/FU/DU/VA, no
    // precipitation, fog or mist): infer the particles from humidity. Humid
    // air stays continental (the humidity whitening below applies); drier
    // air is grey haze, and dry air is dust and pollution (yellow-brown).
    // d: 0 at 75 % RH, 0.5 at 50 % (haze), 1 at 25 % (dust).
    const bool coded = m.haze || m.smoke || m.dust || m.ash || m.mist || m.fog || m.precip;
    t.inferred = std::isfinite(m.vis_m) && !m.vis_open && !coded;
    if (t.inferred)
    {
        const auto lerp = [](const Type &a, const Type &b, double f) {
            const auto mix = [f](double x, double y) { return x + (y - x) * f; };
            return Type{ mix(a.sigma_dry, b.sigma_dry), mix(a.angstrom, b.angstrom), mix(a.g, b.g),
                         mix(a.brown, b.brown), mix(a.ft_haze, b.ft_haze), mix(a.ft_scale, b.ft_scale) };
        };
        const double d = std::clamp((0.75 - rh) / 0.5, 0.0, 1.0);
        const Type &continental = kTypes[static_cast<int>(AirMass::continental)];
        const Type &hz = kTypes[static_cast<int>(AirMass::haze)];
        const Type &dust = kTypes[static_cast<int>(AirMass::dust)];
        type = d < 0.5 ? lerp(continental, hz, d / 0.5) : lerp(hz, dust, (d - 0.5) / 0.5);
        t.air = d < 0.25 ? AirMass::continental : d < 0.75 ? AirMass::haze : AirMass::dust;
    }
    const bool clean_type = t.air == AirMass::continental || t.air == AirMass::haze;

    // Surface aerosol extinction (1/km at 550 nm).
    double sigma = type.sigma_dry * growth;
    if (std::isfinite(m.vis_m) && !m.vis_open && !m.precip && !m.fog)
        sigma = 3.0 / (m.vis_m * 0.001) - kRayleigh550;
    if (m.precip && clean_type)
        sigma = std::min(sigma, 0.015 * growth);  // washout
    sigma = std::clamp(sigma, 0.005, 1.0);  // thicker than ~3 km visibility is fog or cloud: the sim's
    t.sigma = sigma;
    t.rh = rh;

    t.ft_haze = static_cast<float>(type.ft_haze);
    t.ft_scale_km = static_cast<float>(type.ft_scale);
    t.bl_haze = static_cast<float>(std::max(sigma / kStockMieExtinction - type.ft_haze, 0.0));

    // Humidity makes particles larger: whiter, more forward scattering, less absorbing.
    const double whiten = clean_type ? humid : 0.5 * humid;
    double angstrom = type.angstrom + (0.3 - type.angstrom) * whiten;
    // +0.05 at most: +0.08 made the glare toward a low sun too strong (EDDL,
    // 88 %, 2026-10-05; without our aerosol the glow went away).
    double g = type.g + 0.05 * humid;
    if (m.mist)
    {
        angstrom = std::min(angstrom, 0.4);
        g = std::max(g, 0.8);
    }
    t.angstrom = static_cast<float>(angstrom);
    t.g = static_cast<float>(g);
    t.brown = static_cast<float>(type.brown * (1.0 - 0.6 * humid));

    // Boundary layer: 0.5 km at night and in the morning, 1.5 km in the afternoon.
    const double h = in.local_hour;
    const double day = detail::smoothstep(8.0, 13.0, h) * (1.0 - detail::smoothstep(17.0, 21.0, h));
    double depth = 0.5 + 1.0 * day;
    if (std::isfinite(m.low_layer_ft) && day > 0.3)
        depth = std::clamp(m.low_layer_ft * 0.0003048, 0.3, 3.0);  // cumulus base caps the mixed layer
    if (m.fog || m.mist)
        depth = std::min(depth, 0.5);
    if (m.precip)
        depth = std::max(depth, 1.0);
    t.bl_top_km = static_cast<float>(in.ground_km + depth);
    t.bl_edge_km = static_cast<float>(0.10 + 0.10 * day);
    return t;
}

inline const char *air_mass_name(AirMass a)
{
    static const char *const names[] = { "continental", "haze HZ", "smoke FU", "dust", "volcanic ash" };
    return names[static_cast<int>(a)];
}

// One line for logs: inputs and the tunables they give.
inline std::string describe(const Inputs &in, const Tunables &t)
{
    char buf[512];
    snprintf(buf, sizeof(buf),
             "%s RH %.0f%% %s%s | sigma %.3f/km (vis ~%.0f km) | T1 %.2f T2 %.2f T3 %.1f T4 %.1f T5 %.1f "
             "T6 %.2f T9 %.2f T10 %.2f | ground %.2f km, %.1f h",
             in.metar.icao.c_str(), 100.0 * t.rh, t.inferred ? "inferred ~" : "", air_mass_name(t.air), t.sigma,
             3.0 / (t.sigma + kRayleigh550),
             t.bl_top_km, t.bl_edge_km, t.bl_haze, t.ft_haze, t.ft_scale_km, t.brown, t.g, t.angstrom,
             in.ground_km, in.local_hour);
    return buf;
}
} // namespace haze
