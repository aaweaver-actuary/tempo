import hashlib


def stable_key(*args) -> str:
    m = hashlib.sha256()
    for arg in args:
        m.update(str(arg).encode())
    return m.hexdigest()
