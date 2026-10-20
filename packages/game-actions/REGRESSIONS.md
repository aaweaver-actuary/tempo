# Stub regression ownership

Regular suite: `python -m pytest tests --import-mode=importlib -q`.

`test_stub_is_explicitly_unavailable_without_touching_input` proves that the stub returns `not_implemented` and never inspects or executes the supplied request. This is not classification/action implementation proof.
