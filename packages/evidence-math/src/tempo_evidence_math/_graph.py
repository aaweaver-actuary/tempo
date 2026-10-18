"""Finite supplied graph envelope; no persistent traversal or publication authority."""
from dataclasses import dataclass
from ._json import digest, identifier, number
from ._policy_reach import LearnerPolicy, PolicyReachContext, PolicyReachRoute, _unique
from ._json import immutable_dataclass as dataclass

GRAPH_SNAPSHOT_VERSION = 1


@dataclass(frozen=True, slots=True)
class PublicationRevision:
    publication_id: str
    graph_revision: str
    graph_digest: str

    def __post_init__(self):
        identifier(self.publication_id)
        identifier(self.graph_revision)
        digest(self.graph_digest)


@dataclass(frozen=True, slots=True)
class FinitePolicyGraphSnapshot:
    policy: LearnerPolicy
    context: PolicyReachContext
    publications: tuple[PublicationRevision, ...]
    routes: tuple[PolicyReachRoute, ...]
    horizon_plies: int
    version: int = GRAPH_SNAPSHOT_VERSION

    def __post_init__(self):
        if type(self.version) is not int or self.version != GRAPH_SNAPSHOT_VERSION:
            raise ValueError('Unsupported graph snapshot version')
        if type(self.horizon_plies) is not int or self.horizon_plies < 0:
            raise ValueError('Finite horizon must be a nonnegative integer')
        number(self.horizon_plies)
        publications = _unique(tuple(self.publications), lambda item: item.publication_id)
        routes = tuple(self.routes)
        # Validate every identity before duplicate coalescing.
        for route in routes:
            if route.policy_id != self.policy.policy_id:
                raise ValueError('Foreign policy route')
            if route.publication_id not in {item.publication_id for item in publications}:
                raise ValueError('Missing route publication revision')
            if len(route.moves) > self.horizon_plies:
                raise ValueError('Route exceeds finite supplied horizon')
        object.__setattr__(self, 'publications', publications)
        object.__setattr__(self, 'routes', _unique(routes, lambda item: (item.publication_id, item.line_id)))
