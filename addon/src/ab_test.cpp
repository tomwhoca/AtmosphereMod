#include "ab_test.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <sstream>

namespace ab
{
namespace
{
std::string trim(const std::string &s)
{
    const size_t first = s.find_first_not_of(" \t\r\n");
    if (first == std::string::npos)
        return {};
    return s.substr(first, s.find_last_not_of(" \t\r\n") - first + 1);
}

// The q-quantile of sorted values, interpolated.
double quantile(const std::vector<double> &sorted, double q)
{
    if (sorted.empty())
        return 0.0;
    const double at = q * double(sorted.size() - 1);
    const size_t i = size_t(at);
    if (i + 1 >= sorted.size())
        return sorted.back();
    return sorted[i] + (sorted[i + 1] - sorted[i]) * (at - double(i));
}

double median_of(std::vector<double> values)
{
    std::sort(values.begin(), values.end());
    return quantile(values, 0.5);
}

uint64_t splitmix64(uint64_t x)
{
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}
} // namespace

int side_of(uint64_t frame, uint64_t seed)
{
    return int((frame & 1) ^ (splitmix64((frame >> 1) ^ splitmix64(seed)) & 1));
}

bool valid_set_name(const std::string &name)
{
    if (name.empty() || name.size() > 64)
        return false;
    return std::all_of(name.begin(), name.end(), [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_';
    });
}

Request parse_request(const std::string &text)
{
    Request r;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line))
    {
        line = trim(line);
        if (line.empty() || line[0] == '#')
            continue;
        const size_t eq = line.find('=');
        if (eq == std::string::npos)
        {
            r.error = "not key=value: " + line;
            return r;
        }
        const std::string key = trim(line.substr(0, eq)), value = trim(line.substr(eq + 1));
        if (key == "a" || key == "b")
        {
            if (!valid_set_name(value))
            {
                r.error = "not a set's name (letters, digits, - and _): " + key + '=' + value;
                return r;
            }
            (key == "a" ? r.a : r.b) = value;
        }
        else if (key == "frames")
        {
            char *end = nullptr;
            const unsigned long n = std::strtoul(value.c_str(), &end, 10);
            if (value.empty() || *end != '\0')
            {
                r.error = "frames isn't a number: " + value;
                return r;
            }
            r.frames = uint32_t(std::clamp<unsigned long>(n, kMinFrames, kMaxFrames));
        }
        else
        {
            r.error = "unknown key: " + key;
            return r;
        }
    }
    if (r.b.empty())
        r.error = "no b=<set>";
    else if (r.a == r.b)
        r.error = "a and b are the same set";
    return r;
}

Summary summarize(const std::vector<Sample> &samples, uint64_t warmup)
{
    Summary s;
    if (samples.empty())
        return s;
    uint64_t first = UINT64_MAX;
    for (const Sample &x : samples)
        first = std::min(first, x.frame);
    struct Frame
    {
        int side = -1;
        double us = 0.0;
        bool mixed = false;
    };
    std::map<uint64_t, Frame> frames;
    for (const Sample &x : samples)
    {
        if (x.frame < first + warmup || (x.side != 0 && x.side != 1))
            continue;
        Frame &f = frames[x.frame];
        if (f.side >= 0 && f.side != x.side)
            f.mixed = true;
        f.side = x.side;
        f.us += x.us;
    }
    // the slow frames: above each side's 20th percentile + kSlowCut
    double cut[2] = {0.0, 0.0};
    for (int side = 0; side < 2; ++side)
    {
        std::vector<double> all;
        for (const auto &[index, f] : frames)
            if (!f.mixed && f.side == side)
                all.push_back(f.us);
        std::sort(all.begin(), all.end());
        cut[side] = all.empty() ? 0.0 : quantile(all, 0.2) * (1.0 + kSlowCut);
    }
    // the frames counted, by side and parity
    std::vector<double> group[2][2];  // [side][parity], ms
    std::vector<double> a, b;
    for (const auto &[index, f] : frames)
    {
        if (f.mixed)
        {
            ++s.mixed;
            continue;
        }
        if (f.us > cut[f.side])
        {
            ++(f.side == 0 ? s.slow_a : s.slow_b);
            continue;
        }
        group[f.side][index & 1].push_back(f.us / 1000.0);
        (f.side == 0 ? a : b).push_back(f.us / 1000.0);
    }
    std::sort(a.begin(), a.end());
    std::sort(b.begin(), b.end());
    s.frames_a = a.size();
    s.frames_b = b.size();
    s.median_a = quantile(a, 0.5);
    s.median_b = quantile(b, 0.5);
    s.p10_a = quantile(a, 0.1);
    s.p90_a = quantile(a, 0.9);
    s.p10_b = quantile(b, 0.1);
    s.p90_b = quantile(b, 0.9);
    if (!s.valid())
        return s;
    s.by_parity = !group[0][0].empty() && !group[0][1].empty() && !group[1][0].empty() && !group[1][1].empty();
    // B - A from groups of values: by parity, or the sides as a whole
    const auto estimate = [&](const std::vector<double> (&g)[2][2], const std::vector<double> &all_a,
                              const std::vector<double> &all_b) {
        if (!s.by_parity)
            return median_of(all_b) - median_of(all_a);
        return ((median_of(g[1][0]) - median_of(g[0][0])) + (median_of(g[1][1]) - median_of(g[0][1]))) / 2.0;
    };
    s.diff = estimate(group, a, b);
    if (s.by_parity)
        s.parity = ((median_of(group[0][1]) - median_of(group[0][0])) +
                    (median_of(group[1][1]) - median_of(group[1][0]))) / 2.0;
    // the 95 % interval: the estimate from 1000 resamplings (with replacement) of each group, 2.5 and 97.5 %
    uint64_t rng = 0x5EED;
    const auto resample = [&](const std::vector<double> &from) {
        std::vector<double> out(from.size());
        for (double &v : out)
            v = from[size_t((rng = splitmix64(rng)) % from.size())];
        return out;
    };
    std::vector<double> boot;
    boot.reserve(1000);
    for (int i = 0; i < 1000; ++i)
    {
        std::vector<double> g[2][2], ra, rb;
        if (s.by_parity)
        {
            for (int side = 0; side < 2; ++side)
                for (int parity = 0; parity < 2; ++parity)
                    g[side][parity] = resample(group[side][parity]);
        }
        else
        {
            ra = resample(a);
            rb = resample(b);
        }
        boot.push_back(estimate(g, ra, rb));
    }
    std::sort(boot.begin(), boot.end());
    s.diff_low = quantile(boot, 0.025);
    s.diff_high = quantile(boot, 0.975);
    s.diff_percent = s.median_a > 0.0 ? 100.0 * s.diff / s.median_a : 0.0;
    return s;
}

std::string describe(const Summary &s, const std::string &a, const std::string &b)
{
    char buf[512];
    if (!s.valid())
    {
        snprintf(buf, sizeof(buf), "A/B %s vs %s: a side without frames to count (%zu + %zu frames; slow ones left out "
                 "%zu + %zu, with both sides %zu)", a.c_str(), b.c_str(), s.frames_a, s.frames_b, s.slow_a, s.slow_b,
                 s.mixed);
        return buf;
    }
    snprintf(buf, sizeof(buf), "A/B %s vs %s, %zu + %zu frames: A %.3f ms, B %.3f ms per frame (medians; 10-90 %%: "
             "A %.3f-%.3f, B %.3f-%.3f); B - A %+.3f ms (%+.1f %%), 95 %% between %+.3f and %+.3f ms",
             a.c_str(), b.c_str(), s.frames_a, s.frames_b, s.median_a, s.median_b, s.p10_a, s.p90_a, s.p10_b, s.p90_b,
             s.diff, s.diff_percent, s.diff_low, s.diff_high);
    std::string out = buf;
    if (s.slow_a + s.slow_b > 0)
    {
        const auto share = [](size_t slow, size_t kept) {
            return 100.0 * double(slow) / double(std::max<size_t>(slow + kept, 1));
        };
        snprintf(buf, sizeof(buf), "; slow frames left out (other GPU work overlapping): A %.0f %%, B %.0f %%",
                 share(s.slow_a, s.frames_a), share(s.slow_b, s.frames_b));
        out += buf;
    }
    if (s.by_parity)
    {
        snprintf(buf, sizeof(buf), "; odd frames %+.3f ms (cancelled)", s.parity);
        out += buf;
    }
    else
    {
        out += "; a side without frames of one parity: an every-other-frame rhythm of the sim's would be in B - A";
    }
    if (s.mixed > 0)
        out += "; " + std::to_string(s.mixed) + " frame(s) with both sides left out";
    return out;
}
} // namespace ab
