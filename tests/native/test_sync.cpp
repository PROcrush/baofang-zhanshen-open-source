#include "baofang_sync.h"

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>

static_assert(offsetof(BFObservation, position_ms) == 0, "C ABI field order");
static_assert(offsetof(BFObservation, speed_ratio) == 8, "C ABI field order");
static_assert(offsetof(BFObservation, duration_ms) == 16, "C ABI field order");
static_assert(offsetof(BFObservation, captured_ns) == 24, "C ABI field order");
static_assert(offsetof(BFObservation, valid_until_ns) == 32, "C ABI field order");
static_assert(offsetof(BFObservation, play_state) == 40, "C ABI field order");
static_assert(offsetof(BFEstimate, status) == 8, "C ABI field order");

namespace {
int checks = 0;

void check(bool condition, const char *name) {
    ++checks;
    if (!condition) {
        std::cerr << "FAIL: " << name << '\n';
        std::exit(1);
    }
}

BFObservation base() {
    return BFObservation{1000.0, 1.0, 10000.0, 1000000000LL, 1500000000LL,
                         BF_PLAY_PLAYING};
}

BFEstimate estimate(const BFObservation &source, int64_t now_ns) {
    BFEstimate output{-999.0, -999};
    check(bf_estimate_position(&source, now_ns, &output) == 0, "classification return");
    return output;
}

void expect_position(const BFObservation &source, int64_t now_ns, double expected,
                     const char *name) {
    const BFEstimate output = estimate(source, now_ns);
    check(output.status == BF_ESTIMATE_VALID &&
              std::fabs(output.position_ms - expected) < 0.000001,
          name);
}

void expect_status(const BFObservation &source, int64_t now_ns, int32_t expected,
                   const char *name) {
    const BFEstimate output = estimate(source, now_ns);
    check(output.status == expected && output.position_ms == 0.0, name);
}
} // namespace

int main() {
    BFObservation source = base();
    expect_position(source, source.captured_ns, 1000.0, "capture anchor unchanged");
    expect_position(source, 1250000000LL, 1250.0, "forward position tracks elapsed time");
    source.speed_ratio = 2.0;
    expect_position(source, 1250000000LL, 1500.0, "double speed advances source time");
    source.speed_ratio = 0.5;
    expect_position(source, 1250000000LL, 1125.0, "half speed advances source time");
    source.speed_ratio = 0.0;
    expect_position(source, 1250000000LL, 1000.0, "zero speed freezes source time");
    source.play_state = BF_PLAY_PAUSED;
    source.speed_ratio = 3.0;
    expect_position(source, 1500000000LL, 1000.0, "pause freezes despite speed setting");
    expect_status(source, 1500000001LL, BF_ESTIMATE_STALE, "paused anchor expires too");

    source = base();
    expect_position(source, 1500000000LL, 1500.0, "TTL upper endpoint inclusive");
    expect_status(source, 1500000001LL, BF_ESTIMATE_STALE, "never extrapolate past TTL");
    expect_status(source, 999999999LL, BF_ESTIMATE_INVALID, "future capture rejected");
    source.valid_until_ns = source.captured_ns - 1;
    expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "reversed TTL rejected");
    source = base();
    source.valid_until_ns = source.captured_ns;
    expect_position(source, source.captured_ns, 1000.0, "zero TTL valid at exact anchor");

    source = base();
    source.position_ms = 9900.0;
    source.speed_ratio = 2.0;
    expect_position(source, 1500000000LL, 10000.0, "duration caps extrapolation");
    source.duration_ms = 0.0;
    expect_position(source, 1500000000LL, 10900.0, "zero duration means unknown");
    source.position_ms = -1.0;
    expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "negative position rejected");
    source = base();
    source.duration_ms = -1.0;
    expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "negative duration rejected");
    source = base();
    source.position_ms = source.duration_ms + 1.0;
    expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "anchor beyond duration rejected");

    source = base();
    source.play_state = BF_PLAY_UNKNOWN;
    expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "unknown play state rejected");
    source.play_state = 8;
    expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "unrecognized play state rejected");
    source = base();
    source.speed_ratio = -1.0;
    expect_status(source, source.captured_ns, BF_ESTIMATE_UNSUPPORTED_REVERSE, "reverse explicit status");
    source.play_state = BF_PLAY_PAUSED;
    expect_status(source, source.captured_ns, BF_ESTIMATE_UNSUPPORTED_REVERSE, "paused reverse unsupported");

    const double bad_values[] = {std::numeric_limits<double>::quiet_NaN(),
                                 std::numeric_limits<double>::infinity(),
                                 -std::numeric_limits<double>::infinity()};
    for (double bad : bad_values) {
        source = base();
        source.position_ms = bad;
        expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "nonfinite position rejected");
        source = base();
        source.speed_ratio = bad;
        expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "nonfinite speed rejected");
        source = base();
        source.duration_ms = bad;
        expect_status(source, source.captured_ns, BF_ESTIMATE_INVALID, "nonfinite duration rejected");
    }

    source = base();
    source.captured_ns = std::numeric_limits<int64_t>::min();
    source.valid_until_ns = std::numeric_limits<int64_t>::max();
    source.duration_ms = 0.0;
    const BFEstimate large_elapsed = estimate(source, source.valid_until_ns);
    check(large_elapsed.status == BF_ESTIMATE_VALID && std::isfinite(large_elapsed.position_ms) &&
              large_elapsed.position_ms > 10000000000000.0,
          "timestamp subtraction cannot overflow int64");
    source.speed_ratio = std::numeric_limits<double>::max();
    expect_status(source, source.valid_until_ns, BF_ESTIMATE_INVALID, "unknown-duration overflow rejected");
    source.duration_ms = 10000.0;
    expect_position(source, source.valid_until_ns, 10000.0, "bounded overflow clamps to end");

    BFEstimate output{5.0, 5};
    check(bf_estimate_position(nullptr, 0, &output) == -1 &&
              output.status == BF_ESTIMATE_INVALID && output.position_ms == 0.0,
          "null source safely classified");
    source = base();
    check(bf_estimate_position(&source, source.captured_ns, nullptr) == -1,
          "null output safely rejected");

    const BFObservation previous = base();
    BFObservation present = base();
    present.captured_ns = 1250000000LL;
    present.valid_until_ns = 1750000000LL;
    present.position_ms = 1250.0;
    check(bf_is_discontinuity(&previous, &present, 20.0) == 0, "continuous anchor");
    present.position_ms = 1270.0;
    check(bf_is_discontinuity(&previous, &present, 20.0) == 0, "tolerance boundary continuous");
    present.position_ms = 9000.0;
    check(bf_is_discontinuity(&previous, &present, 20.0) == 1, "Hot Cue jump inferred");
    present.position_ms = 100.0;
    check(bf_is_discontinuity(&previous, &present, 20.0) == 1, "loop wrap inferred");
    present.captured_ns = previous.valid_until_ns + 1;
    check(bf_is_discontinuity(&previous, &present, 20.0) == -1, "stale previous is unavailable");
    present = base();
    present.captured_ns = previous.captured_ns - 1;
    check(bf_is_discontinuity(&previous, &present, 20.0) == -1, "out of order anchors unavailable");
    present = base();
    present.play_state = BF_PLAY_UNKNOWN;
    check(bf_is_discontinuity(&previous, &present, 20.0) == -1, "invalid present unavailable");
    check(bf_is_discontinuity(&previous, &present, -1.0) == -1, "negative tolerance rejected");
    check(bf_is_discontinuity(&previous, &present,
                             std::numeric_limits<double>::quiet_NaN()) == -1,
          "NaN tolerance rejected");
    check(bf_is_discontinuity(nullptr, &present, 20.0) == -1, "null discontinuity input safe");

    std::cout << "PASS: " << checks << " native sync checks\n";
    return 0;
}
