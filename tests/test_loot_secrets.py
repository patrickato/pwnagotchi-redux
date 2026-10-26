"""Typed secrets scanner — classify + redact credentials in looted files."""
from redux.loot.secrets import scan_text, scan_files, summarize, SecretFinding


def test_typed_detection_of_common_secrets():
    text = "\n".join([
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
        "github: ghp_" + "a" * 36,
        "slack=xoxb-123456789012-abcdefghijkl",
        "db = postgres://admin:S3cr3tPass@10.0.0.9:5432/prod",
        "GOOGLE=AIza" + "b" * 35,
    ])
    kinds = {f.rule for f in scan_text(text)}
    assert "aws-access-key-id" in kinds and "github-token" in kinds
    assert "slack-token" in kinds and "db-uri-with-creds" in kinds and "google-api-key" in kinds


def test_private_key_and_jwt():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK\n"
    assert any(f.kind == "private-key" for f in scan_text(text))
    jwt = "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123"
    assert any(f.kind == "jwt" for f in scan_text(jwt))


def test_secret_is_redacted_never_emitted():
    text = "aws_access_key_id=AKIAIOSFODNN7EXAMPLE"
    f = scan_text(text)[0]
    assert "AKIAIOSFODNN7EXAMPLE" not in f.redacted      # the real value never appears
    assert f.redacted.startswith("AKIA") and f.redacted.endswith("LE")


def test_password_assignment_medium_confidence():
    f = [x for x in scan_text("password: hunter2hunter") if x.rule == "password-assignment"]
    assert f and f[0].confidence == "medium"


def test_high_confidence_ranked_first():
    text = "password=abcd1234\nkey=AKIAIOSFODNN7EXAMPLE"
    findings = scan_text(text)
    assert findings[0].confidence == "high"             # AWS key outranks a password=


def test_dedupe_same_secret():
    text = "k=AKIAIOSFODNN7EXAMPLE\nk2=AKIAIOSFODNN7EXAMPLE"
    aws = [f for f in scan_text(text) if f.rule == "aws-access-key-id"]
    assert len(aws) == 1


def test_no_false_positive_on_plain_text():
    assert scan_text("the quick brown fox jumps over the lazy dog") == []


def test_scan_files_and_summary(tmp_path):
    p = tmp_path / "config.env"
    p.write_text("API_KEY=abcdef0123456789abcd\nnothing=here\n")
    res = scan_files([str(p)])
    assert res["summary"]["total"] >= 1
    assert str(p) in res["by_file"]
    assert "high-confidence" in res["summary"]["note"] or res["summary"]["total"] >= 1


def test_summarize_counts_by_kind():
    findings = scan_text("key=AKIAIOSFODNN7EXAMPLE\npassword: secretpw")
    s = summarize(findings)
    assert s["total"] == len(findings) and "key" in s["by_kind"]
