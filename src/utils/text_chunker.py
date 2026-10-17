"""Deterministic, context-preserving evidence extraction for semantic diff."""

from __future__ import annotations

import difflib
from typing import List, Tuple


class TextChunker:
    @staticmethod
    def find_changed_regions_with_context(
        old_text: str,
        new_text: str,
        *,
        context_chars: int = 240,
    ) -> Tuple[List[str], List[str]]:
        """Return aligned diff windows with surrounding clause context.

        Every non-equal opcode must be covered. Overlapping windows are
        merged to avoid duplicate evidence. No similarity threshold or
        per-region truncation is permitted: the caller must reject inputs
        exceeding its provider budget rather than analyzing a prefix.
        """
        if context_chars < 0:
            raise ValueError("context_chars must not be negative")
        if old_text == new_text:
            return [], []

        # Strip the common prefix/suffix before running a character-level
        # matcher. Without this, a tiny edit in a very large, repetitive
        # policy can make SequenceMatcher use quadratic time.
        prefix = 0
        prefix_limit = min(len(old_text), len(new_text))
        while prefix < prefix_limit and old_text[prefix] == new_text[prefix]:
            prefix += 1

        suffix = 0
        while (
            suffix < len(old_text) - prefix
            and suffix < len(new_text) - prefix
            and old_text[-suffix - 1] == new_text[-suffix - 1]
        ):
            suffix += 1

        old_end_boundary = len(old_text) - suffix
        new_end_boundary = len(new_text) - suffix
        old_mid = old_text[prefix:old_end_boundary]
        new_mid = new_text[prefix:new_end_boundary]

        # When a very large middle differs, preserve it as one region
        # rather than running an unbounded quadratic comparison. The model
        # input limit will reject oversized evidence explicitly.
        if len(old_mid) * len(new_mid) > 5_000_000:
            opcodes = [("replace", 0, len(old_mid), 0, len(new_mid))]
        else:
            matcher = difflib.SequenceMatcher(
                None, old_mid, new_mid, autojunk=False
            )
            opcodes = matcher.get_opcodes()

        # Windows are (old_start, old_end, new_start, new_end).
        windows: list[tuple[int, int, int, int]] = []
        for tag, old_start, old_end, new_start, new_end in opcodes:
            if tag == "equal":
                continue
            old_start += prefix
            old_end += prefix
            new_start += prefix
            new_end += prefix
            o1 = max(0, old_start - context_chars)
            o2 = min(len(old_text), old_end + context_chars)
            n1 = max(0, new_start - context_chars)
            n2 = min(len(new_text), new_end + context_chars)

            if windows and o1 <= windows[-1][1] and n1 <= windows[-1][3]:
                p1, p2, q1, q2 = windows[-1]
                windows[-1] = (p1, max(p2, o2), q1, max(q2, n2))
            else:
                windows.append((o1, o2, n1, n2))

        return (
            [old_text[o1:o2] for o1, o2, _, _ in windows],
            [new_text[n1:n2] for _, _, n1, n2 in windows],
        )
