// METAR anchoring across stations: the METAR stations
// the weather feed has heard from (the aircraft's nearest, the ones it passed,
// the ones ahead it surveys), each faded in when it arrives, out when left far
// behind, and crossfaded from one METAR to the next, written as the table the
// patched ch_cloudDensity reads from the debug buffer (tools/patch_dxil.py
// ANCHOR_TABLE). Where stations' areas overlap the generator blends them by
// distance, so the clouds change gradually from one station to the next
// instead of all at once when the nearest station changes.
//
// The haze comes from the same stations: each one's haze (haze_mapping.h, at
// its elevation) follows its METARs with the same crossfade and goes into the
// table after the rows, and the patched atmosphere passes weigh the stations
// by inverse distance at every sample of their marches (tools/patch_dxil.py
// HAZE_ROWS), so each part of the view gets the haze of the stations near it.
// haze_at is the same weighting at one point (the aircraft's own values).
//
// Not thread-safe: the weather feed calls it under its mutex.

#pragma once

#include "cloud_anchor.h"

#include <cstring>
#include <functional>
#include <string>
#include <utility>

namespace anchor
{
constexpr size_t kStations = 8;               // patch_dxil.py ANCHOR_STATIONS
constexpr uint32_t kTableMagic = 0x52484341;  // ANCHOR_MAGIC ('ACHR')
// The haze rows (patch_dxil.py HAZE_ROWS), after the stations' rows: two
// float4 per station, (T1, T3, T4, T5) and (T6, T10, counts, 0), blended into
// tunables.ini's by T13; counts is 0 for a station whose METAR has no
// visibility (its haze would be a guess from humidity). Header word 3 is
// kHazeMagic while they're written.
constexpr size_t kHazeOffset = 16 + 16 * kStations * (1 + kSlices);
constexpr size_t kHazeRowBytes = 32;
constexpr size_t kTableBytes = kHazeOffset + kHazeRowBytes * kStations;
constexpr uint32_t kHazeMagic = 0x455A4148;  // 'HAZE' (patch_dxil.py HAZE_MAGIC)
// patch_dxil.py HAZE_IDW_KM: b = weight / (d^2 + this^2). At 15 km a station's
// own visibility (LIMS 7 km, its neighbours 60-70 km away) was ~18 % diluted
// at the station itself (8 km, capture 235); at 10 km ~10 %.
constexpr double kHazeIdwKm = 10.0;
constexpr double kHazeHourStep = 0.25;       // a station's haze follows the time of day in steps of this (h)
// "10 km or more" (9999, CAVOK, 10SM) leaves the amount to haze_mapping.h's
// estimate from the air mass and humidity, which in the humid tropics is
// ~60 km. Next to a station whose haze is thicker than 10 km's, such a report
// is taken as just over 10 km (kOpenVisKm), fading back to the estimate with
// distance: by exp(-(d / kOpenPullKm)^2) times that station's weight. SBMN
// 9999 13 km from SBEG 9000 had put Manaus between them at ~17 km; a webcam
// there showed ~10 km (2026-10-29). Never hazier than the report allows.
constexpr double kOpenMinVisKm = 10.0;
constexpr double kOpenVisKm = 12.0;
constexpr double kOpenPullKm = 50.0;

// The haze tunables the weather drives, T1, T2, T3, T4, T5, T6, T9, T10 (weather_feed.cpp kDriven).
using Haze = std::array<float, 8>;

inline Haze haze_values(const haze::Tunables &t)
{
    return { t.bl_top_km, t.bl_edge_km, t.bl_haze, t.ft_haze, t.ft_scale_km, t.brown, t.g, t.angstrom };
}

// tunables.ini's values of the Haze tunables and T13: the table's haze rows are
// blended into them by mix, as the add-on blends its own (weather_feed.cpp apply).
struct HazeBlend
{
    Haze ini = {};
    float mix = 0.0f;
};
constexpr double kFadeInS = 180.0;       // a new station
constexpr double kFadeOutS = 180.0;      // a station left far behind
constexpr double kRoomFadeS = 60.0;      // a station making room for a nearer one
constexpr double kCrossfadeS = 300.0;    // a station's new METAR
constexpr double kPositionWaitS = 10.0;  // for a station's facility data (position, elevation)
constexpr double kDropKm = 200.0;        // stations farther from the aircraft fade out ...
constexpr double kReturnKm = 160.0;      // ... and back in when it comes back within this
constexpr double kInstantS = 30.0;       // after a start or a jump, stations appear at once for this long

// Great-circle distance, degrees in.
inline double distance_km(double lat1, double lon1, double lat2, double lon2)
{
    const double d = 3.14159265358979323846 / 180.0;
    const double a = std::sin((lat2 - lat1) * d * 0.5), b = std::sin((lon2 - lon1) * d * 0.5);
    const double h = a * a + std::cos(lat1 * d) * std::cos(lat2 * d) * b * b;
    return 2.0 * 6371.0 * std::asin(std::min(1.0, std::sqrt(h)));
}

class StationSet
{
public:
    using Log = std::function<void(const std::string &)>;
    explicit StationSet(Log log) : log_(std::move(log)) {}

    // Forget every station; the next ones appear at once (a new connection, a jump).
    void restart()
    {
        for (Station &s : slots_)
            s = Station();
        instant_pending_ = true;
        instant_until_ = -1.0;
    }

    // A METAR for its station, asked for at (request_lat, request_lon).
    // at_aircraft: asked for where the aircraft is (its nearest station): if
    // the station's position doesn't arrive, the request's stands in, with
    // ground_m (the terrain under the aircraft) for its elevation; a station
    // surveyed elsewhere is used only once its position is known. Returns true
    // for a new station: ask for its facility data (on_position). A new
    // station may have to wait for a slot (allocate): false, as for a known one.
    bool on_metar(const haze::Metar &m, double request_lat, double request_lon, bool at_aircraft, double ground_m,
                  double local_hour, double aircraft_lat, double aircraft_lon, double now)
    {
        Station *s = find(m.icao);
        const bool fresh = s == nullptr;
        if (fresh)
        {
            s = allocate(aircraft_lat, aircraft_lon, request_lat, request_lon, m.icao, now);
            if (s == nullptr)
                return false;
            *s = Station();
            s->used = true;
            s->icao = m.icao;
            s->added = now;
            s->request_lat = request_lat;  // kept: the aircraft moves on, the station doesn't
            s->request_lon = request_lon;
            s->ground_m = ground_m;
        }
        s->metar = m;
        s->at_aircraft |= at_aircraft;
        s->local_hour = local_hour;
        rebuild(*s, now, fresh ? (at_aircraft ? "new, nearest the aircraft" : "new, surveyed") : nullptr);
        return fresh;
    }

    // A station's facility data: its position and elevation.
    void on_position(const std::string &icao, double lat, double lon, double elevation_m, double now)
    {
        Station *s = find(icao);
        if (s == nullptr || !std::isfinite(lat) || !std::isfinite(lon))
            return;
        s->lat = lat;
        s->lon = lon;
        s->elevation_m = elevation_m;
        rebuild(*s, now, nullptr);
    }

    // Makes stations count once their position is known, fades out those left
    // far behind (and back in on return) and those whose report is stale
    // (haze::kStaleReportMin older than newest_report, the newest report_min the
    // sim has given; -1 none), frees the faded ones. local_hour (the
    // aircraft's): stations' haze follows the time of day between METARs.
    void tick(double aircraft_lat, double aircraft_lon, double now, double local_hour = NAN, int newest_report = -1)
    {
        for (size_t i = 0; i < kStations; ++i)
        {
            Station &s = slots_[i];
            if (!s.used)
                continue;
            if (s.ready < 0.0)
            {
                if (s.known() || (s.at_aircraft && now - s.added >= kPositionWaitS))
                    make_ready(s, i, now);
                else if (now - s.added >= kPositionWaitS)
                {
                    note(s, i, "no position (not an airport?): not used");
                    s = Station();
                }
                continue;
            }
            const int age = newest_report >= 0 && s.metar.report_min >= 0
                                ? haze::report_minutes_older(s.metar.report_min, newest_report)
                                : 0;
            if (age > haze::kStaleReportMin && !s.leaving)
            {
                s.leaving = true;
                fade(s, 0.0, kRoomFadeS, now);
                note(s, i, "its report is " + std::to_string(age / 60) + " h older than the newest: fading out");
            }
            if (std::isfinite(local_hour) && std::abs(std::remainder(local_hour - s.haze_hour, 24.0)) >= kHazeHourStep)
            {
                s.local_hour = local_hour;
                update_haze(s, now);
            }
            const double km = distance_km(aircraft_lat, aircraft_lon, s.pos_lat(), s.pos_lon());
            if (s.fade_to > 0.0 && km > kDropKm)
            {
                fade(s, 0.0, kFadeOutS, now);
                note(s, i, "left behind (" + std::to_string(std::lround(km)) + " km): fading out");
            }
            else if (s.fade_to <= 0.0 && km < kReturnKm && !s.leaving)
            {
                fade(s, 1.0, kFadeInS, now);
                note(s, i, "back within " + std::to_string(std::lround(km)) + " km: fading in");
            }
            if (s.fade_to <= 0.0 && weight(s, now) <= 0.0)
            {
                note(s, i, "faded out");
                s = Station();
            }
        }
    }

    // The table at `now` (kTableBytes; layout: patch_dxil.py ANCHOR_TABLE),
    // with the haze rows when given a blend (else none: the haze is the add-on's
    // values everywhere).
    void table(double now, uint8_t *out, const HazeBlend *blend = nullptr) const
    {
        std::memset(out, 0, kTableBytes);
        const uint32_t header[4] = { kTableMagic, static_cast<uint32_t>(in_use()), static_cast<uint32_t>(kStations),
                                     blend != nullptr ? kHazeMagic : 0 };
        std::memcpy(out, header, sizeof(header));
        const double d = 3.14159265358979323846 / 180.0;
        for (size_t i = 0; i < kStations; ++i)
        {
            const Station &s = slots_[i];
            if (!s.used)
                continue;
            const double lat = s.pos_lat() * d, lon = s.pos_lon() * d;
            const float station[4] = { static_cast<float>(std::cos(lat) * std::cos(lon)),
                                       static_cast<float>(std::cos(lat) * std::sin(lon)),
                                       static_cast<float>(std::sin(lat)), static_cast<float>(weight(s, now)) };
            std::memcpy(out + 16 + 16 * i, station, sizeof(station));
            const Rows rows = current(s, now);
            std::memcpy(out + 16 + 16 * kStations + 16 * kSlices * i, rows.data(), sizeof(rows));
            if (blend == nullptr)
                continue;
            const Haze h = effective_haze(s, now);
            const auto mixed = [&](size_t k) { return blend->ini[k] + (h[k] - blend->ini[k]) * blend->mix; };
            const float haze_rows[8] = { mixed(0), mixed(2), mixed(3), mixed(4), mixed(5), mixed(7),
                                         haze_counts(s) ? 1.0f : 0.0f, 0.0f };
            std::memcpy(out + kHazeOffset + kHazeRowBytes * i, haze_rows, sizeof(haze_rows));
        }
    }

    // The stations' haze at a point, weighted as the atmosphere passes weigh
    // them (b = weight / (d^2 + kHazeIdwKm^2)); unblended (the weather's own
    // values). Returns how many stations count, 0 for none (out unchanged).
    size_t haze_at(double lat, double lon, double now, Haze &out) const
    {
        double sum = 0.0;
        std::array<double, std::tuple_size_v<Haze>> acc = {};
        size_t n = 0;
        for (const Station &s : slots_)
        {
            const double w = s.used && haze_counts(s) ? weight(s, now) : 0.0;
            if (!(w > 0.0))
                continue;
            const double km = distance_km(lat, lon, s.pos_lat(), s.pos_lon());
            const double b = w / (km * km + kHazeIdwKm * kHazeIdwKm);
            const Haze h = effective_haze(s, now);
            for (size_t k = 0; k < acc.size(); ++k)
                acc[k] += b * h[k];
            sum += b;
            ++n;
        }
        if (n == 0 || !(sum > 0.0))
            return 0;
        for (size_t k = 0; k < acc.size(); ++k)
            out[k] = static_cast<float>(acc[k] / sum);
        return n;
    }

    size_t in_use() const
    {
        return static_cast<size_t>(std::count_if(slots_.begin(), slots_.end(), [](const Station &s) { return s.used; }));
    }

private:
    struct Station
    {
        bool used = false;
        std::string icao;
        haze::Metar metar;
        double request_lat = NAN, request_lon = NAN, ground_m = 0.0, local_hour = 12.0;
        bool at_aircraft = false;
        bool leaving = false;  // fading out to make room for a nearer station
        double lat = NAN, lon = NAN, elevation_m = NAN;  // facility data
        double added = 0.0, ready = -1.0;  // ready: when it began to count (-1: waiting for its position)
        Rows from = {}, to = {};           // the crossfade between METARs ...
        double change = -1e9;              // ... from this moment
        Haze haze_from = {}, haze_to = {}; // ... and of the haze, which changes on its own
        double haze_change = -1e9;
        double haze_hour = NAN;            // the local hour haze_to is for
        double fade_from = 0.0, fade_to = 0.0, fade_start = 0.0, fade_len = 0.0;
        std::string described;

        bool known() const { return std::isfinite(lat) && std::isfinite(lon); }
        double pos_lat() const { return known() ? lat : request_lat; }
        double pos_lon() const { return known() ? lon : request_lon; }
    };

    static double weight(const Station &s, double now)
    {
        if (s.ready < 0.0)
            return 0.0;
        const double t = s.fade_len > 0.0 ? std::clamp((now - s.fade_start) / s.fade_len, 0.0, 1.0) : 1.0;
        return s.fade_from + (s.fade_to - s.fade_from) * t;
    }

    static void fade(Station &s, double to, double seconds, double now)
    {
        s.fade_from = weight(s, now);
        s.fade_to = to;
        s.fade_start = now;
        s.fade_len = seconds;
    }

    // The crossfade, smoothstepped; each value blended by how much each METAR
    // describes the slice, so a slice coming into view keeps its new value.
    static Rows current(const Station &s, double now)
    {
        double t = std::clamp((now - s.change) / kCrossfadeS, 0.0, 1.0);
        t = t * t * (3.0 - 2.0 * t);
        if (t >= 1.0)
            return s.to;
        Rows out;
        for (size_t k = 0; k < kSlices; ++k)
        {
            const Row &a = s.from[k], &b = s.to[k];
            const double wa = a[1] * (1.0 - t), wb = b[1] * t, sum = wa + wb;
            const double fa = sum > 0.0 ? wa / sum : 1.0 - t, fb = 1.0 - fa;
            out[k] = { static_cast<float>(fa * a[0] + fb * b[0]), static_cast<float>(sum),
                       static_cast<float>(fa * a[2] + fb * b[2]), static_cast<float>(fa * a[3] + fb * b[3]) };
        }
        return out;
    }

    // A METAR without a visibility gives haze only from humidity: such a
    // station stays out of the haze field (its clouds still count).
    static bool haze_counts(const Station &s)
    {
        return std::isfinite(s.metar.vis_m);
    }

    // The haze's crossfade, smoothstepped like the rows'.
    static Haze current_haze(const Station &s, double now)
    {
        double t = std::clamp((now - s.haze_change) / kCrossfadeS, 0.0, 1.0);
        t = t * t * (3.0 - 2.0 * t);
        if (t >= 1.0)
            return s.haze_to;
        Haze out;
        for (size_t k = 0; k < out.size(); ++k)
            out[k] = static_cast<float>(s.haze_from[k] + (s.haze_to[k] - s.haze_from[k]) * t);
        return out;
    }

    // The station's haze as the field uses it: current_haze, with an
    // open-ended visibility (kOpenVisKm, above) set by its neighbours.
    Haze effective_haze(const Station &s, double now) const
    {
        Haze h = current_haze(s, now);
        if (!s.metar.vis_open)
            return h;
        // (haze_mapping.h: the surface aerosol T3 + T4 = sigma / kStockMieExtinction, sigma = 3 / V - Rayleigh)
        const auto sigma_at = [](double km) { return 3.0 / km - haze::kRayleigh550; };
        const double open = sigma_at(kOpenMinVisKm), k = haze::kStockMieExtinction;
        double pull = 0.0;
        for (const Station &o : slots_)
        {
            const double w = &o != &s && o.used && haze_counts(o) && !o.metar.vis_open ? weight(o, now) : 0.0;
            if (!(w > 0.0))
                continue;
            const Haze ho = current_haze(o, now);
            const double thicker = std::clamp(((ho[2] + ho[3]) * k - open) / (0.1 * open), 0.0, 1.0);
            const double km = distance_km(s.pos_lat(), s.pos_lon(), o.pos_lat(), o.pos_lon());
            pull = std::max(pull, w * thicker * std::exp(-(km / kOpenPullKm) * (km / kOpenPullKm)));
        }
        const double own = std::min((h[2] + h[3]) * k, open);  // the report: at least 10 km
        const double sigma = own + std::max(sigma_at(kOpenVisKm) - own, 0.0) * pull;
        h[2] = static_cast<float>(std::max(sigma / k - h[3], 0.0));
        return h;
    }

    // The station's haze from its latest METAR, at its elevation and local
    // hour; a change while it counts crossfades, otherwise it takes effect at once.
    void update_haze(Station &s, double now)
    {
        haze::Inputs in;
        in.metar = s.metar;
        in.ground_km = (std::isfinite(s.elevation_m) ? s.elevation_m : s.ground_m) * 0.001;
        in.local_hour = s.local_hour;
        const Haze h = haze_values(haze::derive(in));
        s.haze_hour = s.local_hour;
        if (h == s.haze_to)
            return;
        const bool crossfade = weight(s, now) > 0.0;
        s.haze_from = crossfade ? current_haze(s, now) : h;
        s.haze_change = crossfade ? now : -1e9;
        s.haze_to = h;
    }

    Station *find(const std::string &icao)
    {
        for (Station &s : slots_)
            if (s.used && s.icao == icao)
                return &s;
        return nullptr;
    }

    // A free slot. With all in use, a station asked for nearer the aircraft
    // than the farthest one in use makes that one fade out (kRoomFadeS); the
    // new station gets the slot with a later METAR (the survey asks again within
    // a minute). A station in use is never dropped at once: that changed the
    // clouds abruptly.
    Station *allocate(double aircraft_lat, double aircraft_lon, double lat, double lon, const std::string &for_icao,
                      double now)
    {
        for (Station &s : slots_)
            if (!s.used)
                return &s;
        size_t farthest = kStations;
        double farthest_km = -1.0;
        for (size_t i = 0; i < kStations; ++i)
        {
            if (slots_[i].leaving)
                return nullptr;  // one is making room already
            const double km = distance_km(aircraft_lat, aircraft_lon, slots_[i].pos_lat(), slots_[i].pos_lon());
            if (km > farthest_km)
            {
                farthest = i;
                farthest_km = km;
            }
        }
        const double km = distance_km(aircraft_lat, aircraft_lon, lat, lon);
        if (farthest == kStations || !(km < farthest_km))  // (NAN: the aircraft's position unknown)
            return nullptr;
        Station &f = slots_[farthest];
        const std::string why = " for " + for_icao + " (" + std::to_string(std::lround(farthest_km)) + " km away, " +
                                for_icao + " " + std::to_string(std::lround(km)) + " km; all " +
                                std::to_string(kStations) + " slots in use)";
        if (f.ready < 0.0)  // not counting yet: nothing to fade
        {
            note(f, farthest, "dropped" + why);
            return &f;
        }
        f.leaving = true;
        fade(f, 0.0, kRoomFadeS, now);
        note(f, farthest, "making room" + why + ": fading out over " + std::to_string(std::lround(kRoomFadeS)) + " s");
        return nullptr;
    }

    void make_ready(Station &s, size_t i, double now)
    {
        s.ready = now;
        if (instant_pending_)
        {
            instant_pending_ = false;
            instant_until_ = now + kInstantS;
        }
        const bool instant = now <= instant_until_;
        s.fade_from = instant ? 1.0 : 0.0;
        s.fade_to = 1.0;
        s.fade_start = now;
        s.fade_len = instant ? 0.0 : kFadeInS;
        s.from = s.to;
        s.change = -1e9;
        s.haze_from = s.haze_to;
        s.haze_change = -1e9;
        note(s, i, instant ? "in use at once (start of the flight or after a jump)"
                           : "in use, fading in over " + std::to_string(std::lround(kFadeInS)) + " s");
    }

    // The station's rows from its latest METAR and position; a change while it
    // counts crossfades, otherwise it takes effect at once.
    void rebuild(Station &s, double now, const char *why)
    {
        const double elevation = std::isfinite(s.elevation_m) ? s.elevation_m : s.ground_m;
        const Anchor a = from_metar(s.metar, s.pos_lat(), s.pos_lon(), elevation, s.local_hour);
        const Rows rows = a.rows();
        bool crossfade = false;
        if (rows != s.to)
        {
            crossfade = weight(s, now) > 0.0;
            s.from = crossfade ? current(s, now) : rows;
            s.change = crossfade ? now : -1e9;
            s.to = rows;
        }
        update_haze(s, now);
        std::string line = a.valid ? a.describe() : "cloud anchor " + s.icao + ": no sky condition in the METAR";
        if (!s.known())
            line += s.at_aircraft ? " (station position unknown: the aircraft's at the request)"
                                  : " (station position unknown)";
        if (line == s.described && why == nullptr)
            return;
        s.described = line;
        const size_t i = static_cast<size_t>(&s - slots_.data());
        std::string tail = why ? why : (crossfade ? "crossfading over " + std::to_string(std::lround(kCrossfadeS)) + " s"
                                                  : "updated");
        log_(line + " [station " + std::to_string(i) + ": " + tail + "]");
    }

    void note(const Station &s, size_t i, const std::string &what)
    {
        log_("cloud anchor " + s.icao + " [station " + std::to_string(i) + "]: " + what);
    }

    Log log_;
    std::array<Station, kStations> slots_ = {};
    bool instant_pending_ = true;
    double instant_until_ = -1.0;
};
} // namespace anchor
