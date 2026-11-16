// The A/B test, for measuring shader changes (a developer's install only: ReShade.ini [ATMOSPHEREMOD]
// Developer=1, which tools/deploy.py sets and the installer never does; not for users).
//
// Two sets of the mod's compute shaders take turns frame by frame in one view, and the GPU times each
// frame's dispatches of the pipelines that differ. Both sides then see the same clouds, GPU clocks and
// temperature. Logs taken minutes apart don't: MSFS's clouds keep evolving even in the sim's full pause,
// and in one paused view the cloud pass moved 6 % in two minutes (16 Nov 2026, logs 827-831).
//
// Only the mod's compute pipelines the game runs in that view whose shader the sets have take part (in
// practice the cloud renderer: tools/build_ab.py builds its variants), and each of their dispatches is timed on
// its own, between timestamps right before and after it.
//
// Asked for with a file, <DataPath>/ab/request.txt (tools/ab.py writes it), lines key=value:
//   b=<set>     the set to compare: <DataPath>/ab/<set>/*.cso (tools/build_ab.py)
//   a=<set>     optional, the other side; without it, the shaders loaded (<DataPath>/shaders)
//   frames=<n>  optional, frames timed for both sides together (default 600, 100..4000)
// The add-on takes the file (deletes it), runs the test, and writes the result to events.log and to
// <DataPath>/ab/result.txt, and every timed dispatch to logs/captures/<id>_ab.tsv. It beeps as it starts
// measuring and when the result is in, and leaves out frames with another window than the sim's in front
// (another window can take the GPU, and the sim may slow down behind it).

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace ab
{
constexpr uint32_t kDefaultFrames = 600;
constexpr uint32_t kMinFrames = 100, kMaxFrames = 4000;

struct Request
{
    std::string a;  // empty: the shaders loaded
    std::string b;
    uint32_t frames = kDefaultFrames;
    std::string error;  // why the request can't run; empty if it can
};

// A set's name: letters, digits, '-' and '_' (a folder in <DataPath>/ab, never a path).
bool valid_set_name(const std::string &name);
Request parse_request(const std::string &text);

// The side of the test's frame `frame` (0 = A, 1 = B): one of each in every pair of frames (0-1, 2-3, ...), in
// an order drawn at random per pair. Strict turns gave one side all the even frames, and MSFS's cloud pass can
// take 0.4 ms longer on every other frame (1.53 and 1.95 ms in turn, 16 Nov 2026): identical shaders then came
// out 20 % apart, either way round.
int side_of(uint64_t frame, uint64_t seed);

// One timed dispatch: the frame it ran in (counted from the test's start, one per frame rendered), its side
// (0 = A, 1 = B) and its GPU time.
struct Sample
{
    uint64_t frame = 0;
    int side = 0;
    double us = 0.0;
};

// Frames with other GPU work overlapping the timed pass are left out: those more than kSlowCut above their
// side's 20th percentile. In one paused view 35 % of the frames were 0.4 ms (28 %) slower, more often on even
// frames and in runs, while the rest stayed within +-1.6 % (16 Nov 2026); with them, null tests scattered by
// +-7 %. The random order spreads them over both sides alike, so the frames left compare fairly.
constexpr double kSlowCut = 0.10;

struct Summary
{
    size_t frames_a = 0, frames_b = 0;  // frames counted (after the warm-up, without the slow ones), each side
    size_t slow_a = 0, slow_b = 0;      // slow frames left out, each side
    size_t mixed = 0;                   // frames with both sides' dispatches in them, left out
    double median_a = 0.0, median_b = 0.0;  // ms per frame (a frame's dispatches added up), the frames counted
    double p10_a = 0.0, p90_a = 0.0, p10_b = 0.0, p90_b = 0.0;
    // B - A (ms) and its 95 % interval (a bootstrap). By parity: B's median minus A's among the odd frames and
    // among the even ones, averaged, so whatever a frame's parity adds (`parity`: the odd frames' extra time,
    // the two sides' mean) cancels; if a side lacks frames of a parity (strict turns), B's median minus A's.
    double diff = 0.0, diff_low = 0.0, diff_high = 0.0;
    double diff_percent = 0.0;  // of median_a
    bool by_parity = false;
    double parity = 0.0;
    bool valid() const { return frames_a > 0 && frames_b > 0; }
};

// The frames from the first one + warmup on.
Summary summarize(const std::vector<Sample> &samples, uint64_t warmup);

// One line for the log: "A/B new vs alu, 297 + 297 frames: A 1.712 ms, B 1.745 ms per frame (medians;
// 10-90 %: ...); B - A +0.033 ms (+1.9 %), 95 % between +0.021 and +0.046 ms; slow frames left out (other
// GPU work overlapping): A 35 %, B 36 %; odd frames +0.004 ms (cancelled)".
std::string describe(const Summary &s, const std::string &a, const std::string &b);
} // namespace ab
