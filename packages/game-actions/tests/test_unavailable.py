from tempo_game_actions import UnimplementedActions


def test_stub_is_explicitly_unavailable_without_touching_input():
    class InputMustNotBeUsed:
        def __getattribute__(self, name):
            raise AssertionError("stub must not inspect input or execute work")

    result = UnimplementedActions().execute(InputMustNotBeUsed())
    assert result.status == "not_implemented"
    assert result.component == "actions"
