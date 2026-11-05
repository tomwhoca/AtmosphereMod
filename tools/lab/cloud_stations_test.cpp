// Checks the METAR anchoring's station set (addon/src/cloud_stations.h) and
// its rows (cloud_anchor.h); tools/test_cloud_stations.py builds and runs it.
//   cloud_stations_test --rows   the rows of test_cloud_gen_capture.py's three
//                                stations, for comparison with its reference
//   cloud_stations_test          fades, crossfades, waiting for positions, drops,
//                                making room (and settling) and CB layers over simulated time

#include "cloud_stations.h"

#include <algorithm>
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

    // the storm cells' evidence, in the row the anchoring doesn't read: thunder, VCTS, CB, TCU, none
    printf("storm evidence\n");
    const auto evidence = [](const haze::Metar &m) {
        return anchor::from_metar(m, 55.618, 12.656, 3.0, 12.0).rows()[anchor::kStormRow];
    };
    check(evidence(cb) == anchor::Row{ anchor::kStormCb, 1.0f, 0.0f, 0.0f }, "OVC010CB: CB evidence");
    haze::Metar ts = metar("EKCH", { { 1000.0, 5, -1 } });
    ts.thunder = true;
    check(evidence(ts)[0] == anchor::kStormThunder, "TS at the station");
    ts.thunder = false;
    ts.thunder_vicinity = true;
    check(evidence(ts)[0] == anchor::kStormVicinity, "VCTS");
    ts.thunder_vicinity = false;
    ts.layers = { { 3000.0, 4, 0 } };
    check(near(evidence(ts)[0], anchor::kStormTcu * 6.0f / 7.0f), "SCT030TCU: 0.30");
    ts.layers = { { 1500.0, 3, 1 } };
    check(near(evidence(ts)[0], 0.5), "FEW015CB: 0.5 (one or two cells)");
    ts.layers = { { 1500.0, 4, 1 }, { 3000.0, 5, 0 } };
    check(near(evidence(ts)[0], 0.6), "SCT015CB BKN030TCU: the CB's 0.6 (more than the TCU's 0.35)");
    ts.layers = { { 3000.0, 4, -1 } };
    check(evidence(ts) == anchor::Row{ 0.0f, 1.0f, 0.0f, 0.0f }, "no storm: 0");
    ts.clouds_reported = false;
    ts.layers.clear();
    ts.thunder = true;
    check(evidence(ts)[0] == anchor::kStormThunder, "TS without a cloud group still counts");

    printf(g_fails ? "%d check(s) failed\n" : "all checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

// An open-ended visibility's haze pulled toward kOpenVisKm by `pull` (cloud_stations.h
// effective_haze), for the expectations.
anchor::Haze open_pulled(anchor::Haze h, double pull)
{
    const double k = haze::kStockMieExtinction;
    const auto sigma_at = [](double km) { return 3.0 / km - haze::kRayleigh550; };
    const double own = std::min((h[2] + h[3]) * k, sigma_at(anchor::kOpenMinVisKm));
    const double sigma = own + std::max(sigma_at(anchor::kOpenVisKm) - own, 0.0) * pull;
    h[2] = static_cast<float>(std::max(sigma / k - h[3], 0.0));
    return h;
}

// Visibility (km) from a haze row's T3 and T4, as haze_mapping.h defines it.
double row_vis_km(const std::array<float, 8> &r)
{
    return 3.0 / ((r[1] + r[2]) * haze::kStockMieExtinction + haze::kRayleigh550);
}

// The haze from the stations: the table's haze rows (blended into the ini's by
// T13), their crossfade between METARs, the time of day, and haze_at's weighting.
int haze_scenario()
{
    using anchor::Haze, anchor::kCrossfadeS, anchor::kHazeOffset, anchor::kHazeRowBytes;
    anchor::StationSet set([](const std::string &line) { printf("  log: %s\n", line.c_str()); });
    Table t;
    const double lat = 55.618, lon = 12.656;
    const auto haze_row = [&t](size_t i) {
        std::array<float, 8> r;
        std::memcpy(r.data(), t.bytes.data() + kHazeOffset + kHazeRowBytes * i, sizeof(r));
        return r;
    };
    const auto derived = [](const haze::Metar &m, double elevation_m, double hour) {
        haze::Inputs in;
        in.metar = m;
        in.ground_km = elevation_m * 0.001;
        in.local_hour = hour;
        return anchor::haze_values(haze::derive(in));
    };
    printf("haze rows\n");
    haze::Metar clear = metar("EKCH", { { 4000.0, 3, -1 } });
    clear.vis_m = 10000.0;
    clear.vis_open = true;
    haze::Metar hazy = metar("ESMS", { { 4000.0, 3, -1 } });
    hazy.vis_m = 4000.0;
    hazy.haze = true;
    set.on_metar(clear, lat, lon, true, 3.0, 12.0, lat, lon, 0.0);
    set.on_position("EKCH", 55.618, 12.656, 3.0, 0.5);
    set.on_metar(hazy, 55.5, 13.3, false, 0.0, 12.0, lat, lon, 1.0);
    set.on_position("ESMS", 55.529, 13.371, 69.0, 1.5);
    set.tick(lat, lon, 2.0);
    anchor::HazeBlend blend;
    blend.ini = { 1.5f, 0.15f, 4.0f, 0.6f, 2.5f, 0.05f, 0.7f, 1.3f };
    blend.mix = 1.0f;
    set.table(2.0, t.bytes.data());
    check(t.word(3) == 0, "no haze rows without a blend");
    set.table(2.0, t.bytes.data(), &blend);
    check(t.word(3) == anchor::kHazeMagic, "the haze rows' magic");
    // EKCH's 9999 is open-ended and ESMS (4 km) 46 km away: pulled toward 12 km (kOpenVisKm)
    const Haze esms = derived(hazy, 69.0, 12.0);
    const double ekch_esms_km = anchor::distance_km(55.618, 12.656, 55.529, 13.371);
    const Haze ekch = open_pulled(derived(clear, 3.0, 12.0), std::exp(-std::pow(ekch_esms_km / anchor::kOpenPullKm, 2)));
    const auto row_of = [](const Haze &h) { return std::array<float, 8>{ h[0], h[2], h[3], h[4], h[5], h[7], 1, 0 }; };
    // (the rows went through the blend with mix 1: ini + (h - ini) * 1, within float rounding)
    const auto same = [](const std::array<float, 8> &a, const std::array<float, 8> &b) {
        for (size_t k = 0; k < a.size(); ++k)
            if (!near(a[k], b[k], 1e-5 * (1.0 + std::abs(b[k]))))
                return false;
        return true;
    };
    check(same(haze_row(0), row_of(ekch)) && same(haze_row(1), row_of(esms)), "the rows: each station's haze at its elevation");
    check(near(haze_row(1)[0] - haze_row(0)[0], 0.066, 1e-4),
          "ESMS's top 66 m above EKCH's, as its elevation: " + std::to_string(haze_row(1)[0]));
    check(esms[2] > 2.0f * ekch[2], "4 km HZ is hazier than 9999: T3 " + std::to_string(esms[2]) + " vs " +
                                       std::to_string(ekch[2]));
    blend.mix = 0.25f;
    set.table(2.0, t.bytes.data(), &blend);
    check(near(haze_row(1)[1], blend.ini[2] + (esms[2] - blend.ini[2]) * 0.25f, 1e-4) &&
              near(haze_row(1)[5], blend.ini[7] + (esms[7] - blend.ini[7]) * 0.25f, 1e-5),
          "blended into the ini's by T13");
    blend.mix = 1.0f;

    printf("haze at a point\n");
    Haze h = {};
    check(set.haze_at(55.618, 12.656, 2.0, h) == 2, "two stations count");
    const double d1 = anchor::distance_km(55.618, 12.656, 55.529, 13.371), c2 = anchor::kHazeIdwKm * anchor::kHazeIdwKm;
    const double b0 = 1.0 / c2, b1 = 1.0 / (d1 * d1 + c2);
    check(near(h[2], (b0 * ekch[2] + b1 * esms[2]) / (b0 + b1), 1e-4), "at EKCH: inverse distance");
    check(std::abs(h[2] - ekch[2]) < 0.1 * std::abs(esms[2] - ekch[2]), "at EKCH mostly EKCH's");
    Haze mid = {};
    set.haze_at(0.5 * (55.618 + 55.529), 0.5 * (12.656 + 13.371), 2.0, mid);
    check(near(mid[2], 0.5 * (ekch[2] + esms[2]), 0.01 * (esms[2] - ekch[2])),
          "half way: half each " + std::to_string(mid[2]));

    printf("haze crossfade\n");
    haze::Metar hazier = hazy;
    hazier.vis_m = 2500.0;
    const Haze esms2 = derived(hazier, 69.0, 12.0);
    set.on_metar(hazier, 55.5, 13.3, false, 0.0, 12.0, lat, lon, 100.0);
    set.table(100.0, t.bytes.data(), &blend);
    check(same(haze_row(1), row_of(esms)), "the old haze at the start of the crossfade");
    set.table(100.0 + kCrossfadeS / 2, t.bytes.data(), &blend);
    check(near(haze_row(1)[1], 0.5 * (esms[2] + esms2[2]), 1e-3), "half way: " + std::to_string(haze_row(1)[1]));
    set.table(100.0 + kCrossfadeS, t.bytes.data(), &blend);
    check(same(haze_row(1), row_of(esms2)), "the new haze after it");

    printf("time of day\n");
    set.tick(lat, lon, 1000.0, 12.1);
    set.table(1000.0, t.bytes.data(), &blend);
    check(same(haze_row(1), row_of(esms2)), "under kHazeHourStep: unchanged");
    set.tick(lat, lon, 1000.0, 22.0);
    set.table(1000.0 + kCrossfadeS, t.bytes.data(), &blend);
    const Haze night = derived(hazier, 69.0, 22.0);
    check(same(haze_row(1), row_of(night)) && night[0] < esms2[0], "at night: the night's lower top " +
                                                                    std::to_string(haze_row(1)[0]));

    printf("no visibility\n");
    haze::Metar blind = metar("LIRT", {});  // (an AUTO report with //// for visibility)
    blind.raw = "SPECI LIRT 211356Z AUTO 27016G26KT 240V310 //// // ///////// 11/10 Q1015";
    blind.clouds_reported = false;
    set.on_metar(blind, 55.7, 12.5, false, 0.0, 22.0, lat, lon, 1100.0);
    set.on_position("LIRT", 55.7, 12.5, 10.0, 1100.5);
    set.tick(lat, lon, 1101.0, 22.0);
    set.table(1101.0 + anchor::kFadeInS, t.bytes.data(), &blend);
    check(t.weight(2) == 1.0f && haze_row(2)[6] == 0.0f && haze_row(1)[6] == 1.0f,
          "a station without visibility: in the table for its clouds, not for the haze");
    check(set.haze_at(55.7, 12.5, 1101.0 + anchor::kFadeInS, h) == 2, "... and haze_at leaves it out");

    printf("stale report\n");
    haze::Metar fresh = hazier;
    check(fresh.report_min < 0, "the test's METARs have no report time");
    blind.report_min = 21 * 1440 + 13 * 60 + 56;
    set.on_metar(blind, 55.7, 12.5, false, 0.0, 22.0, lat, lon, 1500.0);
    const int newest = 29 * 1440 + 14 * 60 + 20;
    check(haze::report_minutes_older(blind.report_min, newest) / 60 == 192, "8 days old");
    check(haze::report_minutes_older(30 * 1440 + 23 * 60, 1 * 1440 + 60) < 0 &&
              haze::report_minutes_older(21 * 1440, 1 * 1440) > 7 * 1440,
          "across a month's end: 2 h old isn't stale, 8+ days is");
    set.tick(lat, lon, 1500.0, 22.0, newest);
    set.table(1500.0 + anchor::kRoomFadeS / 2, t.bytes.data(), &blend);
    check(near(t.weight(2), 0.5), "fading out: " + std::to_string(t.weight(2)));
    set.tick(lat, lon, 1500.0 + anchor::kRoomFadeS + 1.0, 22.0, newest);
    check(set.in_use() == 2, "then gone");
    set.tick(lat, lon, 1600.0, 22.0, newest);
    check(set.in_use() == 2, "the stations without a report time stay");

    printf("open-ended visibility\n");
    {
        anchor::StationSet open_set([](const std::string &) {});
        haze::Metar sbeg = metar("SBEG", { { 2500.0, 3, -1 } }), sbmn = metar("SBMN", { { 2500.0, 3, -1 } });
        sbeg.temp_c = sbmn.temp_c = 32.0;
        sbeg.dew_c = sbmn.dew_c = 25.0;
        sbeg.vis_m = 9000.0;
        sbmn.vis_m = 10000.0;
        sbmn.vis_open = true;
        const double alat = -3.04, alon = -60.05;
        open_set.on_metar(sbeg, -3.041, -60.051, true, 89.0, 12.0, alat, alon, 0.0);
        open_set.on_position("SBEG", -3.041, -60.051, 89.0, 0.5);
        open_set.on_metar(sbmn, -3.146, -59.985, false, 0.0, 12.0, alat, alon, 1.0);
        open_set.on_position("SBMN", -3.146, -59.985, 76.0, 1.5);
        open_set.tick(alat, alon, 2.0);
        open_set.table(2.0, t.bytes.data(), &blend);
        const double own = row_vis_km(row_of(derived(sbmn, 76.0, 12.0)));
        const double pulled = row_vis_km(haze_row(1));
        const double d = anchor::distance_km(-3.041, -60.051, -3.146, -59.985);
        const double want = row_vis_km(row_of(open_pulled(derived(sbmn, 76.0, 12.0),
                                                          std::exp(-std::pow(d / anchor::kOpenPullKm, 2)))));
        check(own > 40.0 && pulled > 10.0 && pulled < 13.0 && near(pulled, want, 1e-3 * want),
              "9999 13 km from 9000: ~12 km (own estimate " + std::to_string(own) + ", now " + std::to_string(pulled) + ")");
        check(same(haze_row(0), row_of(derived(sbeg, 89.0, 12.0))), "the 9000 station is as reported");
        // the hazier station 300 km away: the open report keeps its own estimate, at most 10 km's haze
        open_set.on_position("SBEG", -5.7, -60.05, 89.0, 3.0);
        open_set.table(3.0, t.bytes.data(), &blend);
        check(near(row_vis_km(haze_row(1)), own, 0.01 * own), "far from the hazier station: its own " +
                                                                  std::to_string(row_vis_km(haze_row(1))));
        // a hazier station that's 9999 too doesn't pull
        open_set.on_position("SBEG", -3.041, -60.051, 89.0, 4.0);
        haze::Metar sbeg2 = sbeg;
        sbeg2.vis_m = 10000.0;
        sbeg2.vis_open = true;
        open_set.on_metar(sbeg2, -3.041, -60.051, true, 89.0, 12.0, alat, alon, 5.0);
        open_set.table(5.0 + kCrossfadeS, t.bytes.data(), &blend);
        check(near(row_vis_km(haze_row(1)), own, 0.01 * own), "next to another 9999: its own " +
                                                                  std::to_string(row_vis_km(haze_row(1))));
    }

    set.restart();
    check(set.haze_at(lat, lon, 2000.0, h) == 0, "no stations: none");
    printf(g_fails ? "%d check(s) failed\n" : "all checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

size_t lines_with(const std::vector<std::string> &log, const std::string &what)
{
    return static_cast<size_t>(std::count_if(log.begin(), log.end(), [&](const std::string &line) {
        return line.find(what) != std::string::npos;
    }));
}

// The set must settle, whatever order the survey's answers come in. The two loops
// seen in weather.log, 2026-11-04: CYQG (94 km) making room for KARB (49 km) every
// 105 s, because the freed slot went to whichever METAR came next, and that was
// CYQG's; and CYQV (220 km, nearest a survey point 110 km ahead) new, left behind,
// faded out and new again every 210 s.
int churn_scenario()
{
    using anchor::kFadeInS, anchor::kRoomFadeS, anchor::kStations;
    std::vector<std::string> log;
    Table t;
    const haze::Metar sky = metar("XXXX", { { 2000.0, 4, -1 } });
    const auto named = [&sky](const std::string &icao) {
        haze::Metar m = sky;
        m.icao = icao;
        return m;
    };
    struct Place
    {
        std::string icao;
        double lat, lon;
    };
    const double lat = 42.63, lon = -83.98;

    printf("no loop making room\n");
    {
        anchor::StationSet set([&log](const std::string &line) { log.push_back(line); });
        // seven stations nearer than CYQG, known and in use; CYQG the farthest, KARB nearer
        std::vector<Place> places;
        for (size_t i = 0; i + 1 < kStations; ++i)
            places.push_back({ "ST0" + std::to_string(i), lat + 0.1 + 0.05 * i, lon });
        const Place cyqg{ "CYQG", 42.276, -82.958 }, karb{ "KARB", 42.223, -83.746 };
        places.push_back(cyqg);
        double now = 0.0;
        for (const Place &p : places)
        {
            set.on_metar(named(p.icao), p.lat, p.lon, false, 0.0, 12.0, lat, lon, now);
            set.on_position(p.icao, p.lat, p.lon, 200.0, now);
        }
        set.tick(lat, lon, 1.0);
        check(set.in_use() == kStations, "all slots in use");
        // the survey: 7 points, one every 15 s, so each answers every 105 s: KARB at 0 s and
        // CYQG at 75 s, in that order, as in the log; the others are known stations
        const std::array<Place, 7> answers = { karb, places[0], places[1], places[2], places[3], cyqg, places[4] };
        size_t asked_positions = 0;
        for (int step = 0; step < 1800; ++step)
        {
            now = 2.0 + step;
            if (step % 15 == 0)
            {
                const Place &p = answers[(step / 15) % answers.size()];
                if (set.on_metar(named(p.icao), p.lat, p.lon, false, 0.0, 12.0, lat, lon, now))
                {
                    ++asked_positions;
                    set.on_position(p.icao, p.lat, p.lon, 200.0, now + 0.5);
                }
            }
            set.tick(lat, lon, now);
        }
        check(lines_with(log, "making room for KARB") == 1,
              "CYQG makes room for KARB once in 30 minutes: " + std::to_string(lines_with(log, "making room for KARB")));
        check(lines_with(log, "CYQG [station 7]: faded out") == 1 && lines_with(log, "new, surveyed") == kStations + 1,
              "CYQG isn't back: only the first eight and KARB were ever new: " + std::to_string(lines_with(log, "new, surveyed")));
        check(asked_positions == 1, "KARB's facility data is asked for once: " + std::to_string(asked_positions));
        set.table(now, t.bytes.data());
        check(set.in_use() == kStations && t.weight(kStations - 1) == 1.0f, "KARB in CYQG's slot, faded in");
        const size_t settled = log.size();
        // CYQG, farther than everything now in use, stays out however often it answers
        for (int step = 0; step < 600; step += 15)
        {
            set.on_metar(named(cyqg.icao), cyqg.lat, cyqg.lon, false, 0.0, 12.0, lat, lon, now + step);
            set.tick(lat, lon, now + step);
        }
        check(log.size() == settled, "settled: nothing more to log");
    }

    printf("the freed slot goes to a nearer station, for a while\n");
    {
        const auto fill = [&](anchor::StationSet &set) {
            for (size_t i = 0; i < kStations; ++i)
            {
                const std::string icao = "ST0" + std::to_string(i);
                set.on_metar(named(icao), lat + 0.1 * (i + 1), lon, false, 0.0, 12.0, lat, lon, 0.0);
                set.on_position(icao, lat + 0.1 * (i + 1), lon, 200.0, 0.0);
            }
            set.tick(lat, lon, 1.0);
        };
        anchor::StationSet a([](const std::string &) {});
        fill(a);
        const double near_lat = lat + 0.15;  // nearer than the farthest, ST07 at 0.8 deg
        a.on_metar(named("NEAR"), near_lat, lon, false, 0.0, 12.0, lat, lon, 10.0);  // ST07 makes room
        a.tick(lat, lon, 10.0 + kRoomFadeS + 1.0);
        check(a.in_use() == kStations - 1, "ST07's slot is free");
        a.on_metar(named("ST07"), lat + 0.8, lon, false, 0.0, 12.0, lat, lon, 10.0 + kRoomFadeS + 2.0);
        check(a.in_use() == kStations - 1, "the station that left doesn't get it back");
        a.on_metar(named("FARR"), lat + 0.95, lon, false, 0.0, 12.0, lat, lon, 10.0 + kRoomFadeS + 3.0);
        check(a.in_use() == kStations - 1, "nor does one farther than it");
        a.on_metar(named("NEA2"), lat + 0.5, lon, false, 0.0, 12.0, lat, lon, 10.0 + kRoomFadeS + 4.0);
        check(a.in_use() == kStations, "any station nearer than it does, not only the one that asked: in flight, that one may be long past");
        a.on_metar(named("NEAR"), near_lat, lon, false, 0.0, 12.0, lat, lon, 10.0 + kRoomFadeS + 5.0);
        check(a.in_use() == kStations, "and the one that asked, with no slot left, starts another swap");

        anchor::StationSet b([](const std::string &) {});
        fill(b);
        b.on_metar(named("GONE"), near_lat, lon, false, 0.0, 12.0, lat, lon, 10.0);  // ... and never heard of again
        const double later = 10.0 + kRoomFadeS + anchor::kRoomHoldS + 1.0;
        b.tick(lat, lon, later);
        b.on_metar(named("FARR"), lat + 0.95, lon, false, 0.0, 12.0, lat, lon, later);
        check(b.in_use() == kStations, "the slot isn't kept for ever: after a while any station takes it");
    }

    printf("the distance that counts is the station's own\n");
    {
        log.clear();
        anchor::StationSet set([&log](const std::string &line) { log.push_back(line); });
        for (size_t i = 0; i < kStations; ++i)
        {
            const std::string icao = "ST0" + std::to_string(i);
            set.on_metar(named(icao), lat + 0.1 * (i + 1), lon, false, 0.0, 12.0, lat, lon, 0.0);
            set.on_position(icao, lat + 0.1 * (i + 1), lon, 200.0, 0.0);
        }
        // known to be farther than the farthest in use, though asked for close by
        set.on_position("FARR", lat + 1.2, lon, 200.0, 0.0);
        set.tick(lat, lon, 1.0);
        check(!set.on_metar(named("FARR"), lat + 0.05, lon, false, 0.0, 12.0, lat, lon, 2.0) &&
                  lines_with(log, "making room") == 0,
              "a station whose position is known isn't judged by where it was asked for");
    }

    printf("no loop for a station beyond the range\n");
    {
        log.clear();
        anchor::StationSet set([&log](const std::string &line) { log.push_back(line); });
        const double far_lat = 51.899, far_lon = -103.799, ac_lat = 51.899, ac_lon = -100.6;  // ~220 km
        const double ask_lat = 51.899, ask_lon = -102.2;                                      // ~110 km ahead
        size_t asked = 0;
        for (int step = 0; step < 1800; ++step)
        {
            const double now = step;
            if (step % 60 == 0 && set.on_metar(named("CYQV"), ask_lat, ask_lon, false, 0.0, 12.0, ac_lat, ac_lon, now))
            {
                ++asked;
                set.on_position("CYQV", far_lat, far_lon, 560.0, now + 0.5);
            }
            set.tick(ac_lat, ac_lon, now);
        }
        check(asked == 1, "its facility data is asked for once: " + std::to_string(asked));
        check(lines_with(log, "left behind") == 0, "it's never 'left behind': " + std::to_string(lines_with(log, "left behind")));
        check(lines_with(log, "too far") == 1, "it's noted once: " + std::to_string(lines_with(log, "too far")));
        check(lines_with(log, "new, surveyed") == 1, "and added once: " + std::to_string(lines_with(log, "new, surveyed")));
        check(set.in_use() == 0, "it holds no slot");
        // the aircraft comes closer: it counts, with no new request
        const double closer = -102.6;  // ~83 km
        check(!set.on_metar(named("CYQV"), ask_lat, ask_lon, false, 0.0, 12.0, ac_lat, closer, 2000.0), "known: no request");
        check(set.in_use() == 1, "within range it takes a slot");
        set.tick(ac_lat, closer, 2001.0);
        set.table(2001.0 + kFadeInS, t.bytes.data());
        check(t.weight(0) == 1.0f, "and fades in");
    }

    printf(g_fails ? "%d check(s) failed\n" : "all checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

// A new report that changes the kind of visibility it gives crossfades like any other. It changed the
// haze at once (issue 19; KMSP 10SM to 5SM -RA BR on 3 Nov 2026: ~13 to ~25 km in one frame), the open-ended
// report's pull and whether a station counts being taken from the latest report as they stood.
int haze_flip_scenario()
{
    using anchor::Haze, anchor::kCrossfadeS;
    printf("haze: a report changing its kind of visibility\n");
    const double lat = 44.88, lon = -93.22;
    const auto report = [](const char *icao, double vis_m, bool open, bool mist = false) {
        haze::Metar m = metar(icao, { { 1000.0, 8, -1 } });
        m.vis_m = vis_m;
        m.vis_open = open;
        m.fog = mist;
        return m;
    };
    const auto sigma = [](const Haze &h) { return static_cast<double>(h[2]) + h[3]; };
    // the largest change in one second of the haze at the aircraft, A's report changing at 100 s (or B's)
    const auto largest_step = [&](const haze::Metar &a0, const haze::Metar &b0, const haze::Metar &later, const char *name) {
        std::vector<std::string> log;
        anchor::StationSet set([&log](const std::string &line) { log.push_back(line); });
        set.on_metar(a0, lat, lon, true, 250.0, 12.0, lat, lon, 0.0);
        set.on_position(a0.icao, lat, lon, 250.0, 0.5);
        set.on_metar(b0, 45.05, -93.22, false, 0.0, 12.0, lat, lon, 1.0);
        set.on_position(b0.icao, 45.05, -93.22, 250.0, 1.5);
        set.tick(lat, lon, 2.0);
        Haze h = {};
        const bool at_a = later.icao == a0.icao;
        set.haze_at(lat, lon, 99.0, h);
        double previous = sigma(h);
        const double first = previous;
        double worst = 0.0;
        set.on_metar(later, at_a ? lat : 45.05, at_a ? lon : -93.22, at_a, 0.0, 12.0, lat, lon, 100.0);
        for (double t = 100.0; t <= 101.0 + kCrossfadeS; t += 1.0)
        {
            set.tick(lat, lon, t);
            set.haze_at(lat, lon, t, h);
            worst = std::max(worst, std::abs(std::log(sigma(h) / previous)));
            previous = sigma(h);
        }
        printf("  %s: T3+T4 %.1f -> %.1f, at most %.1f %% in a second\n", name, first, previous, 100.0 * worst);
        // (a fade of the largest of these peaks at 2.8 %; in one frame they were 40-194 %)
        check(worst < 0.03, std::string(name) + ": gradual, " + std::to_string(100.0 * worst) + " % in a second");
        check(lines_with(log, "visibility now") == 1, std::string(name) + ": logged once, a neighbour's too");
    };
    const haze::Metar a_open = report("KMSP", 10000.0, true), b_thick = report("KFCM", 4000.0, false);
    largest_step(a_open, b_thick, report("KMSP", 8047.0, false), "open-ended to a number, thicker neighbour");
    largest_step(report("KMSP", 3000.0, false, true), b_thick, a_open, "a number to open-ended");
    largest_step(a_open, b_thick, report("KFCM", 10000.0, true), "the neighbour's report turning open-ended");
    largest_step(a_open, report("KFCM", 10000.0, true), b_thick, "the neighbour's report turning thick");
    haze::Metar blind = report("KMSP", NAN, false);
    largest_step(report("KMSP", 4000.0, false), report("KFCM", 10000.0, true), blind, "a report losing its visibility");
    largest_step(blind, report("KFCM", 10000.0, true), report("KMSP", 4000.0, false), "a report getting one");
    printf(g_fails ? "%d check(s) failed\n" : "all checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

// Longer runs among many stations, with weather_feed.cpp's survey: stations every
// 0.4 deg, the nearest to the aircraft asked for every minute, and the nearest to one
// survey point every 15 s. Sitting still the set must settle; flying, the station
// under the aircraft must always count, and no station may keep coming and going.
int soak_scenario()
{
    using anchor::kStations;
    constexpr double kPi = 3.14159265358979323846;
    struct Point
    {
        double bearing_deg, km;
    };
    constexpr Point kSurvey[] = { { 0.0, 50.0 }, { 0.0, 110.0 }, { -60.0, 60.0 }, { 60.0, 60.0 }, { -125.0, 80.0 }, { 125.0, 80.0 }, { 180.0, 70.0 } };
    const auto icao_at = [](int i, int j) { return "G" + std::to_string(i) + "_" + std::to_string(j); };
    const haze::Metar sky = metar("XXXX", { { 2000.0, 4, -1 } });
    // heading east, degrees: returns aircraft-relative point
    const auto ahead = [](double lat, double lon, double turned_deg, const Point &p, double &out_lat, double &out_lon) {
        const double b = (p.bearing_deg + turned_deg) * 3.14159265358979323846 / 180.0 + 3.14159265358979323846 / 2;
        out_lat = lat + p.km / 111.2 * std::cos(b);
        out_lon = lon + p.km / (111.2 * std::cos(lat * 3.14159265358979323846 / 180.0)) * std::sin(b);
    };
    // stop_after_s >= 0: flies that long, then stays, circling in orbit_km if > 0 (a 4-minute hold).
    // swaps_per_10min: the swaps in each 10 minutes after that.
    const auto run = [&](const char *name, double speed_deg_per_s, int seconds, std::vector<std::string> &log, double &worst_km,
                         int stop_after_s = -1, double orbit_km = 0.0, std::vector<size_t> *swaps_per_10min = nullptr) {
        anchor::StationSet set([&log](const std::string &line) { log.push_back(line); });
        Table t;
        double lat = 45.0;
        double lon = 10.0;
        double hold_lon = 0.0, turned_deg = 0.0;
        size_t next_point = 0, swaps_before = 0;
        worst_km = 0.0;
        const auto answer = [&](double qlat, double qlon, bool at_aircraft, double now) {
            const int i = static_cast<int>(std::lround(qlat / 0.4)), j = static_cast<int>(std::lround(qlon / 0.4));
            haze::Metar m = sky;
            m.icao = icao_at(i, j);
            if (set.on_metar(m, qlat, qlon, at_aircraft, 0.0, 12.0, lat, lon, now))
                set.on_position(m.icao, i * 0.4, j * 0.4, 100.0, now + 0.5);
        };
        for (int s = 0; s < seconds; ++s)
        {
            const double now = s;
            if (stop_after_s < 0 || s < stop_after_s)
                lon += speed_deg_per_s;
            else
            {
                if (s == stop_after_s)
                {
                    hold_lon = lon;
                    swaps_before = lines_with(log, "making room");
                }
                else if (swaps_per_10min && (s - stop_after_s) % 600 == 0)
                {
                    const size_t swaps = lines_with(log, "making room");
                    swaps_per_10min->push_back(swaps - swaps_before);
                    swaps_before = swaps;
                }
                if (orbit_km > 0.0)
                {
                    const double a = (s - stop_after_s) / 240.0 * 2.0 * kPi;
                    lat = 45.0 + orbit_km / 111.2 * std::sin(a);
                    lon = hold_lon + orbit_km / (111.2 * std::cos(45.0 * kPi / 180.0)) * (std::cos(a) - 1.0);
                    turned_deg = (s - stop_after_s) / 240.0 * 360.0;
                }
            }
            if (s % 60 == 0)
                answer(lat, lon, true, now);
            if (s % 15 == 5)
            {
                double qlat, qlon;
                ahead(lat, lon, turned_deg, kSurvey[next_point++ % std::size(kSurvey)], qlat, qlon);
                answer(qlat, qlon, false, now);
            }
            set.tick(lat, lon, now);
            if (s < 600)
                continue;
            // the nearest counting station, from the table the generator reads
            set.table(now, t.bytes.data());
            double best = 1e9;
            for (size_t i = 0; i < kStations; ++i)
            {
                if (t.weight(i) < 0.5f)
                    continue;
                float v[3];
                std::memcpy(v, t.bytes.data() + 16 + 16 * i, sizeof(v));
                const double slat = std::asin(v[2]) * 180.0 / kPi, slon = std::atan2(v[1], v[0]) * 180.0 / kPi;
                best = std::min(best, anchor::distance_km(lat, lon, slat, slon));
            }
            worst_km = std::max(worst_km, best);
        }
        printf("  %s: %zu log lines, %zu making room, %zu new, nearest counting station at worst %.1f km\n", name, log.size(),
               lines_with(log, "making room"), lines_with(log, ": new,"), worst_km);
    };

    printf("soak: sitting still among many stations\n");
    {
        std::vector<std::string> log;
        double worst = 0.0;
        run("still", 0.0, 7200, log, worst);
        check(lines_with(log, "making room") <= kStations, "a few swaps at most, then settled: " + std::to_string(lines_with(log, "making room")));
        check(worst < 40.0, "a station near the aircraft counts throughout: worst " + std::to_string(worst) + " km");
    }
    // The test flight of 5 Nov 2026: at cruise speed, then a hold. Stopped, the set settles. Circling, which
    // stations are nearest keeps changing, so a few far ones keep swapping (5 in 8 minutes on that flight);
    // before the fix it was 61 in these 110 minutes, stopped or circling.
    for (const double orbit_km : { 0.0, 8.0 })
    {
        printf("soak: flying for 30 minutes, then %s\n", orbit_km > 0.0 ? "holding (8 km circle, 4 minutes a turn)" : "stopping dead");
        std::vector<std::string> log;
        std::vector<size_t> swaps;
        double worst = 0.0;
        run(orbit_km > 0.0 ? "hold" : "stop", 0.25 / (111.2 * std::cos(45.0 * kPi / 180.0)), 1800 + 7200, log, worst, 1800, orbit_km, &swaps);
        printf("  swaps per 10 minutes after that:");
        size_t total = 0, after_first = 0;
        for (size_t i = 0; i < swaps.size(); ++i)
        {
            printf(" %zu", swaps[i]);
            total += swaps[i];
            after_first += i > 0 ? swaps[i] : 0;
        }
        printf("\n");
        if (orbit_km > 0.0)
            check(total <= 38, "in a hold, fewer swaps than before the fix (61): " + std::to_string(total));
        else
            check(after_first == 0, "stopped, settled within 10 minutes: " + std::to_string(after_first) + " swaps after that");
    }
    printf("soak: flying across them at 250 m/s\n");
    {
        std::vector<std::string> log;
        double worst = 0.0;
        run("flying", 0.25 / (111.2 * std::cos(45.0 * kPi / 180.0)), 7200, log, worst);
        check(worst < 77.0, "a station near the aircraft counts throughout, no farther than before the fix (76.7 km): " + std::to_string(worst) + " km");
        check(lines_with(log, "making room") <= 78, "no more swaps than before the fix (78, normal progress in flight): " + std::to_string(lines_with(log, "making room")));
        // no station comes and goes: new at most twice each
        size_t worst_new = 0;
        std::string who;
        for (const std::string &line : log)
        {
            const size_t a = line.find("cloud anchor "), b = line.find(' ', a + 13);
            if (a == std::string::npos || line.find(": new,") == std::string::npos)
                continue;
            const std::string icao = line.substr(a + 13, b - a - 13);
            const size_t count = lines_with(log, "cloud anchor " + icao + " ") == 0 ? 0 : static_cast<size_t>(std::count_if(log.begin(), log.end(), [&](const std::string &l) {
                return l.find("cloud anchor " + icao + " ") != std::string::npos && l.find(": new,") != std::string::npos;
            }));
            if (count > worst_new)
            {
                worst_new = count;
                who = icao;
            }
        }
        check(worst_new <= 2, "no station keeps coming back: " + who + " new " + std::to_string(worst_new) + " times");
    }

    printf(g_fails ? "%d check(s) failed\n" : "all checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}
} // namespace

int main(int argc, char **argv)
{
    if (argc > 1 && std::strcmp(argv[1], "--rows") == 0)
        return rows();
    const int clouds = scenario();
    const int churn = churn_scenario();
    const int soak = soak_scenario();
    const int flip = haze_flip_scenario();
    return haze_scenario() | clouds | churn | soak | flip;
}
