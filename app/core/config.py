"""应用配置加载、类型校验和运行时必填项校验。"""

from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """表示应用配置缺失或不合法。"""


class Settings(BaseSettings):
    """从环境变量和 .env 文件加载应用配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    log_file_path: Path = Path("./data/logs/app.log")

    database_url: str = ""
    upload_dir: Path = Path("./data/uploads")
    max_upload_size_mb: int = 20
    allowed_file_types: str = "md,pdf,doc,docx,txt"

    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_embedding_model: str = "qwen3.7-text-embedding"
    qwen_embedding_model_flash: str = "qwen3.7-text-embedding-flash"
    qwen_embedding_dimension: int = 1024
    qwen_embedding_batch_size: int = 10
    qwen_embedding_timeout_seconds: int = 60
    qwen_embedding_max_retries: int = 2
    qwen_embedding_retry_delay_seconds: float = 1.0

    gpt_api_key: str = ""
    gpt_base_url: str = "https://heyroute.ai/v1"
    gpt_chat_model: str = "gpt-5.5"
    gpt_temperature: float = 0.3
    gpt_max_tokens: int = 2000
    gpt_timeout_seconds: int = 60
    gpt_max_retries: int = 1
    gpt_retry_delay_seconds: float = 1.0
    gpt_token_char_ratio: int = 4

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    chunk_max_length: int = 1000
    chunk_overlap: int = 100
    retrieval_top_k: int = 5
    retrieval_min_score: float = 0.3
    online_search_enabled: bool = False
    online_search_confidence_threshold: int = 60
    tavily_api_key: str = ""

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """规范化日志级别，便于 Loguru 和标准库共同使用。"""
        return value.upper()

    @property
    def allowed_extensions(self) -> tuple[str, ...]:
        """返回去除空白和点号后的允许文件扩展名。"""
        return tuple(
            extension.strip().lower().lstrip(".")
            for extension in self.allowed_file_types.split(",")
            if extension.strip()
        )

    def validate_required(self) -> None:
        """校验启动所需的密钥和连接配置，不在日志中输出具体值。"""
        required = {
            "DATABASE_URL": self.database_url,
            "QWEN_API_KEY": self.qwen_api_key,
            "GPT_API_KEY": self.gpt_api_key,
            "JWT_SECRET_KEY": self.jwt_secret_key,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ConfigurationError(
                "缺少必填环境变量: " + ", ".join(missing)
            )

    def prepare_directories(self) -> None:
        """创建上传目录和日志目录。"""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        """返回结构化应用日志文件路径。"""
        return self.log_file_path


def load_settings() -> Settings:
    """加载配置并将 Pydantic 错误转换为应用配置错误。"""
    try:
        return Settings()
    except ValidationError as error:
        raise ConfigurationError(f"配置格式错误: {error}") from error


settings = load_settings()
