from __future__ import annotations

import pytest

from altas.control_plane.redaction import REDACTED, sanitize_for_storage


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
        "AIza" + "A" * 35,
        "GOCSPX-" + "b" * 24,
        "ya29." + "c" * 30,
        "glpat-" + "d" * 20,
        "glrt-" + "e" * 20,
        "npm_" + "f" * 36,
        "sk_live_" + "g" * 24,
        "rk_test_" + "h" * 24,
        "whsec_" + "i" * 32,
    ],
)
def test_redacts_cloud_and_package_tokens_embedded_in_text(secret: str) -> None:
    sanitized = sanitize_for_storage(f"before({secret})after")

    assert secret not in sanitized
    assert sanitized == f"before({REDACTED})after"


@pytest.mark.parametrize(
    ("label", "secret"),
    [
        ("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE"),
        ("AWS_SECRET_ACCESS_KEY", "aBcd/efghijklmnopqrstuvwxyz1234567890+AB"),
        ("SecretAccessKey", "bCde/fghijklmnopqrstuvwxyz1234567890+ABC"),
        ("AWS_SESSION_TOKEN", "IQoJb3JpZ2luX2VjEOb//////////wEaCXVzLXdlc3QtMg=="),
        ("awsSecurityToken", "FwoGZXIvYXdzEBYaDP//////////AQ=="),
    ],
)
def test_redacts_contextual_aws_credentials_in_assignments(
    label: str, secret: str
) -> None:
    payload = f'prefix "{label}": "{secret}", suffix'

    sanitized = sanitize_for_storage(payload)

    assert secret not in sanitized
    assert REDACTED in sanitized
    assert "prefix" in sanitized
    assert "suffix" in sanitized


def test_redacts_nested_sensitive_credential_fields_without_mutating_input() -> None:
    payload = {
        "safe": "retained",
        "nested": [
            {"awsSecretAccessKey": "aws-secret-material"},
            {"accessKeyId": "AKIAIOSFODNN7EXAMPLE"},
            {"privateKey": "opaque-private-key-material"},
            {"sshPrivateKey": "opaque-private-key-material"},
            {"stripeSecretKey": "opaque-stripe-material"},
        ],
    }

    sanitized = sanitize_for_storage(payload)

    assert sanitized == {
        "safe": "retained",
        "nested": [
            {"awsSecretAccessKey": REDACTED},
            {"accessKeyId": REDACTED},
            {"privateKey": REDACTED},
            {"sshPrivateKey": REDACTED},
            {"stripeSecretKey": REDACTED},
        ],
    }
    assert payload["nested"][0]["awsSecretAccessKey"] == "aws-secret-material"


@pytest.mark.parametrize(
    "header",
    [
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "authorization=Bearer abc.DEF_ghi-jkl~mno+/=",
        '"Authorization": "Basic YWRtaW46c3VwZXItc2VjcmV0"',
        "Proxy-Authorization: Bearer proxy-token-value",
    ],
)
def test_redacts_basic_and_bearer_authorization_payloads(header: str) -> None:
    sanitized = sanitize_for_storage(f"header={header}; retained=true")

    assert REDACTED in sanitized
    assert "retained=true" in sanitized
    for credential in (
        "dXNlcjpwYXNzd29yZA==",
        "abc.DEF_ghi-jkl~mno+/=",
        "YWRtaW46c3VwZXItc2VjcmV0",
        "proxy-token-value",
    ):
        assert credential not in sanitized


def test_authorization_redaction_is_idempotent() -> None:
    sanitized = sanitize_for_storage("Authorization: Basic dXNlcjpwYXNz")

    assert sanitize_for_storage(sanitized) == sanitized


@pytest.mark.parametrize(
    "pem_label",
    [
        "PRIVATE KEY",
        "RSA PRIVATE KEY",
        "EC PRIVATE KEY",
        "OPENSSH PRIVATE KEY",
        "ENCRYPTED PRIVATE KEY",
    ],
)
def test_redacts_complete_pem_private_key_blocks(pem_label: str) -> None:
    block = (
        f"-----BEGIN {pem_label}-----\n"
        "c3VwZXItc2VjcmV0LXByaXZhdGUta2V5\n"
        f"-----END {pem_label}-----"
    )

    sanitized = sanitize_for_storage(f"before\n{block}\nafter")

    assert sanitized == f"before\n{REDACTED}\nafter"


def test_redacts_truncated_pem_private_key_fail_closed() -> None:
    payload = "safe-prefix\n-----BEGIN PRIVATE KEY-----\nc2VjcmV0LWtleQ=="

    sanitized = sanitize_for_storage(payload)

    assert sanitized == f"safe-prefix\n{REDACTED}"
    assert "c2VjcmV0LWtleQ" not in sanitized


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        (
            "postgresql://db_user:p%40ssword@db.internal:5432/app",
            f"postgresql://{REDACTED}@db.internal:5432/app",
        ),
        (
            "postgres+psycopg2://user:secret@localhost/app",
            f"postgres+psycopg2://{REDACTED}@localhost/app",
        ),
        (
            "mysql+pymysql://root:hunter2@mysql.internal/inventory",
            f"mysql+pymysql://{REDACTED}@mysql.internal/inventory",
        ),
        (
            "mongodb+srv://atlas-user:atlas-pass@cluster.example/app?retryWrites=true",
            f"mongodb+srv://{REDACTED}@cluster.example/app?retryWrites=true",
        ),
        (
            "redis://default:redis-pass@cache.internal:6379/0",
            f"redis://{REDACTED}@cache.internal:6379/0",
        ),
        (
            "rediss://token-only@cache.internal:6380/0",
            f"rediss://{REDACTED}@cache.internal:6380/0",
        ),
        (
            "postgresql://user:p@ssword@db.internal:5432/app",
            f"postgresql://{REDACTED}@db.internal:5432/app",
        ),
    ],
)
def test_redacts_database_uri_userinfo_and_preserves_location(
    uri: str, expected: str
) -> None:
    assert sanitize_for_storage(uri) == expected


def test_does_not_redact_database_uri_without_userinfo() -> None:
    uri = "postgresql://db.internal:5432/app?sslmode=require"

    assert sanitize_for_storage(uri) == uri
