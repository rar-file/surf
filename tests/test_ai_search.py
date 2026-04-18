from core.ai_search import SearchResult, PageContent, AISearch


def test_search_result_dataclass_fields():
    result = SearchResult(title="Example", url="https://example.com", snippet="hello", position=1)
    assert result.title == "Example"
    assert result.url == "https://example.com"
    assert result.snippet == "hello"
    assert result.position == 1


def test_page_content_dataclass_fields():
    page = PageContent(url="https://example.com", title="Example", text="Body", success=True)
    assert page.success is True
    assert page.error is None
    assert page.text == "Body"


def test_clean_text_removes_junk_and_normalizes_whitespace():
    search = AISearch()
    dirty = """
    menu

    Hello   world


    advertisement
    This is a test.

    skip to content
    """
    cleaned = search._clean_text(dirty)
    assert "menu" not in cleaned.lower()
    assert "advertisement" not in cleaned.lower()
    assert "skip to content" not in cleaned.lower()
    assert "Hello world" in cleaned
    assert "This is a test." in cleaned
