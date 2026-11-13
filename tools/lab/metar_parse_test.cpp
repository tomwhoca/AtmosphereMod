// The METAR parser (addon/src/haze_mapping.h parse_metar_json) and the cloud
// anchoring built from it (cloud_anchor.h): cloud layers an automatic station
// reports without a base (FEW///CB). The sim sends those with alt 0, like a
// real FEW000; their base comes from the dew-point spread instead.
// Run by tools/test_metar_parse.py; exits 1 on a failure.

#include "cloud_anchor.h"
#include "cloud_stations.h"
#include "haze_mapping.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

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
    {
        // Report times dated by the sim's clock (issue #39: a station's month-old report looked newest).
        const auto day = [](int y, int mo, int d) {
            return int64_t(std::chrono::sys_days{std::chrono::year{y} / mo / d}.time_since_epoch().count()) * 1440;
        };
        const int64_t nov10 = haze::clock_minutes(2026, 314, 69307);  // (weather.log: UTC day 314 69307 s)
        check(nov10 == day(2026, 11, 10) + 19 * 60 + 15, "clock: day 314 of 2026, 69307 s is 10 Nov 19:15");
        check(haze::clock_minutes(2028, 60, 0) == day(2028, 2, 29), "clock: day 60 of 2028 is 29 Feb");
        check(haze::clock_minutes(0, 0, 0) == -1 && haze::clock_minutes(2026, 314, -5) == -1, "clock: nonsense is -1");
        const int64_t now = day(2026, 11, 11) + 1 * 60 + 42;  // KLAX, 11 Nov 01:42Z
        const auto at = [](int dd, int hh, int mm) { return dd * 1440 + hh * 60 + mm; };
        check(haze::report_time(at(11, 0, 53), now) == day(2026, 11, 11) + 53, "report: 110053Z is today's");
        check(haze::report_time(at(10, 18, 53), now) == day(2026, 11, 10) + 18 * 60 + 53, "report: yesterday's");
        check(haze::report_time(at(25, 13, 0), now) == day(2026, 10, 25) + 13 * 60,
              "report: 251300Z on the 11th is October's, not two weeks ahead");
        check(haze::report_time(at(11, 2, 30), now) == day(2026, 11, 11) + 2 * 60 + 30,
              "report: 48 min after the clock is still today's (a clock a little behind)");
        check(haze::report_time(at(11, 2, 50), now) == day(2026, 10, 11) + 2 * 60 + 50,
              "report: more than an hour after the clock is last month's");
        const int64_t dec1 = day(2026, 12, 1) + 50;  // 1 Dec 00:50Z
        check(haze::report_time(at(30, 23, 0), dec1) == day(2026, 11, 30) + 23 * 60,
              "report: across a month's end, the 30th's is 1 h 50 min old, not newer");
        check(haze::report_time(at(31, 23, 0), dec1) == day(2026, 10, 31) + 23 * 60,
              "report: a 31st on 1 Dec (November has 30) is October's");
        check(haze::report_time(at(29, 12, 0), day(2028, 3, 1)) == day(2028, 2, 29) + 12 * 60,
              "report: 29 Feb in a leap year");
        check(haze::report_time(-1, now) == -1 && haze::report_time(at(11, 0, 53), -1) == -1,
              "report: without a time or a clock, -1");
        // The stale check as the feed makes it: the newest report is the latest dated one, and October's
        // 25th doesn't become it.
        const int64_t current = haze::report_time(at(11, 0, 53), now), old = haze::report_time(at(25, 13, 0), now);
        const int64_t newest = std::max(current, old);
        check(newest == current && newest - old > haze::kStaleReportMin && newest - current == 0,
              "stale: October's report is stale, today's isn't");
        // Historical weather (the sim at 12 Nov 15:01; 2026-11-13 at LOWI): the sim's own reports are current,
        // today's first ones are last month's, and more than 20 days older than the clock: stale outright.
        const int64_t hist = day(2026, 11, 12) + 15 * 60 + 1;
        const int64_t own = haze::report_time(at(12, 14, 50), hist), live = haze::report_time(at(13, 12, 50), hist);
        check(own == day(2026, 11, 12) + 14 * 60 + 50, "historical: the sim's 121450Z is its own day's");
        check(live == day(2026, 10, 13) + 12 * 60 + 50 && hist - live > haze::kReportMaxAgeMin,
              "historical: today's 131250Z is last month's: stale whatever comes first");
        check(hist - own <= haze::kStaleReportMin, "historical: the sim's own report is current");
        // And back to live weather (the clock now 13 Nov 13:40): yesterday's reports that still come in are
        // yesterday's, hours older than today's newest.
        const int64_t back = day(2026, 11, 13) + 13 * 60 + 40;
        const int64_t late = haze::report_time(at(12, 14, 50), back), fresh = haze::report_time(at(13, 13, 20), back);
        check(late == day(2026, 11, 12) + 14 * 60 + 50 && fresh - late > haze::kStaleReportMin &&
                  back - fresh <= haze::kStaleReportMin,
              "back to live: yesterday's 121450Z is stale against today's 131320Z, which is current");    }
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

    // The in-cloud fog's floor (cloud_stations.h fog_floor_agl_m): where the report puts the bottom of its clouds
    {
        const auto floor_of = [](const std::string &raw, const std::string &layers) {
            return anchor::fog_floor_agl_m(haze::parse_metar_json(message(raw, layers, "8", "7")));
        };
        const auto near = [](double a, double b) { return std::abs(a - b) < 1e-6; };
        // EDDF 111320Z: the report behind issue (fly-through fog on the ground under OVC004 with 2,900 m of mist)
        check(near(floor_of("METAR EDDF 111320Z AUTO 28004KT 240V320 2900 -RADZ BR OVC004 08/07 Q1002 NOSIG",
                            layer(4, 6, -1)), 400.0 * 0.3048),
              "EDDF OVC004: the floor 400 ft above the station");
        check(near(floor_of("METAR LIPE 111520Z VRB02KT 1600 -RA BR BKN004 BKN010 11/11 Q1007",
                            layer(10, 5, -1) + "," + layer(4, 5, -1)), 400.0 * 0.3048),
              "LIPE BKN004 BKN010: the lowest layer, whatever the order");
        const haze::Metar vv = haze::parse_metar_json(message("METAR EDDF 111520Z AUTO VRB02KT 0200 FG VV002 08/08 Q1001",
                                                              "", "8", "8"));
        check(vv.obscured && vv.vertical_vis_ft == 200.0 && !vv.clouds_reported && near(anchor::fog_floor_agl_m(vv), 200.0 * 0.3048),
              "VV002: obscured, the floor at the vertical visibility (200 ft)");
        check(near(floor_of("METAR EDDF 111520Z AUTO VRB02KT 0100 FG VV/// 08/08 Q1001", ""), 0.0),
              "VV///: the floor at the ground");
        check(near(floor_of("METAR EDDM 091050Z 09004KT 9999 NSC 07/05 Q1019", ""), 5000.0 * 0.3048),
              "NSC: no cloud below 5,000 ft");
        const haze::Metar cb = haze::parse_metar_json(
            message("METAR EDDK 251150Z AUTO 26019KT 9999 FEW///CB 14/06 Q1000", layer(0, 3, 1), "14", "6"));
        check(near(anchor::fog_floor_agl_m(cb), 3280.0 * 0.3048), "only an estimated base (FEW///CB): that one");
        check(std::isnan(floor_of("METAR EDDK 251150Z AUTO 26019KT 9999 14/06 Q1000", "")),
              "no sky condition: no floor");
    }

    std::printf("%s\n", g_failures ? "FAILED" : "all checks passed");
    return g_failures ? 1 : 0;
}
