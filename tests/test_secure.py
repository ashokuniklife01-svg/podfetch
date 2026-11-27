import pytest

from secure import TokenBox, redact


def test_roundtrip_and_wrong_key():
    a = TokenBox("one")
    enc = a.encrypt("ya29.secret-token")
    assert enc and "secret" not in enc
    assert a.decrypt(enc) == "ya29.secret-token"
    assert TokenBox("two").decrypt(enc) is None
    assert a.encrypt(None) is None and a.decrypt(None) is None
    assert a.decrypt("garbage") is None
    with pytest.raises(ValueError):
        TokenBox("")


def test_redact():
    s = redact("GET /x?access_token=EAAB123&code=abc {\"refresh_token\": \"1//xyz\"} Bearer ya29.AAA")
    assert "EAAB123" not in s and "abc" not in s and "1//xyz" not in s and "ya29.AAA" not in s
    assert redact(None) == ""
    assert len(redact("x" * 5000)) == 600
