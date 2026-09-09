import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Server Environment
    ENVIRONMENT: str = "development"
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    DEBUG: bool = True
    ALLOWED_ORIGINS: str = "*"  # Comma-separated or "*"

    def get_allowed_origins(self) -> list[str]:
        if self.ALLOWED_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    # Static API Keys (matching Android client AuthInterceptor.kt)
    API_KEY_ID: str = "vastavik_prod_v1"
    API_KEY_SECRET: str = "super_secret_hmac_production_key_change_me_32char"

    # Enforce HMAC Check? (Can be toggled in dev/tests if needed)
    ENFORCE_HMAC: bool = True

    # JWT Authentication
    JWT_SECRET_KEY: str = "super_jwt_secret_key_change_this_in_production_min_32_bytes"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Default Admin
    ADMIN_EMAIL: str = "admin@vastaviklearning.com"
    ADMIN_PASSWORD: str = "change_this_admin_password_123!"

    # Judge0 External Code Runner
    JUDGE0_URL: str = "http://139.84.172.230:2358"
    JUDGE0_TIMEOUT_SECONDS: float = 10.0
    JUDGE0_AUTH_TOKEN: Optional[str] = None

    # AI API Keys
    MISTRAL_API_KEY: Optional[str] = "WKifALNZPu4xSxRDeNu0vVumvbjvqao1"
    GEMINI_API_KEY: Optional[str] = None
    XKIRO_API_KEY: Optional[str] = "sk-xt-075d80d397e9a363d923b80bd53d65c01463b37b28f60dae"
    XKIRO_BASE_URL: str = "https://api.xkiro.com/v1"

    # Firebase
    FIREBASE_CREDENTIALS_PATH: Optional[str] = "serviceAccountKey.json"
    FIREBASE_CREDENTIALS_JSON: Optional[str] = None
    FIREBASE_CREDENTIALS_BASE64: Optional[str] = None
    FIREBASE_PROJECT_ID: str = "vastavikcomputers"

    # OAuth
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    GOOGLE_CLIENT_ID: Optional[str] = None

    # Payment Gateway (legacy)
    PAYMENT_GATEWAY_KEY: Optional[str] = None
    PAYMENT_GATEWAY_SECRET: Optional[str] = None
    PAYMENT_WEBHOOK_SECRET: Optional[str] = None

    # Razorpay
    RAZORPAY_KEY_ID: Optional[str] = None
    RAZORPAY_KEY_SECRET: Optional[str] = None
    RAZORPAY_WEBHOOK_SECRET: Optional[str] = None

    # Rate Limiting (Requests / minute)
    RATE_LIMIT_AUTH: int = 5
    RATE_LIMIT_AI: int = 10
    RATE_LIMIT_CODE: int = 8
    RATE_LIMIT_GENERAL: int = 120
    RATE_LIMIT_WEBRTC: int = 600
    RATE_LIMIT_CONVERSATIONS: int = 60
    RATE_LIMIT_SOCKET: int = 300

    # Conversations / Socket.IO
    SOCKETIO_CORS_ORIGINS: str = "*"  # comma-separated or "*"
    SOCKETIO_ASYNC_MODE: str = "asgi"
    MAX_CONVERSATION_PARTICIPANTS: int = 100
    MAX_MESSAGE_PAYLOAD_KB: int = 64
    CONVERSATION_HISTORY_PAGE_SIZE: int = 50
    CONVERSATION_HISTORY_MAX_LIMIT: int = 100

    def get_socketio_cors_origins(self) -> list[str]:
        if self.SOCKETIO_CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.SOCKETIO_CORS_ORIGINS.split(",") if o.strip()]

    # Uploads
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # Render Anti-Cold-Start & Keep-Alive Settings
    KEEP_ALIVE_ENABLED: bool = True
    KEEP_ALIVE_INTERVAL_SECONDS: int = 600  # 10 minutes (Render spins down after 15 mins)
    RENDER_EXTERNAL_URL: Optional[str] = "https://vastaviklearning-backend-app.onrender.com"

    @property
    def public_health_url(self) -> str:
        """Returns the public health endpoint URL for keep-alive pings."""
        if self.RENDER_EXTERNAL_URL and self.RENDER_EXTERNAL_URL.strip():
            return f"{self.RENDER_EXTERNAL_URL.strip().rstrip('/')}/health"
        host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if host and host.strip():
            return f"https://{host.strip().rstrip('/')}/health"
        return "https://vastaviklearning-backend-app.onrender.com/health"


settings = Settings()
