"""
Calls Gemini's free-tier embedding model (gemini-embedding-001) via REST.
No RAG framework needed at this scale (~150-300 pages) - plain numpy
cosine similarity is fast enough and has zero extra dependencies.
"""
import time
import requests
import numpy as np

from . import config

EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-embedding-001:embedContent?key={api_key}"
)


def embed_text(text: str, retries: int = 6) -> np.ndarray:
    """Returns a single embedding vector. Handles free-tier rate limits patiently."""
    text = text[:8000]  # model input limit safety margin
    url = EMBED_URL.format(api_key=config.GEMINI_API_KEY)
    body = {"model": "models/gemini-embedding-001", "content": {"parts": [{"text": text}]}}

    for attempt in range(retries):
        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code == 200:
            values = resp.json()["embedding"]["values"]
            return np.array(values, dtype=np.float32)
        if resp.status_code == 429 or resp.status_code == 503:
            # rate limited - back off politely and wait for the quota window
            wait = min(90, 15 * (2 ** attempt))
            print(f"   rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"Embedding failed after {retries} retries: {resp.text}")


def embed_pages(pages: list, delay_seconds: float = 0.3) -> dict:
    """
    pages: list of dicts with 'url', 'title', 'text'
    Returns: dict {url: embedding_vector}
    """
    embeddings = {}
    for page in pages:
        content_for_embedding = f"{page.get('title', '')}\n\n{page.get('text', '')[:3000]}"
        embeddings[page["url"]] = embed_text(content_for_embedding)
        time.sleep(delay_seconds)
    return embeddings


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def top_similar(target_url: str, embeddings: dict, top_n: int = 5) -> list:
    """Returns list of (url, similarity_score) sorted descending, excluding target itself."""
    target_vec = embeddings[target_url]
    scores = []
    for url, vec in embeddings.items():
        if url == target_url:
            continue
        scores.append((url, cosine_similarity(target_vec, vec)))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]
