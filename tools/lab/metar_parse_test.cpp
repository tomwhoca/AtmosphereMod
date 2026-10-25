// The METAR parser (addon/src/haze_mapping.h parse_metar_json) and the cloud
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

    std::printf("%s\n", g_failures ? "FAILED" : "all checks passed");
    return g_failures ? 1 : 0;
}
