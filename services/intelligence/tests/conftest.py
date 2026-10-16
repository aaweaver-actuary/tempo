"""Normal owning tests have no external network access or credential requirement."""
import socket
import pytest


@pytest.fixture(autouse=True)
def external_network_disabled(monkeypatch):
    connect=socket.socket.connect
    connect_ex=socket.socket.connect_ex
    getaddrinfo=socket.getaddrinfo
    def permitted(address):
        if isinstance(address,tuple) and address[0] not in ("127.0.0.1","::1","localhost"):
            raise AssertionError("external_network_disabled")
    def guarded_connect(connection,address):
        permitted(address)
        return connect(connection,address)
    def guarded_connect_ex(connection,address):
        permitted(address)
        return connect_ex(connection,address)
    def guarded_getaddrinfo(host,*arguments,**keywords):
        if host not in ("127.0.0.1","::1","localhost",None):
            raise AssertionError("external_network_disabled")
        return getaddrinfo(host,*arguments,**keywords)
    monkeypatch.setattr(socket.socket,"connect",guarded_connect)
    monkeypatch.setattr(socket.socket,"connect_ex",guarded_connect_ex)
    monkeypatch.setattr(socket,"getaddrinfo",guarded_getaddrinfo)
