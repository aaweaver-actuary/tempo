"""Augment observed maturity facts with an opening baseline."""


def augment_opening_maturity(successful_days, recent_outcomes, baseline_days, baseline_clean):
    outcomes = list(recent_outcomes)
    outcomes.extend(["correct"] * min(baseline_clean, max(0, 2 - len(outcomes))))
    return successful_days + baseline_days, outcomes
