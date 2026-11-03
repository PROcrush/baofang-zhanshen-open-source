#include "baofang_sync.h"

#include <algorithm>
#include <cmath>
#include <limits>

extern "C" int bf_estimate_position(const BFObservation *observation,
                                     int64_t now_ns,
                                     BFEstimate *estimate) {
    if (estimate == nullptr) {
        return -1;
    }
    estimate->position_ms = 0.0;
    estimate->status = BF_ESTIMATE_INVALID;
    if (observation == nullptr) {
        return -1;
    }

    const BFObservation &source = *observation;
    if (!std::isfinite(source.position_ms) ||
        !std::isfinite(source.speed_ratio) ||
        !std::isfinite(source.duration_ms) || source.position_ms < 0.0 ||
        source.duration_ms < 0.0 ||
        (source.duration_ms > 0.0 && source.position_ms > source.duration_ms) ||
        source.valid_until_ns < source.captured_ns || now_ns < source.captured_ns ||
        (source.play_state != BF_PLAY_PLAYING && source.play_state != BF_PLAY_PAUSED)) {
        return 0;
    }
    if (source.speed_ratio < 0.0) {
        estimate->status = BF_ESTIMATE_UNSUPPORTED_REVERSE;
        return 0;
    }
    if (now_ns > source.valid_until_ns) {
        estimate->status = BF_ESTIMATE_STALE;
        return 0;
    }

    long double position_ms = source.position_ms;
    if (source.play_state == BF_PLAY_PLAYING) {
        // Convert before subtraction: signed nanosecond endpoints may span
        // more than INT64_MAX even though each timestamp itself is valid.
        const long double elapsed_ms =
            (static_cast<long double>(now_ns) -
             static_cast<long double>(source.captured_ns)) / 1000000.0L;
        position_ms += elapsed_ms * static_cast<long double>(source.speed_ratio);
    }
    if (source.duration_ms > 0.0) {
        position_ms = std::min(position_ms, static_cast<long double>(source.duration_ms));
    }
    if (!std::isfinite(position_ms) ||
        position_ms > static_cast<long double>(std::numeric_limits<double>::max())) {
        return 0;
    }
    estimate->position_ms = static_cast<double>(position_ms);
    estimate->status = BF_ESTIMATE_VALID;
    return 0;
}

extern "C" int bf_is_discontinuity(const BFObservation *previous,
                                    const BFObservation *present,
                                    double tolerance_ms) {
    if (previous == nullptr || present == nullptr ||
        !std::isfinite(tolerance_ms) || tolerance_ms < 0.0) {
        return -1;
    }
    BFEstimate predicted{};
    BFEstimate observed{};
    if (bf_estimate_position(previous, present->captured_ns, &predicted) != 0 ||
        bf_estimate_position(present, present->captured_ns, &observed) != 0 ||
        predicted.status != BF_ESTIMATE_VALID || observed.status != BF_ESTIMATE_VALID) {
        return -1;
    }
    return std::fabs(observed.position_ms - predicted.position_ms) > tolerance_ms ? 1 : 0;
}
