from app.tools.redaction import public_value, redact_text


def test_paths_keys_tracebacks_and_environment_secrets_are_redacted(monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "provider-key-without-sk-prefix")
    text = "Paths /home/lei/project/file.py /opt/private/file C:\\Users\\Lei\\secret.txt and sk-proj-secret123456 and provider-key-without-sk-prefix"
    result = redact_text(text)
    for forbidden in ["/home/", "/opt/", "C:", "sk-proj-", "provider-key-without"]:
        assert forbidden not in result
    assert "Traceback" not in redact_text("Traceback (most recent call last): failure")
    assert public_value({"api_key": "short", "nested": {"password": "test"}}) == {
        "api_key": "[redacted]",
        "nested": {"password": "[redacted]"},
    }


def test_source_urls_and_opaque_asset_references_are_not_paths():
    value = "https://arxiv.org/abs/2204.14198v1 asset:ev_1234"
    assert redact_text(value) == value


def test_file_uri_and_unc_path_are_not_public_resources():
    value = r"file:///home/lei/private/file.png \\wsl.localhost\Ubuntu\home\lei\private"
    result = redact_text(value)
    assert "file:///" not in result and "wsl.localhost" not in result
