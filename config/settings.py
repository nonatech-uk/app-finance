from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Monzo
    monzo_client_id: str = ""
    monzo_client_secret: str = ""
    monzo_redirect_uri: str = "http://localhost:9876/oauth/callback"

    # Postgres
    db_host: str = "192.168.128.9"
    db_port: int = 5432
    db_name: str = "finance"
    db_user: str = "finance"
    db_password: str = ""
    db_sslmode: str = "require"

    # Wise
    wise_api_token: str = ""
    wise_api_base: str = "https://api.wise.com"

    # Monzo API constants
    monzo_auth_url: str = "https://auth.monzo.com/"
    monzo_token_url: str = "https://api.monzo.com/oauth2/token"
    monzo_api_base: str = "https://api.monzo.com"

    # Splitwise
    splitwise_api_key: str = ""
    splitwise_api_base: str = "https://secure.splitwise.com/api/v3.0"
    splitwise_default_group_id: int = 0

    # Xero
    xero_client_id: str = ""
    xero_client_secret: str = ""
    xero_redirect_uri: str = "http://localhost:9877/oauth/callback"
    xero_token_file: str = "xero_tokens.json"
    xero_tenant_id: str = ""
    xero_bank_account_id: str = ""
    xero_default_account_code: str = "400"

    # Anthropic (for LLM categorisation)
    anthropic_api_key: str = ""

    # Receipts
    receipt_storage_path: str = "./receipts"

    # Auth
    auth_enabled: bool = True
    dev_user_email: str = "stu@mees.st"
    cors_origins: list[str] = [
        "https://finance.mees.st",
        "http://localhost:5173",
    ]

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    db_pool_min: int = 2
    db_pool_max: int = 10

    model_config = {
        "env_file": str(Path(__file__).resolve().parent / ".env"),
        "env_file_encoding": "utf-8",
    }

    @property
    def dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password} sslmode={self.db_sslmode}"
        )


settings = Settings()
