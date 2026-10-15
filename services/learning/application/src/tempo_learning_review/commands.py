"""Explicit registration; importing the package never changes a registry."""

def register_review_commands(register, *, submit, reconcile):
    register("cards.review", submit)
    register("cards.review.reconcile", reconcile)
