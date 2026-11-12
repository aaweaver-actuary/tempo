# Study public capabilities

The retained [captured_tactic.py](captured_tactic.py) capability accepts immutable
validated Tactics facts. [Tactics capture composition](../../product_composition/tactic_capture.py)
binds the borrowed writer: daily admission locking precedes capture locking and
replay; Study owns card/queue admission before the capture receipt is saved on
that writer. The protocol imports no concrete database connection.

The former `game_priority.py` and `next_card.py` interfaces and their
`queue.prepare_next` feedback preparation assembly are retired. They are not
current public APIs. Existing accepted records and journals must not be deleted
or declared settled merely because a producer/interface is absent.

Current queue issuance is coordinated through
[Study composition](../../product_composition/study.py) and
[next_exercise.py](../next_exercise.py). Source navigation is not a native replay
or assembled saved-review-to-playable-next receipt.
