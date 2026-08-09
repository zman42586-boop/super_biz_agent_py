from app.config import Settings


def test_smtp_env_is_parsed_without_real_credentials(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2465")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASS", "test-password")
    monkeypatch.setenv("SMTP_TO", "receiver@example.com")

    settings = Settings(_env_file=None)

    assert settings.smtp_host == "smtp.example.com"
    assert settings.smtp_port == 2465
    assert settings.smtp_user == "sender@example.com"
    assert settings.smtp_to == "receiver@example.com"

