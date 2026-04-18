from core.surf import estimate_tokens, format_tokens


def test_estimate_tokens_simple_cases():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2


def test_format_tokens_output():
    assert format_tokens(999) == "999"
    assert format_tokens(2300) == "2.3k"
