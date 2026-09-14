from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = PROJECT_ROOT / "data"
FLOAT_SUMMARIES_ARCHIVE_PATH: Path = DATA_DIR / "float_summaries_archive.json"

DEFAULT_EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_GENERATOR = "openrouter:openai/gpt-5-nano"
DEFAULT_EVALUATOR = "openrouter:openai/gpt-5.6-luna"
MATH_COREF_MODEL = "kblw/mathcoref"
