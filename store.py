"""
store.py - opens the vector database. Shared by ingest.py, ask.py and app.py.

A vector database stores each chunk of text together with its "embedding":
a list of numbers (here, 384 of them) that captures what the text MEANS.
Texts about similar things get similar numbers, so "vacation days" and
"paid time off" land close together even though they share no words.
"""
import hashlib
import os

import chromadb
from chromadb.utils import embedding_functions

import config


class _FakeEmbedder(embedding_functions.EmbeddingFunction):
    """Test-only stand-in used when RAG_FAKE_EMBED=1.

    It turns words into numbers with a hash, so it matches on shared words
    only (no real understanding of meaning). Useful to check the plumbing
    works without downloading the real model. Do not use it for real answers.
    """

    def __init__(self):
        pass

    def __call__(self, input):
        vectors = []
        for text in input:
            v = [0.0] * 256
            for word in text.lower().split():
                h = int(hashlib.md5(word.strip(".,:;!?()").encode()).hexdigest(), 16)
                v[h % 256] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            vectors.append([x / norm for x in v])
        return vectors

    @staticmethod
    def name():
        return "fake-hash-embedder"


def get_embedder():
    if os.environ.get("RAG_FAKE_EMBED") == "1":
        return _FakeEmbedder()
    # Real embedder: all-MiniLM-L6-v2, run locally through ONNX.
    return embedding_functions.DefaultEmbeddingFunction()


def get_collection(reset: bool = False):
    """Open (or create) the collection that holds our chunks."""
    client = chromadb.PersistentClient(path=config.DB_DIR)
    if reset:
        try:
            client.delete_collection(config.COLLECTION_NAME)
        except Exception:
            pass  # nothing to delete on a first run
    return client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        embedding_function=get_embedder(),
        # "cosine" compares the direction of two vectors - the standard
        # choice for text similarity.
        metadata={"hnsw:space": "cosine"},
    )
