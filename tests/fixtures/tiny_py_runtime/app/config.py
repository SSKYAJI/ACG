from dataclasses import dataclass

__all__ = ["settings", "Settings"]


@dataclass
class Settings:
    title: str = "tiny"


settings = Settings()
