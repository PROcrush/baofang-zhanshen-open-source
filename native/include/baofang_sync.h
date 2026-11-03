#ifndef BAOFANG_SYNC_H
#define BAOFANG_SYNC_H

#include <stdint.h>

#if defined(_WIN32)
#if defined(BF_SYNC_BUILD)
#define BF_SYNC_API __declspec(dllexport)
#else
#define BF_SYNC_API __declspec(dllimport)
#endif
#else
#define BF_SYNC_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum BFPlayState {
    BF_PLAY_UNKNOWN = 0,
    BF_PLAY_PLAYING = 1,
    BF_PLAY_PAUSED = 2
};

enum BFEstimateStatus {
    BF_ESTIMATE_VALID = 0,
    BF_ESTIMATE_STALE = 1,
    BF_ESTIMATE_INVALID = 2,
    BF_ESTIMATE_UNSUPPORTED_REVERSE = 3
};

/* Times must use the same monotonic clock domain. A duration of zero is unknown.
 * This is an observation of source-file time, not an audio/output latency time.
 * Keep field order and types stable: Python and other callers use this C ABI.
 */
typedef struct BFObservation {
    double position_ms;
    double speed_ratio;
    double duration_ms;
    int64_t captured_ns;
    int64_t valid_until_ns;
    int32_t play_state;
} BFObservation;

typedef struct BFEstimate {
    double position_ms;
    int32_t status;
} BFEstimate;

/* Returns 0 after classifying an observation; -1 for a null pointer.
 * position_ms is meaningful only when status is BF_ESTIMATE_VALID.
 * TTL endpoints are inclusive. Invalid observations are never extrapolated.
 * Paused observations freeze, regardless of a positive speed ratio.
 * Negative speed is explicitly unsupported for every play state.
 */
BF_SYNC_API int bf_estimate_position(const BFObservation *observation,
                                     int64_t now_ns,
                                     BFEstimate *estimate);

/* Returns 1 if the present anchor differs from the prior projected position by
 * more than tolerance_ms, 0 if continuous, or -1 if comparison is unavailable.
 * Both snapshots must describe the same source version. The present capture
 * must fall inside the previous TTL; stale, reversed, or malformed snapshots
 * do not imply a cue. The present snapshot is checked at its own capture time.
 */
BF_SYNC_API int bf_is_discontinuity(const BFObservation *previous,
                                    const BFObservation *present,
                                    double tolerance_ms);

#ifdef __cplusplus
}
#endif

#endif
