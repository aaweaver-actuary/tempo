"""Pure command registration contracts; execution and transactions stay with the gateway."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

Transaction = TypeVar('Transaction')
Original = TypeVar('Original')
Prepared = TypeVar('Prepared')
Result = TypeVar('Result')


@dataclass(frozen=True)
class CommandDescriptor(Generic[Transaction, Original, Prepared, Result]):
    """Correlate one command's original decoding, preparation, and application."""
    name: str
    decode: Callable[[dict[str, Any]], Original]
    prepare: Callable[[Original], Prepared]
    apply: Callable[[Transaction, Prepared], Result]
    after_commit: Callable[[Result], None] | None = None


class CommandRegistry:
    """Own one heterogeneous collection; erasure is limited to this boundary."""
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.preparers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._after_commit: dict[str, Callable[[Any], None]] = {}

    def register(self, descriptor: CommandDescriptor[Transaction, Original, Prepared, Result]) -> None:
        if descriptor.name in self.handlers:
            raise ValueError(f'Duplicate command: {descriptor.name}')

        def prepare(payload: dict[str, Any]) -> Prepared:
            return descriptor.prepare(descriptor.decode(payload))

        def apply(transaction: Any, prepared: Any) -> Result:
            return descriptor.apply(cast(Transaction, transaction), cast(Prepared, prepared))

        self.handlers[descriptor.name] = apply
        self.preparers[descriptor.name] = prepare
        if descriptor.after_commit is not None:
            callback = descriptor.after_commit

            def after_commit(result: Any) -> None:
                callback(cast(Result, result))

            self._after_commit[descriptor.name] = after_commit
        else:
            self._after_commit.pop(descriptor.name, None)

    def clear_after_commit(self, name: str) -> None:
        self._after_commit.pop(name, None)

    def after_commit(self, name: str, result: Any) -> None:
        callback = self._after_commit.get(name)
        if callback is not None:
            callback(result)
