"""Small dependency-free BM25 index over embedding chunks."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from app.services.retrieval.official_query_tokenizer import TOKENIZER_VERSION, tokenize_official_query

INDEX_VERSION = "bm25_index_v2"


class BM25Index:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        title_weight: float = 2.0,
    ):
        if k1 <= 0:
            raise ValueError("BM25 k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("BM25 b must be between 0 and 1")
        if title_weight <= 0:
            raise ValueError("BM25 title_weight must be positive")
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.title_weight = title_weight
        self.term_frequencies: list[Counter[str]] = []
        self.document_lengths: list[float] = []
        self.postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        document_frequency: Counter[str] = Counter()

        for doc_index, document in enumerate(documents):
            metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
            title = str(document.get("title") or metadata.get("title") or "")
            section_path = document.get("section_path") or metadata.get("section_path") or []
            if isinstance(section_path, list):
                section_path_text = " ".join(str(part) for part in section_path)
            else:
                section_path_text = str(section_path)
            heading = str(document.get("section_heading") or metadata.get("section_heading") or "")
            body = str(document.get("chunk_text") or document.get("text") or "")
            frequencies = Counter(tokenize_official_query(body))
            frequencies.update(tokenize_official_query(heading))
            frequencies.update(tokenize_official_query(section_path_text))
            for term, frequency in Counter(tokenize_official_query(title)).items():
                frequencies[term] += frequency * self.title_weight
            self.term_frequencies.append(frequencies)
            self.document_lengths.append(sum(frequencies.values()))
            document_frequency.update(frequencies.keys())
            for term, frequency in frequencies.items():
                self.postings[term].append((doc_index, frequency))

        self.document_count = len(documents)
        self.average_length = sum(self.document_lengths) / max(self.document_count, 1)
        self.idf = {
            term: math.log(1.0 + (self.document_count - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    def search(self, query: str, top_k: int = 30) -> list[dict[str, Any]]:
        query_terms = tokenize_official_query(query)
        if not query_terms or not self.documents:
            return []

        scores: dict[int, float] = defaultdict(float)
        query_counts = Counter(query_terms)
        for term, query_frequency in query_counts.items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            for doc_index, term_frequency in self.postings.get(term, []):
                length = self.document_lengths[doc_index]
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * length / max(self.average_length, 1.0)
                )
                scores[doc_index] += query_frequency * idf * (
                    term_frequency * (self.k1 + 1.0) / denominator
                )

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:max(0, top_k)]
        results: list[dict[str, Any]] = []
        for rank, (doc_index, score) in enumerate(ranked, start=1):
            result = dict(self.documents[doc_index])
            result["bm25_score"] = round(score, 6)
            result["bm25_rank"] = rank
            results.append(result)
        return results

    def info(self) -> dict[str, Any]:
        return {
            "index_version": INDEX_VERSION,
            "tokenizer_version": TOKENIZER_VERSION,
            "chunk_count": self.document_count,
            "k1": self.k1,
            "b": self.b,
            "title_weight": self.title_weight,
        }
