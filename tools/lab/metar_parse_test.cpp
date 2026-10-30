// The METAR parsers (addon/src/haze_mapping.h parse_metar_json, parse_metar_raw) and the cloud
// anchoring built from it (cloud_anchor.h): cloud layers an automatic station
// reports without a base (FEW///CB). The sim sends those with alt 0, like a
// real FEW000; their base comes from the dew-point spread instead.
// Run by tools/test_metar_parse.py; exits 1 on a failure.

#include "cloud_anchor.h"
#include "haze_mapping.h"

#include <cmath>
#include <cstdio>
#include <string>

namespace
{
int g_failures = 0;

void check(bool ok, const std::string &what)
{
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what.c_str());
    g_failures += !ok;
}

// The EFB app's message, shaped as the sim's Metar object (build/logs/metar_json.log).
std::string message(const std::string &raw, const std::string &layers, const std::string &temp, const std::string &dew)
{
    return "{\"time\":\"2026-10-25T11:50:00.000Z\",\"lat\":50.86,\"lon\":7.14,\"ident\":null,\"metar\":{"
           "\"__Type\":\"Metar\",\"cavok\":false,\"dew\":" + dew + ",\"icao\":\"EDDK\",\"layers\":[" + layers +
           "],\"metarString\":\"" + raw + "\",\"phenomena\":[],\"temp\":" + temp +
           ",\"vis\":9999,\"visUnits\":0,\"flightCategory\":0}}";
}

std::string layer(int alt, int cover, int type)
{
    return "{\"__Type\":\"Clouds\",\"alt\":" + std::to_string(alt) + ",\"cover\":" + std::to_string(cover) +
           ",\"tempo\":false,\"type\":" + std::to_string(type) + "}";
}
} // namespace

int main()
{
    // EDDK 251150Z: FEW041 and a CB without a base; 14/06, spread 8 C -> 8 * 410 = 3280 ft
    {
        const haze::Metar m = haze::parse_metar_json(message(
            "METAR EDDK 251150Z AUTO 26019G30KT 9999 VCSH FEW041 FEW///CB 14/06 Q1000 RESHRA TEMPO SHRA",
            layer(41, 3, -1) + "," + layer(0, 3, 1), "14", "6"));
        check(m.layers.size() == 2, "EDDK: two layers");
        check(m.layers.size() == 2 && m.layers[0].base_ft == 4100.0 && !m.layers[0].base_estimated,
              "EDDK: FEW041 as reported");
        check(m.layers.size() == 2 && m.layers[1].base_ft == 3280.0 && m.layers[1].base_estimated &&
                  m.layers[1].type == 1,
              "EDDK: FEW///CB at 3280 ft, estimated");
        check(m.low_layer_ft == 3280.0, "EDDK: the haze's lowest layer is the estimated base, not 0");
        check(m.report_min == 25 * 1440 + 11 * 60 + 50, "EDDK: report time 251150Z");
        const anchor::Anchor a = anchor::from_metar(m, 50.866, 7.143, 77.0, 14.0);
        bool cb_ok = false;
        for (const anchor::Layer &l : a.layers)
        {
            std::printf("     anchored: %s %.0f-%.0f m\n", l.name.c_str(), l.base_m, l.top_m);
            if (l.name.find("CB cumulonimbus (base estimated)") != std::string::npos)
                cb_ok = std::abs(l.base_m - (77.0 + 3280.0 * 0.3048)) < 1.0;
        }
        check(cb_ok, "EDDK: the CB anchored from ~1077 m MSL, named as estimated");
        bool none_low = true;
        for (const anchor::Layer &l : a.layers)
            none_low = none_low && l.base_m > 500.0f;
        check(none_low, "EDDK: no anchored cloud from the ground");
    }
    // a real surface base (FEW000) stays at 0
    {
        const haze::Metar m = haze::parse_metar_json(
            message("METAR EDDK 250550Z 00000KT 0800 FG FEW000 BKN010 05/05 Q1020", layer(0, 3, -1) + "," + layer(10, 5, -1),
                    "5", "5"));
        check(m.layers.size() == 2 && m.layers[0].base_ft == 0.0 && !m.layers[0].base_estimated,
              "FEW000: a real surface base kept at 0");
    }
    // without a base and without a temperature: the layer is left out
    {
        const haze::Metar m = haze::parse_metar_json(
            message("METAR EDDK 251150Z AUTO 26019KT 9999 SCT///TCU BKN045 /////// Q1000", layer(0, 4, 0) + "," + layer(45, 5, -1),
                    "-2147483648", "-2147483648"));
        check(m.layers.size() == 1 && m.layers[0].base_ft == 4500.0, "SCT///TCU without a temperature: left out");
    }
    // the estimate's limits: saturated air (spread 0) -> 500 ft; BKN/// without a type too
    {
        const haze::Metar m = haze::parse_metar_json(
            message("METAR EDDK 251150Z AUTO 20005KT 4000 -RA BKN/// 12/12 Q1005", layer(0, 5, -1), "12", "12"));
        check(m.layers.size() == 1 && m.layers[0].base_ft == 500.0 && m.layers[0].base_estimated,
              "BKN/// at spread 0: 500 ft, the estimate's floor");
    }
    // two layers of the same cover, one without a base, in either order
    {
        const haze::Metar m = haze::parse_metar_json(
            message("METAR EDDK 251150Z AUTO 26019KT 9999 FEW///CB FEW041 16/06 Q1000", layer(0, 3, 1) + "," + layer(41, 3, -1),
                    "16", "6"));
        check(m.layers.size() == 2 && m.layers[0].base_ft == 4100.0 && m.layers[0].base_estimated &&
                  m.layers[1].base_ft == 4100.0 && !m.layers[1].base_estimated,
              "FEW///CB before FEW041: the CB estimated (spread 10 -> 4100 ft), FEW041 as reported");
    }

    // Active Sky's reports as text (parse_metar_raw) read as the sim's JSON of the same report does
    {
        const std::string raw =
            "METAR EDDK 251150Z AUTO 26019G30KT 9999 VCSH FEW041 FEW///CB 14/06 Q1000 RESHRA TEMPO SHRA";
        const haze::Metar j = haze::parse_metar_json(message(raw, layer(41, 3, -1) + "," + layer(0, 3, 1), "14", "6"));
        const haze::Metar r = haze::parse_metar_raw(raw);
        bool same_layers = r.layers.size() == j.layers.size();
        for (size_t i = 0; same_layers && i < r.layers.size(); ++i)
            same_layers = r.layers[i].base_ft == j.layers[i].base_ft && r.layers[i].cover == j.layers[i].cover &&
                          r.layers[i].type == j.layers[i].type &&
                          r.layers[i].base_estimated == j.layers[i].base_estimated;
        check(r.valid && r.icao == "EDDK" && r.report_min == j.report_min, "raw EDDK: station and report time");
        check(same_layers && r.low_layer_ft == j.low_layer_ft, "raw EDDK: layers as from the JSON");
        check(r.temp_c == 14.0 && r.dew_c == 6.0 && r.vis_m == 9999.0 && r.vis_open, "raw EDDK: 14/06, 9999");
        check(!r.precip && !r.thunder && r.clouds_reported, "raw EDDK: VCSH, RESHRA and TEMPO SHRA not at the station");
    }
    {
        const haze::Metar m = haze::parse_metar_raw("EGLL 291550Z COR AUTO 05006KT 010V080 9999 OVC022 06/01 Q1028 NOSIG");
        check(m.valid && m.icao == "EGLL" && m.layers.size() == 1 && m.layers[0].cover == 6 &&
                  m.layers[0].base_ft == 2200.0 && std::isnan(m.low_layer_ft),
              "raw EGLL (Active Sky): OVC022, no FEW-BKN layer for the haze");
    }
    {
        const haze::Metar m = haze::parse_metar_raw(
            "KSNA 040808Z 00000KT 10SM 22/17 A2988 RMK AS DEPICTED\r\n(Cloned by: KSNA, closest weather station)");
        check(m.valid && m.icao == "KSNA" && m.raw == "KSNA 040808Z 00000KT 10SM 22/17 A2988 RMK AS DEPICTED",
              "raw KSNA: the clone note cut off");
        check(std::abs(m.vis_m - 16093.44) < 0.01 && m.vis_open && m.temp_c == 22.0 && m.dew_c == 17.0 &&
                  m.layers.empty() && !m.clouds_reported,
              "raw KSNA: 10SM, 22/17, no sky condition");
    }
    {
        const haze::Metar m = haze::parse_metar_raw("KJFK 291551Z 18012KT 1 1/2SM R04R/5000VP6000FT -TSRA BR "
                                                    "BKN008 OVC015CB M02/M04 A2992 RMK AO2");
        check(std::abs(m.vis_m - 1.5 * 1609.344) < 0.01 && !m.vis_open, "raw KJFK: 1 1/2SM");
        check(m.thunder && m.precip && m.mist && !m.fog, "raw KJFK: -TSRA BR");
        check(m.temp_c == -2.0 && m.dew_c == -4.0, "raw KJFK: M02/M04");
        check(m.layers.size() == 2 && m.layers[1].type == 1 && m.low_layer_ft == 800.0, "raw KJFK: BKN008 OVC015CB");
    }
    {
        const haze::Metar m = haze::parse_metar_raw("LIRF 291550Z 22008KT CAVOK 18/09 Q1021 NOSIG");
        check(m.cavok && m.vis_m == 10000.0 && m.vis_open && m.no_significant_cloud && m.clouds_reported,
              "raw LIRF: CAVOK");
    }
    {
        const haze::Metar m = haze::parse_metar_raw("OMDB 291550Z 31010KT 2000 DU HZ VCTS VV/// 35/12 Q1008");
        check(m.vis_m == 2000.0 && m.dust && m.haze && m.thunder_vicinity && !m.thunder && !m.clouds_reported,
              "raw OMDB: DU HZ, VCTS near, VV obscured");
    }
    {
        const haze::Metar m = haze::parse_metar_raw("KDEN 291553Z P6SM M1/4SM FG");
        check(m.vis_open && std::abs(m.vis_m - 6 * 1609.344) < 0.01 && m.fog,
              "raw: P6SM is at least 6 SM; a second visibility ignored");
        check(!haze::parse_metar_raw("ICAO not recognized").valid && !haze::parse_metar_raw("EDDK not available").valid,
              "raw: an answer without a report time (Active Sky's unknown station) isn't a METAR");
        const haze::Metar bom = haze::parse_metar_raw("\xEF\xBB\xBF \r\nEGLL 291450Z 1/0SM OVC022 08/06\r\n(Cloned by: x)");
        check(bom.valid && bom.icao == "EGLL" && bom.raw.rfind("EGLL", 0) == 0 && bom.layers.size() == 1 &&
                  std::isnan(bom.vis_m),
              "raw: a byte order mark and leading blank lines are skipped, a zero denominator unreported");
    }

    std::printf("%s\n", g_failures ? "FAILED" : "all checks passed");
    return g_failures ? 1 : 0;
}
