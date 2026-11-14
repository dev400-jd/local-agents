"""Lokale Agenten mit Pydantic AI und Ollama."""

import os

# Das Startbanner von Pydantic AI wuerde die Agentenausgabe ueberlagern.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
