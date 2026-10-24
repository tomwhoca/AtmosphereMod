// Checks the METAR anchoring's station set (addon/src/cloud_stations.h) and
// its rows (cloud_anchor.h); tools/test_cloud_stations.py builds and runs it.
//   cloud_stations_test --rows   the rows of test_cloud_gen_capture.py's three
//                                stations, for comparison with its reference
//   cloud_stations_test          fades, crossfades, waiting for positions,
//                                drops, making room and CB layers over simulated time

#include "cloud_stations.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace
{
int g_fails = 0;

void check(bool ok, const std::string &what)
{
    if (!ok)
    {
        ++g_fails;
        printf("FAIL: %s\n", what.c_str());
    }
}

anchor::Anchor layers_anchor(std::vector<anchor::Layer> layers, float ceiling)
{
    anchor::Anchor a;
    a.valid = true;
    a.layers = std::move(layers);
    a.ceiling_m = ceiling;
    return a;
}

int rows()
{
    using anchor::Layer;
    const anchor::Anchor stations[] = {
        layers_anchor({ { 158.0f, 658.0f, 0.58f, anchor::kSmoothDeck }, { 1000.0f, 2200.0f, 0.38f, anchor::kCumuliform },
                        { 9000.0f, 9800.0f, 0.21f, anchor::kStratiform } }, 3700.0f),
        layers_anchor({ { 130.0f, 580.0f, 1.0f, anchor::kSmoothDeck } }, 580.0f),
        layers_anchor({ { 1200.0f, 1800.0f, 0.58f, anchor::kStratiform } }, 3700.0f),
    };
    for (size_t i = 0; i < std::size(stations); ++i)
    {
        const anchor::Rows r = stations[i].rows();
        for (size_t k = 0; k < anchor::kSlices; ++k)
            printf("%zu %zu %.9g %.9g %.9g %.9g\n", i, k, r[k][0], r[k][1], r[k][2], r[k][3]);
    }
    return 0;
}

haze::Metar metar(const char *icao, std::vector<haze::CloudLayer> layers)
{
    haze::Metar m;
    m.valid = true;
    m.icao = icao;
    m.layers = std::move(layers);
    m.clouds_reported = true;
    m.temp_c = 14.0;
    m.dew_c = 13.0;
    return m;
}

struct Table
{
    std::array<uint8_t, anchor::kTableBytes> bytes = {};
    float weight(size_t i) const
    {
        float w;
        std::memcpy(&w, bytes.data() + 16 + 16 * i + 12, 4);
        return w;
    }
    anchor::Row row(size_t i, size_t k) const
    {
        anchor::Row r;
        std::memcpy(r.data(), bytes.data() + 16 + 16 * anchor::kStations + 16 * (anchor::kSlices * i + k), 16);
        return r;
    }
    uint32_t word(size_t i) const
    {
        uint32_t w;
        std::memcpy(&w, bytes.data() + 4 * i, 4);
        return w;
    }
};

bool near(double a, double b, double tol = 1e-5)
{
    return std::abs(a - b) <= tol;
}

int scenario()
{
    using anchor::kCrossfadeS, anchor::kFadeInS, anchor::kFadeOutS, anchor::kRoomFadeS, anchor::kStations;
    std::vector<std::string> log;
    anchor::StationSet set([&log](const std::string &line) {
        log.push_back(line);
        printf("  log: %s\n", line.c_str());
    });
    Table t;
    double lat = 55.618, lon = 12.656;  // the aircraft
    auto at = [&](double now) {
        set.tick(lat, lon, now);
        set.table(now, t.bytes.data());
    };

    // the first station: waits for its position, then counts at once (start of the flight)
    printf("first station\n");
    const haze::Metar ekch1 = metar("EKCH", { { 500.0, 5, -1 } });
    check(set.on_metar(ekch1, 55.62, 12.64, true, 3.0, 12.0, lat, lon, 0.0), "EKCH is new");
    check(!set.on_metar(ekch1, 55.62, 12.64, true, 3.0, 12.0, lat, lon, 0.2), "EKCH again isn't new");
    at(0.5);
    check(t.word(0) == anchor::kTableMagic && t.word(1) == 1, "header");
    check(t.weight(0) == 0.0f, "weight 0 until its position arrives");
    set.on_position("EKCH", 55.618, 12.656, 3.0, 1.0);
    at(1.0);
    check(t.weight(0) == 1.0f, "the first station counts at once");
    const anchor::Rows ekch1_rows = anchor::from_metar(ekch1, 55.618, 12.656, 3.0, 12.0).rows();
    check(t.row(0, 3) == ekch1_rows[3] && t.row(0, 3)[0] > 0.5f, "EKCH's rows (BKN005 in slice 3)");

    // a surveyed station: after the start's 30 s, fades in once its position arrives
    printf("surveyed station\n");
    check(set.on_metar(metar("ESMS", { { 300.0, 6, -1 } }), 55.5, 13.3, false, 0.0, 12.0, lat, lon, 40.0),
          "ESMS is new");
    at(45.0);
    check(t.weight(1) == 0.0f, "a surveyed station waits for its position");
    set.on_position("ESMS", 55.529, 13.371, 69.0, 46.0);
    at(46.0);
    check(t.weight(1) == 0.0f, "fading in from 0");
    at(46.0 + kFadeInS / 2);
    check(near(t.weight(1), 0.5), "half way: " + std::to_string(t.weight(1)));
    at(46.0 + kFadeInS);
    check(t.weight(1) == 1.0f, "in");

    // a new METAR crossfades (smoothstep: half way at half the time)
    printf("crossfade\n");
    const double t0 = 300.0;
    const haze::Metar ekch2 = metar("EKCH", { { 1500.0, 6, -1 } });
    const anchor::Rows ekch2_rows = anchor::from_metar(ekch2, 55.618, 12.656, 3.0, 12.0).rows();
    check(!set.on_metar(ekch2, 55.62, 12.64, true, 3.0, 12.0, lat, lon, t0), "EKCH's new METAR");
    at(t0);
    check(t.row(0, 3) == ekch1_rows[3], "the old rows at the start of the crossfade");
    at(t0 + kCrossfadeS / 2);
    for (size_t k : { 3, 5, 6, 9 })
    {
        const anchor::Row a = ekch1_rows[k], b = ekch2_rows[k], r = t.row(0, k);
        const double wa = a[1] * 0.5, wb = b[1] * 0.5;
        const double want = wa + wb > 0 ? (wa * a[0] + wb * b[0]) / (wa + wb) : 0.5 * (a[0] + b[0]);
        check(near(r[0], want) && near(r[1], wa + wb),
              "slice " + std::to_string(k) + " half way: " + std::to_string(r[0]) + " / " + std::to_string(r[1]) +
                  ", want " + std::to_string(want) + " / " + std::to_string(wa + wb));
    }
    at(t0 + kCrossfadeS);
    check(t.row(0, 3) == ekch2_rows[3] && t.row(0, 9) == ekch2_rows[9], "the new rows after the crossfade");
    check(t.weight(0) == 1.0f, "a crossfade keeps the weight");

    // the same METAR again changes nothing
    size_t lines = log.size();
    set.on_metar(ekch2, 55.62, 12.64, true, 3.0, 12.0, lat, lon, t0 + kCrossfadeS + 10.0);
    check(log.size() == lines, "a repeated METAR logs nothing");

    // a surveyed station without a position: dropped after 10 s
    printf("no position\n");
    const double t1 = 1000.0;
    set.on_metar(metar("XXXX", { { 800.0, 4, -1 } }), 56.0, 13.0, false, 0.0, 12.0, lat, lon, t1);
    at(t1 + 5.0);
    check(set.in_use() == 3, "waiting");
    at(t1 + 11.0);
    check(set.in_use() == 2, "dropped without a position");

    // left behind: fades out, then freed; back within 160 km fades in again
    printf("left behind\n");
    const double t2 = 1100.0;
    lat = 58.0;  // ~265 km north
    at(t2);
    at(t2 + kFadeOutS / 2);
    check(near(t.weight(0), 0.5) && near(t.weight(1), 0.5), "fading out: " + std::to_string(t.weight(0)));
    lat = 56.5;  // back (~100 km)
    at(t2 + kFadeOutS / 2 + 1.0);
    at(t2 + kFadeOutS);
    check(t.weight(0) > 0.5f && t.weight(0) < 1.0f, "fading back in: " + std::to_string(t.weight(0)));
    lat = 58.0;
    at(t2 + kFadeOutS + 10.0);
    at(t2 + 3 * kFadeOutS);
    check(set.in_use() == 0, "freed once faded out");

    // all slots in use: a farther new station is ignored; a nearer one makes the
    // farthest fade out and takes its slot with its next METAR; nothing drops at once
    printf("making room\n");
    const double t3 = 2000.0;
    for (size_t i = 0; i < kStations; ++i)
    {
        const std::string icao = "ST0" + std::to_string(i);
        set.on_metar(metar(icao.c_str(), { { 2000.0, 4, -1 } }), lat + 0.1 * (i + 1), lon, false, 0.0, 12.0, lat, lon,
                     t3);
        set.on_position(icao, lat + 0.1 * (i + 1), lon, 0.0, t3 + 0.5);
    }
    at(t3 + 1.0);
    check(set.in_use() == kStations, "all slots in use");
    const double t4 = t3 + 1.0 + kFadeInS;  // all in
    at(t4);
    const size_t last = kStations - 1;  // the farthest
    lines = log.size();
    check(!set.on_metar(metar("FAR1", { { 2000.0, 4, -1 } }), lat + 2.0, lon, false, 0.0, 12.0, lat, lon, t4 + 1.0) &&
              log.size() == lines,
          "a farther station is ignored");
    check(!set.on_metar(metar("NEAR", { { 2000.0, 4, -1 } }), lat - 0.05, lon, false, 0.0, 12.0, lat, lon, t4 + 2.0),
          "a nearer station waits for a slot");
    check(log.back().find("[station " + std::to_string(last) + "]: making room for NEAR") != std::string::npos,
          "the farthest makes room");
    lines = log.size();
    check(!set.on_metar(metar("NEA2", { { 2000.0, 4, -1 } }), lat - 0.06, lon, false, 0.0, 12.0, lat, lon, t4 + 3.0) &&
              log.size() == lines,
          "only one makes room at a time");
    at(t4 + 2.0 + kRoomFadeS / 2);
    check(near(t.weight(last), 0.5), "fading out to make room: " + std::to_string(t.weight(last)));
    at(t4 + 2.0 + kRoomFadeS + 1.0);
    check(set.in_use() == kStations - 1, "its slot is free once faded out");
    check(set.on_metar(metar("NEAR", { { 2000.0, 4, -1 } }), lat - 0.05, lon, false, 0.0, 12.0, lat, lon,
                       t4 + kRoomFadeS + 10.0),
          "the nearer station's next METAR takes the slot");

    // a jump: everything forgotten; the station nearest the aircraft without facility
    // data counts after 10 s at the request position, at once
    printf("restart\n");
    set.restart();
    at(3000.0);
    check(set.in_use() == 0 && t.word(1) == 0, "restart clears");
    set.on_metar(metar("YYYY", { { 1000.0, 5, -1 } }), lat, lon, true, 120.0, 12.0, lat, lon, 3001.0);
    at(3005.0);
    check(t.weight(0) == 0.0f, "waits for its position");
    at(3011.5);
    check(t.weight(0) == 1.0f, "then counts at once at the request position");
    float x;
    std::memcpy(&x, t.bytes.data() + 16 + 8, 4);
    check(near(std::asin(x) * 180.0 / 3.14159265358979323846, lat, 1e-4), "at the request position");

    // CB: the base part at the reported cover, isolated towers above, nothing above
    // what the METAR sees (12,000 ft)
    printf("CB\n");
    haze::Metar cb = metar("EKCH", { { 1000.0, 5, 1 } });
    const anchor::Anchor a = anchor::from_metar(cb, 55.618, 12.656, 3.0, 12.0);
    check(a.layers.size() == 2 && near(a.layers[0].top_m - a.layers[0].base_m, anchor::kConvectiveBaseM, 1.0) &&
              near(a.layers[1].value, anchor::kTowerValue) && near(a.layers[1].top_m, a.ceiling_m, 1.0) &&
              near(a.ceiling_m, 3.0 + 12000.0 * anchor::kFt, 1.0),
          "BKN010CB: " + a.describe());
    const anchor::Rows cr = a.rows();
    check(near(cr[5][0], 0.58) && near(cr[12][0], anchor::kTowerValue) && cr[16][1] == 0.0f,
          "CB rows: base part, towers, MSFS's own above 3.7 km");
    cb.layers = { { 1000.0, 6, 1 } };
    const anchor::Anchor ovc = anchor::from_metar(cb, 55.618, 12.656, 3.0, 12.0);
    check(ovc.layers.size() == 1 && near(ovc.ceiling_m, ovc.layers[0].top_m, 1.0), "OVC010CB: " + ovc.describe());

    printf(g_fails ? "%d check(s) failed\n" : "all checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}
} // namespace

int main(int argc, char **argv)
{
    if (argc > 1 && std::strcmp(argv[1], "--rows") == 0)
        return rows();
    return scenario();
}
