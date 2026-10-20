from tempo_game_classification import UnimplementedClassification


def test_stub_is_explicitly_unavailable_without_touching_input():
    class InputMustNotBeUsed:
        def __getattribute__(self, name):
            raise AssertionError("stub must not inspect input or execute work")

    result = UnimplementedClassification().classify(InputMustNotBeUsed())
    assert result.status == "not_implemented"
    assert result.component == "classification"
