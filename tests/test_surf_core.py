from core.surf import get_context_limit


def test_get_context_limit_known_and_unknown_models():
    assert get_context_limit("llama3.2") == 128000
    assert get_context_limit("some-unknown-model") == 8192
