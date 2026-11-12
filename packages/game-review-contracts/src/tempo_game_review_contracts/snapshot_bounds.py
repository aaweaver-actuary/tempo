"""Public bounded-snapshot refusal diagnostics."""

class RepertoireSnapshotBoundExceeded(ValueError):
    """A proved budget refusal containing only public numeric diagnostics."""

    def __init__(self, *, stage, sample_complete, violations):
        dimensions = {
            "source_rows",
            "card_rows",
            "membership_rows",
            "graph_rows",
            "row_bytes",
            "estimated_snapshot_bytes",
            "snapshot_bytes",
            "authored_moves",
        }
        observations = {"exact", "lower_bound", "conservative_estimate"}
        if stage not in {"read", "projection"} or type(sample_complete) is not bool:
            raise ValueError("invalid_repertoire_snapshot_bound_diagnostic")
        copied = []
        for violation in violations:
            if (
                set(violation) != {"dimension", "observed", "limit", "observation"}
                or violation["dimension"] not in dimensions
                or violation["observation"] not in observations
                or type(violation["observed"]) is not int
                or type(violation["limit"]) is not int
                or violation["limit"] < 0
                or violation["observed"] <= violation["limit"]
            ):
                raise ValueError("invalid_repertoire_snapshot_bound_diagnostic")
            copied.append(
                tuple(
                    violation[key]
                    for key in ("dimension", "observed", "limit", "observation")
                )
            )
        if not copied:
            raise ValueError("invalid_repertoire_snapshot_bound_diagnostic")
        self._stage, self._sample_complete, self._violations = (
            stage,
            sample_complete,
            tuple(copied),
        )
        super().__init__("repertoire_snapshot_exceeds_bound")

    def diagnostic(self):
        return {
            "code": "repertoire_snapshot_exceeds_bound",
            "stage": self._stage,
            "sample_complete": self._sample_complete,
            "violations": [
                dict(zip(("dimension", "observed", "limit", "observation"), values))
                for values in self._violations
            ],
        }
