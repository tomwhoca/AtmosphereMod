// The A/B test's request and statistics (addon/src/ab_test.h), without the sim: tools/test_ab.py.
//
//   ab_test_test.exe                       the checks
//   ab_test_test.exe --tsv <id>_ab.tsv     a test's timed dispatches analysed again (as the add-on does)

#include "ab_test.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace
{
int g_failures = 0;

void check(bool ok, const std::string &what)
{
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what.c_str());
    g_failures += ok ? 0 : 1;
}

// A tiny deterministic generator for the noise.
struct Lcg
{
    uint32_t state = 12345;
    double next()  // 0..1
    {
        state = state * 1664525u + 1013904223u;
        return double(state >> 8) / double(1u << 24);
    }
};

struct Model
{
    double a_us = 1300.0, b_us = 1300.0;
    double drift = 0.0;       // over the run, a fraction
    double noise = 15.0;      // +-us
    bool strict = true;       // A, B, A, ... or as the add-on draws them (ab::side_of)
    double odd_us = 0.0;      // every odd frame this much slower (a strict rhythm)
    // other GPU work overlapping: frames slower by `busy_us`, with these chances on even and odd frames, in
    // bursts (a busy frame makes the next busy frames of its parity likelier)
    double busy_us = 0.0, busy_even = 0.0, busy_odd = 0.0;
};

std::vector<ab::Sample> run(uint64_t start, size_t frames, const Model &m)
{
    std::vector<ab::Sample> out;
    Lcg rng;
    bool burst = false;
    for (size_t i = 0; i < frames; ++i)
    {
        const uint64_t frame = start + i;
        const int side = m.strict ? int(i % 2) : ab::side_of(frame, 42);
        double us = (side == 0 ? m.a_us : m.b_us) * (1.0 + m.drift * double(i) / double(frames));
        us += (rng.next() * 2.0 - 1.0) * m.noise + ((frame & 1) ? m.odd_us : 0.0);
        const double chance = (frame & 1) ? m.busy_odd : m.busy_even;
        if ((frame & 1) == 0)
            burst = rng.next() < (burst ? 0.7 : 0.4) * chance / 0.6;
        if (((frame & 1) == 0 && burst) || ((frame & 1) == 1 && rng.next() < chance))
            us += m.busy_us;
        out.push_back({frame, side, us});
    }
    return out;
}

// A test's <id>_ab.tsv (frame, side, crc32, groups, in front, gpu us), the frames in front only.
int analyse_tsv(const char *path)
{
    std::ifstream file(path);
    if (!file)
    {
        std::fprintf(stderr, "can't read %s\n", path);
        return 2;
    }
    std::string line;
    std::getline(file, line);
    std::vector<ab::Sample> samples;
    while (std::getline(file, line))
    {
        std::vector<std::string> cols;
        std::stringstream ss(line);
        for (std::string col; std::getline(ss, col, '\t');)
            cols.push_back(col);
        if (cols.size() < 6 || cols[5] == "-" || cols[4] != "yes")
            continue;
        samples.push_back({std::strtoull(cols[0].c_str(), nullptr, 10), cols[1] == "B" ? 1 : 0,
                           std::strtod(cols[5].c_str(), nullptr)});
    }
    std::printf("%s\n", ab::describe(ab::summarize(samples, 30), "A", "B").c_str());
    return 0;
}
} // namespace

int main(int argc, char **argv)
{
    if (argc == 3 && std::string(argv[1]) == "--tsv")
        return analyse_tsv(argv[2]);

    // requests
    {
        const ab::Request r = ab::parse_request("# compare\nb=alu\n a = new \nframes=800\n");
        check(r.error.empty() && r.a == "new" && r.b == "alu" && r.frames == 800, "a request with a, b and frames");
        const ab::Request d = ab::parse_request("b=shadow_gate-2\r\n");
        check(d.error.empty() && d.a.empty() && d.frames == ab::kDefaultFrames,
              "b alone: the loaded shaders against it, the default frames");
        check(!ab::parse_request("a=new\n").error.empty(), "no b: refused");
        check(!ab::parse_request("b=../shaders\n").error.empty(), "a path as the set: refused");
        check(!ab::parse_request("b=C:\\x\n").error.empty(), "a drive path as the set: refused");
        check(!ab::parse_request("b=alu\na=alu\n").error.empty(), "the same set twice: refused");
        check(!ab::parse_request("b=alu\nframes=lots\n").error.empty(), "frames not a number: refused");
        check(!ab::parse_request("b=alu\ncolour=red\n").error.empty(), "an unknown key: refused");
        check(!ab::parse_request("b alu\n").error.empty(), "a line without '=': refused");
        check(ab::parse_request("b=alu\nframes=5\n").frames == ab::kMinFrames &&
                  ab::parse_request("b=alu\nframes=99999\n").frames == ab::kMaxFrames,
              "frames clamped to 100..4000");
    }

    // the sides' order: one of each per pair of frames, either way round at random
    {
        bool one_each = true;
        size_t b_first = 0;
        for (uint64_t pair = 0; pair < 1000; ++pair)
        {
            one_each = one_each && ab::side_of(2 * pair, 7) + ab::side_of(2 * pair + 1, 7) == 1;
            b_first += size_t(ab::side_of(2 * pair, 7));
        }
        check(one_each, "every pair of frames has one of each side");
        check(b_first > 440 && b_first < 560, "B first in about half the pairs (" + std::to_string(b_first) + " of 1000)");
        size_t same = 0;
        for (uint64_t f = 0; f < 1000; ++f)
            same += size_t(ab::side_of(f, 7) == ab::side_of(f, 8));
        check(same > 380 && same < 620, "another seed, another order");
    }

    // what the sim did on 16 Nov: a third of the frames 0.4 ms slower from other GPU work, mostly even ones and
    // in bursts; the rest +-1.6 %
    Model sim;
    sim.noise = 20.0;
    sim.drift = 0.02;
    sim.busy_us = 400.0;
    sim.busy_even = 0.6;
    sim.busy_odd = 0.09;
    sim.strict = false;
    {
        const ab::Summary s = ab::summarize(run(500, 600, sim), 30);
        check(s.frames_a + s.slow_a + s.frames_b + s.slow_b == 570, "the warm-up's 30 frames left out: 570 left");
        check(s.slow_a > 50 && s.slow_b > 50 && s.frames_a > 150 && s.frames_b > 150,
              "the busy frames left out on both sides (A " + std::to_string(s.slow_a) + ", B " +
                  std::to_string(s.slow_b) + ")");
        check(s.by_parity && std::abs(s.diff) < 0.006 && s.diff_low < 0.0 && s.diff_high > 0.0,
              "identical sides: B - A about 0, the interval holds it (" + std::to_string(s.diff) + ", " +
                  std::to_string(s.diff_low) + " .. " + std::to_string(s.diff_high) + ")");
        check(s.diff_high - s.diff_low < 0.02, "... and is narrow (under 1.5 % of 1.3 ms)");
        check(s.median_a > 1.29 && s.median_a < 1.33, "A's median: the frames without the busy work");
    }
    {
        Model m = sim;
        m.b_us = 1300.0 * 1.03;
        const ab::Summary s = ab::summarize(run(501, 600, m), 30);
        check(std::abs(s.diff - 0.039) < 0.008 && s.diff_low < 0.039 && s.diff_high > 0.039 && s.diff_low > 0.02,
              "B 3 % slower: B - A +0.039 ms found (" + std::to_string(s.diff) + ", " + std::to_string(s.diff_low) +
                  " .. " + std::to_string(s.diff_high) + ")");
        check(std::abs(s.diff_percent - 3.0) < 0.6, "about +3 % of A");
        const std::string line = ab::describe(s, "new", "alu");
        check(line.find("A/B new vs alu, ") == 0 && line.find("slow frames left out") != std::string::npos &&
                  line.find("(cancelled)") != std::string::npos,
              "the log line: " + line);
    }

    // B 3 % slower with drift and noise, in strict turns and without busy frames: found too, with a warning
    {
        Model m;
        m.a_us = 1700.0;
        m.b_us = 1751.0;
        m.drift = 0.06;
        const ab::Summary s = ab::summarize(run(500, 600, m), 30);
        check(s.frames_a == 285 && s.frames_b == 285 && s.slow_a + s.slow_b == 0, "no slow frames: 285 + 285");
        check(!s.by_parity && std::abs(s.diff - 0.0525) < 0.006 && s.diff_low < 0.0525 && s.diff_high > 0.0525,
              "B - A about +0.052 ms despite the drift (" + std::to_string(s.diff) + ", " +
                  std::to_string(s.diff_low) + " .. " + std::to_string(s.diff_high) + ")");
        check(ab::describe(s, "new", "alu").find("without frames of one parity") != std::string::npos,
              "strict turns: the log warns that a rhythm can't be told apart");
    }

    // a strict rhythm, every odd frame 0.4 ms slower: in strict turns B takes it all; in the drawn order the
    // odd frames are the slow ones on both sides and drop out, and B - A stays about 0
    {
        Model m;
        m.odd_us = 400.0;
        const ab::Summary strict = ab::summarize(run(0, 600, m), 30);
        check(strict.diff > 0.35, "strict turns, identical sides: B - A takes the rhythm (" +
                                       std::to_string(strict.diff) + " ms)");
        m.strict = false;
        const ab::Summary drawn = ab::summarize(run(0, 600, m), 30);
        check(std::abs(drawn.diff) < 0.006 && drawn.diff_low < 0.0 && drawn.diff_high > 0.0,
              "drawn order, identical sides: about 0 (" + std::to_string(drawn.diff) + ")");
    }

    // a rhythm too small to count as slow (+5 % on odd frames): cancelled by comparing like parities
    {
        Model m;
        m.odd_us = 65.0;
        m.strict = false;
        m.b_us = 1300.0 * 1.02;
        const ab::Summary s = ab::summarize(run(0, 600, m), 30);
        check(s.by_parity && std::abs(s.parity - 0.065) < 0.01, "the rhythm measured: odd frames +" +
                                                                   std::to_string(s.parity));
        check(std::abs(s.diff - 0.026) < 0.006, "B 2 % slower found through it (" + std::to_string(s.diff) + ")");
    }

    // a frame's dispatches are added up; frames with both sides are left out
    {
        std::vector<ab::Sample> samples = {
            {0, 0, 1000.0}, {0, 0, 500.0},  // frame 0, A: two dispatches, 1.5 ms
            {1, 1, 1600.0},                 // frame 1, B
            {2, 0, 1500.0}, {2, 1, 1600.0}, // frame 2: both sides, left out
            {3, 1, 1600.0},                 // frame 3, B
            {5, 0, 1500.0},                 // frame 5, A
            {6, 1, 1600.0},                 // frame 6, B
        };
        const ab::Summary s = ab::summarize(samples, 0);
        check(s.mixed == 1 && s.frames_a == 2 && s.frames_b == 3, "a frame with both sides left out");
        check(s.by_parity && std::abs(s.diff - 0.1) < 1e-9, "B - A +0.1 ms, by parity");
        check(std::abs(s.median_a - 1.5) < 1e-9, "a frame's two dispatches added up");
    }

    // nothing to compare
    {
        const ab::Summary s = ab::summarize({{0, 0, 1000.0}, {2, 0, 1000.0}}, 0);
        check(!s.valid() && ab::describe(s, "new", "alu").find("a side without frames") != std::string::npos,
              "only one side: no result, and the log says why");
        check(!ab::summarize({}, 30).valid(), "no samples");
    }

    std::printf("%s\n", g_failures == 0 ? "all passed" : (std::to_string(g_failures) + " FAILED").c_str());
    return g_failures == 0 ? 0 : 1;
}
