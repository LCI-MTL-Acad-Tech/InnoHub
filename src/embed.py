"""
embed.py — generate and persist document embeddings.
Model is downloaded once to ~/.cache/huggingface/ and never needs network after that.
"""
import numpy as np
from pathlib import Path
import tomllib

with open("config.toml", "rb") as f:
    _CFG = tomllib.load(f)

_MODEL_NAME = _CFG["model"]["name"]
_model = None  # lazy load


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_text(text: str) -> np.ndarray:
    return _get_model().encode(text, normalize_embeddings=True)


def save_embedding(vector: np.ndarray, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(path), vector)


def load_embedding(path: str | Path) -> np.ndarray:
    return np.load(str(path))


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Both vectors assumed to be already L2-normalised."""
    return float(np.dot(a, b))
