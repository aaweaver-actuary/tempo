# Study opening policies

The retained supplied-input kernels are [capacity_frontier.py](capacity_frontier.py)
and [frozen_math.py](frozen_math.py). Capacity computes bounded proposals and
priority-frontier facts; frozen mathematics computes represented-path/coverage
facts with [shared opening identities](../../chess/opening_segments.py).
They do not own database transactions, provider lifecycle or queue writes.

The former `preparedness.py` and `policy_reach.py` modules and move-probability
contract are absent. Historical extraction/parity counts for those modules are
not current acceptance. [Frozen-order policy](../frozen_order/README.md) remains
a separate owner; this directory does not consolidate it or qualify deployment.
