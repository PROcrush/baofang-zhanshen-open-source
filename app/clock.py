"""One cross-process source clock, including macOS sleep semantics."""
import time


def monotonic_ns():
    if hasattr(time, 'clock_gettime_ns') and hasattr(time, 'CLOCK_MONOTONIC'):
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    return time.monotonic_ns()


CLOCK_BASIS = 'clock_gettime:CLOCK_MONOTONIC' if hasattr(time, 'clock_gettime_ns') and hasattr(time, 'CLOCK_MONOTONIC') else 'time.monotonic_ns'
