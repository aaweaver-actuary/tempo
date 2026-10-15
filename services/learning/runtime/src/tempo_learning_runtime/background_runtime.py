"""Optional timing instrumentation does not participate in settlement."""

from contextlib import nullcontext


def training_timing_stage(name):
    return nullcontext()
