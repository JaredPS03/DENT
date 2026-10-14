from app.core.security import verify_meta_signature
from tests.conftest import TEST_APP_SECRET, sign

BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def test_valid_signature_is_accepted() -> None:
    assert verify_meta_signature(BODY, sign(BODY), TEST_APP_SECRET)


def test_uppercase_hex_is_accepted() -> None:
    assert verify_meta_signature(
        BODY, "sha256=" + sign(BODY).removeprefix("sha256=").upper(), TEST_APP_SECRET
    )


def test_signature_of_another_body_is_rejected() -> None:
    assert not verify_meta_signature(BODY + b" ", sign(BODY), TEST_APP_SECRET)


def test_signature_with_another_secret_is_rejected() -> None:
    assert not verify_meta_signature(BODY, sign(BODY, secret="otro-secreto"), TEST_APP_SECRET)


def test_missing_signature_is_rejected() -> None:
    assert not verify_meta_signature(BODY, None, TEST_APP_SECRET)
    assert not verify_meta_signature(BODY, "", TEST_APP_SECRET)


def test_malformed_signatures_are_rejected() -> None:
    digest = sign(BODY).removeprefix("sha256=")
    for header in (
        digest,
        f"sha1={digest}",
        "sha256=",
        "sha256=zz" + digest[2:],
        "sha256=" + digest[:-2],
        "sha256=" + "é" * 64,
    ):
        assert not verify_meta_signature(BODY, header, TEST_APP_SECRET), header


def test_empty_app_secret_never_validates() -> None:
    assert not verify_meta_signature(BODY, sign(BODY, secret=""), "")
