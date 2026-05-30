"""Evidence Linker — evidence_text → chunk_id semantic matching.

Replaces the naive substring matching in agents.py._dict_to_review()
with LLM-based semantic matching (or improved keyword fallback).

The problem: when an LLM agent writes "the team decided to skip real-time ASR"
as evidence_text, the original chunk may say "We should do text import first,
not real-time ASR." — substring matching fails because the paraphrase doesn't
contain a long enough shared prefix.

This linker uses the provider (when available) to do a single LLM call that
maps all evidence_text strings to their most likely chunk_ids in one shot.
"""

from __future__ import annotations

import json
import logging
import re

from roundtable.models import TranscriptChunk
from roundtable.providers import BaseLLMProvider

logger = logging.getLogger("roundtable.linker")


class EvidenceLinker:
    """Maps evidence_text references to chunk_ids using semantic matching.

    When a provider is available, uses LLM to match each evidence_text
    to the most likely chunk. Without provider, uses improved keyword
    overlap as fallback.
    """

    def __init__(self, provider: BaseLLMProvider | None = None):
        self.provider = provider

    async def link(
        self,
        evidence_texts: list[str],
        chunks: list[TranscriptChunk],
    ) -> list[list[str]]:
        """Link each evidence_text to its matching chunk_ids.

        Args:
            evidence_texts: List of evidence_text strings from agent claims
            chunks: All TranscriptChunks from the evidence packet

        Returns:
            List of lists — evidence_texts[i] → [chunk_id, ...]
        """
        if not evidence_texts:
            return []

        if self.provider is not None:
            try:
                return await self._link_with_llm(evidence_texts, chunks)
            except Exception:
                logger.warning("LLM evidence linking failed, falling back to keywords", exc_info=True)

        return self._link_with_keywords(evidence_texts, chunks)

    def link_sync(
        self,
        evidence_texts: list[str],
        chunks: list[TranscriptChunk],
    ) -> list[list[str]]:
        """Synchronous wrapper (uses keyword fallback only)."""
        return self._link_with_keywords(evidence_texts, chunks)

    # ── LLM-based matching ──

    async def _link_with_llm(
        self,
        evidence_texts: list[str],
        chunks: list[TranscriptChunk],
    ) -> list[list[str]]:
        """Use LLM to match evidence_text strings to chunks in one call."""
        assert self.provider is not None

        # Build a concise chunk index for the LLM
        chunk_index = ""
        for c in chunks:
            chunk_index += f"[{c.chunk_id}] (speaker={c.speaker}) {c.text}\n"

        evidence_index = ""
        for i, t in enumerate(evidence_texts):
            evidence_index += f"[E{i}] {t}\n"

        system = (
            "You are an evidence linker. Your job is to match each evidence "
            "reference to the transcript chunk(s) it refers to.\n\n"
            "Rules:\n"
            "- Match based on semantic meaning, not exact text.\n"
            "- If evidence_text is a paraphrase or summary, match to the chunk "
            "that contains the closest content.\n"
            "- Return an empty array for evidence that doesn't match any chunk.\n"
            "- Output ONLY valid JSON, no explanation."
        )

        user = (
            "Transcript chunks:\n"
            f"{chunk_index}\n"
            "Evidence references to link:\n"
            f"{evidence_index}\n"
            "Return JSON: {\"matches\": [{\"evidence_index\": 0, \"chunk_ids\": [\"t_000000\", \"t_000001\"]}, ...]}"
        )

        raw = await self.provider.chat(
            system_prompt=system,
            user_message=user,
            max_tokens=1000,
            temperature=0.1,  # Low temperature for reliable matching
        )

        return self._parse_llm_response(raw, len(evidence_texts))

    def _parse_llm_response(
        self, raw: str, evidence_count: int
    ) -> list[list[str]]:
        """Parse the LLM's JSON response into chunk_id lists."""
        result: list[list[str]] = [[] for _ in range(evidence_count)]

        # Extract JSON
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                return result
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return result

        if not isinstance(data, dict):
            return result

        for m in data.get("matches", []):
            idx = m.get("evidence_index", -1)
            if 0 <= idx < evidence_count:
                result[idx] = m.get("chunk_ids", [])

        return result

    # ── Keyword-based fallback ──

    def _link_with_keywords(
        self,
        evidence_texts: list[str],
        chunks: list[TranscriptChunk],
    ) -> list[list[str]]:
        """Improved keyword overlap matching.

        Better than the original prefix match: tokenizes both sides
        and computes Jaccard-like overlap on meaningful tokens.
        """
        chunk_text_map = {c.chunk_id: c.text for c in chunks}

        results: list[list[str]] = []
        for ev_text in evidence_texts:
            matched = self._find_best_chunks(ev_text, chunk_text_map)
            results.append(matched)

        return results

    def _find_best_chunks(
        self, evidence_text: str, chunk_text_map: dict[str, str]
    ) -> list[str]:
        """Find the best matching chunk_ids for a single evidence_text."""
        ev_tokens = set(self._tokenize(evidence_text))
        if not ev_tokens:
            return []

        best_score = 0.0
        best_ids: list[str] = []

        for cid, ctext in chunk_text_map.items():
            c_tokens = set(self._tokenize(ctext))
            if not c_tokens:
                continue

            overlap = ev_tokens & c_tokens
            score = len(overlap) / max(len(ev_tokens), 1)

            if score > best_score and score >= 0.2:  # Minimum 20% token overlap
                best_score = score
                best_ids = [cid]
            elif score == best_score and score >= 0.2:
                best_ids.append(cid)

        return best_ids

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize text into meaningful word tokens.

        For Chinese text: split on common delimiters and extract
        2-4 character n-grams. For English: split on whitespace.
        """
        # Normalize
        text = text.lower().strip()

        # Check if predominantly Chinese
        cjk_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if cjk_chars > len(text) * 0.3:
            # Chinese-dominant: use character bigrams + meaningful words
            tokens = []
            # Extract 2-gram character sequences
            for i in range(len(text) - 1):
                bigram = text[i : i + 2]
                if all("\u4e00" <= c <= "\u9fff" or c.isalnum() for c in bigram):
                    tokens.append(bigram)
            # Also include individual meaningful characters
            meaningful = [
                c for c in text
                if "\u4e00" <= c <= "\u9fff" or c.isalnum()
            ]
            tokens.extend(meaningful)
            # Deduplicate while preserving order
            seen = set()
            result = []
            for t in tokens:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
            return result
        else:
            # English-dominant: split on non-alphanumeric
            return [
                t for t in re.split(r"[^a-z0-9]+", text)
                if len(t) >= 2
            ]
