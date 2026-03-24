from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./data/cma.db"
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]

    # RAG / Vector store
    CHROMA_PERSIST_DIR: str = "./data/chroma"
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    # LLM provider for chat generation ("openai" or "ollama")
    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "llama3"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # RAG retrieval tuning
    RAG_TOP_K: int = 10
    RAG_SIMILARITY_THRESHOLD: float = 0.3
    RAG_MAX_CONTEXT_TOKENS: int = 3000

    model_config = {"env_prefix": "CMA_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
