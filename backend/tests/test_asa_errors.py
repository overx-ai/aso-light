from app.services.asa.errors import ASAAPIError


def test_asa_api_error_carries_status_and_truncates_long_body():
    err = ASAAPIError("rate limit", status=429, body="x" * 5000)
    s = str(err)
    assert "rate limit" in s
    assert "429" in s
    assert "x" * 5000 not in s  # body truncated


def test_asa_api_error_str_without_status():
    err = ASAAPIError("token request failed", body="bad creds")
    s = str(err)
    assert "token request failed" in s
    assert "bad creds" in s
