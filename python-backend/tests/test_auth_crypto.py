from app.api.auth import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse battery staple", encoded)


def test_wrong_password_rejected():
    encoded = hash_password("correct horse battery staple")
    assert not verify_password("wrong password", encoded)


def test_salts_are_unique():
    assert hash_password("same") != hash_password("same")


def test_malformed_encoded_rejected():
    assert not verify_password("anything", "not-a-valid-hash")
    assert not verify_password("anything", "")
    assert not verify_password("anything", "pbkdf2_sha256$abc$salt$hash")
    assert not verify_password("anything", "plaintext$password$salt$hash$extra")
