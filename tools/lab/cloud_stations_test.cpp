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
} // namespace

int main(int argc, char **argv)
{
    if (argc > 1 && std::strcmp(argv[1], "--rows") == 0)
        return rows();
    const int clouds = scenario();
    return haze_scenario() | clouds;
}
