from app.services.asc.clone import _retry_post_once
from app.services.asc.errors import ASCAPIError, ASCNetworkError, ASCRateLimitError


def test_an_apple_5xx_answer_is_worth_one_more_post():
    assert _retry_post_once(ASCAPIError(503, {"errors": []}))


def test_a_network_failure_may_have_landed_the_post_so_it_is_not_retried():
    assert not _retry_post_once(ASCNetworkError(TimeoutError("read timed out")))


def test_a_4xx_is_not_retried():
    assert not _retry_post_once(ASCAPIError(409, {"errors": []}))
    assert not _retry_post_once(ASCRateLimitError({"errors": []}))
