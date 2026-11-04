"""Content final-generation commands; importing this module opens no resources."""
from dataclasses import dataclass
from typing import Literal

from .domain import ContentError


class FinalGenerationConflict(ContentError):
    def __init__(self, code, message, reservation_id, epoch):
        super().__init__(code, message)
        self.reservation_id = reservation_id
        self.epoch = epoch


@dataclass(frozen=True)
class ReserveFinal:
    transfer_id: str
    source_id: str
    publication_id: str
    generation: int
    approved_operation_id: str


class ContentFinalReservationHandle:
    """Opaque, service-instance-local reference. Recover issues a fresh handle."""
    __slots__ = ()

    def __new__(cls):
        raise TypeError('Content final handles are issued by the owner')

    def __reduce__(self):
        raise TypeError('Content final handles are not serializable')


@dataclass(frozen=True)
class CaptureFinal:
    handle: ContentFinalReservationHandle


@dataclass(frozen=True)
class RecoverFinal:
    transfer_id: str
    source_id: str


@dataclass(frozen=True)
class ResolveFinal:
    handle: ContentFinalReservationHandle
    intent: Literal['accept', 'abort']


@dataclass(frozen=True)
class FinalGenerationResult:
    status: str
    code: str
    reservation_id: str | None = None
    epoch: int | None = None
    handle: ContentFinalReservationHandle | None = None
    capture: object | None = None
    state: str | None = None


class ContentFinalGeneration:
    def __init__(self, store, target_disposition_reader=None):
        self._store = store
        self._target = target_disposition_reader
        self._handles = {}

    def _handle(self, row):
        handle = object.__new__(ContentFinalReservationHandle)
        self._handles[handle] = (row['source_id'], row['transfer_id'], row['reservation_id'], row['epoch'])
        return handle

    def _resolve_handle(self, handle):
        if type(handle) is not ContentFinalReservationHandle or handle not in self._handles:
            raise ContentError('content_final_handle_invalid', 'Handle was not issued by this service')
        source, transfer, reservation, epoch = self._handles[handle]
        row = self._store.recover(transfer, source)
        if not row or row['reservation_id'] != reservation or row['epoch'] != epoch or row['state'] not in ('reserved', 'captured', 'recovery-held'):
            raise ContentError('content_final_handle_invalid', 'Reservation is no longer active')
        return row

    def execute(self, command):
        row = None
        handle = None
        try:
            if isinstance(command, ReserveFinal):
                if any(type(x) is not str or not x for x in (command.transfer_id, command.source_id, command.publication_id, command.approved_operation_id)) or type(command.generation) is not int or command.generation < 1:
                    raise ContentError('content_final_handle_invalid', 'Invalid reservation command')
                row = self._store.reserve(command)
                if row['state'] in ('accepted','aborted'):
                    return FinalGenerationResult(row['state'].upper(), 'content_final_' + row['state'], row['reservation_id'], row['epoch'], state=row['state'])
                handle = self._handle(row)
                if row['state'] == 'captured':
                    return self._store.capture(row, handle)
                if row['state'] == 'recovery-held':
                    return FinalGenerationResult('BLOCK','content_final_recovery_required', row['reservation_id'], row['epoch'], handle, state=row['state'])
                return FinalGenerationResult('RESERVED', 'content_final_reserved', row['reservation_id'], row['epoch'], handle, state=row['state'])
            if isinstance(command, RecoverFinal):
                if any(type(x) is not str or not x for x in (command.transfer_id, command.source_id)):
                    raise ContentError('content_final_handle_invalid', 'Invalid recovery command')
                row = self._store.recover(command.transfer_id, command.source_id)
                if not row:
                    raise ContentError('content_final_handle_invalid', 'Reservation is unavailable')
                if row['state'] in ('reserved', 'captured', 'recovery-held'):
                    handle = self._handle(row)
                    if row['state'] == 'captured':
                        return self._store.capture(row, handle)
                    if row['state'] == 'recovery-held':
                        return FinalGenerationResult('BLOCK','content_final_recovery_required', row['reservation_id'], row['epoch'], handle, state=row['state'])
                    return FinalGenerationResult('RESERVED', 'content_final_recovered', row['reservation_id'], row['epoch'], handle, state=row['state'])
                return FinalGenerationResult(row['state'].upper(), 'content_final_' + row['state'], row['reservation_id'], row['epoch'], state=row['state'])
            if isinstance(command, CaptureFinal):
                handle = command.handle
                row = self._resolve_handle(command.handle)
                return self._store.capture(row, command.handle)
            if isinstance(command, ResolveFinal):
                handle = command.handle
                row = self._resolve_handle(command.handle)
                if command.intent not in ('accept', 'abort'):
                    raise ContentError('content_final_handle_invalid', 'Unknown disposition intent')
                return self._store.resolve(row, command.handle, command.intent, self._target)
            raise ContentError('content_final_handle_invalid', 'Unknown final generation command')
        except ContentError as error:
            return FinalGenerationResult('BLOCK', error.code, row['reservation_id'] if row else getattr(error,'reservation_id',None), row['epoch'] if row else getattr(error,'epoch',None), handle if row else None, state=row['state'] if row else None)


@dataclass(frozen=True)
class ContentFinalCaptureEvidence:
    contract_version: str
    database_authority_id: str
    workspace_id: str
    source_id: str
    transfer_id: str
    reservation_id: str
    epoch: int
    capture_id: str
    certificate_digest: str
    export_sha256: str
    inventory_sha256: str
    proof_sha256: str
    state: Literal['captured']
    certificate_bytes: bytes
    export_bytes: bytes
    inventory_bytes: bytes
    proof_bytes: bytes


@dataclass(frozen=True)
class AcceptedTarget:
    authority_id: str
    allocation_id: str
    allocation_generation: int
    transfer_id: str
    certificate_digest: str
    export_sha256: str
    legacy_watermark: str
    activation_receipt: str
    checkpoint: str
    receipt_digest: str


@dataclass(frozen=True)
class ProvenNoTargetWrite:
    authority_id: str
    allocation_id: str
    transfer_id: str
    closed_disposition: str


@dataclass(frozen=True)
class UnknownTarget:
    reason: str
