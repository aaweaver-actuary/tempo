"""Existing credential status and registration workflows through owner operations."""
from .errors import DomainError
from .serialization import public


def project_status(operations,clock):
    row=operations.row()
    if not row["registration_id"]:
        return {"generation":row["generation"],"registration_id":None,"status":"credential_missing"}
    if row["expires_at"] and row["expires_at"]<=clock() and row["status"] not in ("rejected","revoked"):
        row["status"]="expired"
    return public(row)


def credential_status(transaction,configuration,clock):
    with transaction() as operations:
        status=operations.status()
    if status["status"] in ("registered","accepted"):
        from tempo_intelligence.credentials import CredentialError, PrivateCredentials
        try:
            material=PrivateCredentials(configuration().credential_file).load()
            if material.registration_id != status["registration_id"]:
                status["status"]="credential_registration_mismatch"
            elif material.expires_at and material.expires_at<=clock():
                status["status"]="expired"
        except CredentialError:
            status["status"]="credential_storage_unavailable"
    return status


def reload_credential(material,expected_generation,transaction,clock):
    with transaction() as operations:
        head=operations.head(expected_generation)
        old=operations.registration(material)
        if old:
            return operations.registration_replay(head,material,old)
        if material.expires_at and material.expires_at<=clock():
            raise DomainError("credential_expired",422)
        generation=head["generation"]+1
        operations.register(material,generation)
        return operations.status()


def revoke_credential(expected_generation,transaction):
    with transaction() as operations:
        head=operations.head(expected_generation)
        if head["registration_id"]:
            operations.revoke(head)
        return operations.status()
