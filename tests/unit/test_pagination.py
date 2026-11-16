from hourplan.shared.pagination import Page, page_args


def test_page_numbers_and_window() -> None:
    page = Page(items=[], number=7, size=10, total=95)
    assert page.pages == 10
    assert (page.first_index, page.last_index) == (61, 70)
    assert list(page.window) == [5, 6, 7, 8, 9]
    assert list(Page([], 10, 10, 95).window) == [6, 7, 8, 9, 10]
    assert list(Page([], 1, 10, 30).window) == [1, 2, 3]


def test_an_empty_list_is_one_page() -> None:
    page = Page(items=[], number=1, size=10, total=0)
    assert page.pages == 1
    assert (page.first_index, page.last_index) == (0, 0)


def test_page_args_fall_back_to_valid_values() -> None:
    assert page_args({}) == (1, 10)
    assert page_args({"pagina": "3", "tamano": "50"}) == (3, 50)
    assert page_args({"pagina": "-2", "tamano": "7"}) == (1, 10)
    assert page_args({"pagina": "abc", "tamano": ""}) == (1, 10)
