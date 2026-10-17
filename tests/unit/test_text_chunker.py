"""The semantic evidence extractor may not silently drop small changes."""

import difflib

import pytest

from src.utils.text_chunker import TextChunker


def test_identical_content_has_no_changed_regions():
    text = "The service costs $10 per month."
    assert TextChunker.find_changed_regions_with_context(text, text) == ([], [])


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Customers may share data.", "Customers may not share data."),
        ("Refunds within 30 days.", "Refunds within 3 days."),
        ("Uptime is 99.9%.", "Uptime is 99.0%."),
        ("The fee is $100.", "The fee is $1,000."),
    ],
)
def test_short_material_edit_keeps_clause_context_in_long_document(before, after):
    suffix = "\n" + "This paragraph is unchanged and provides background.\n" * 20
    old_text = before + suffix
    new_text = after + suffix
    assert difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).ratio() > 0.95

    old_regions, new_regions = TextChunker.find_changed_regions_with_context(
        old_text, new_text
    )
    assert len(old_regions) == len(new_regions) == 1
    assert before in old_regions[0]
    assert after in new_regions[0]


def test_insertion_and_removal_have_context_on_both_sides():
    old_text = "Data may be shared with partners."
    new_text = "Data may not be shared with partners."
    old_regions, new_regions = TextChunker.find_changed_regions_with_context(
        old_text, new_text, context_chars=25
    )
    assert "Data may" in old_regions[0]
    assert "Data may not" in new_regions[0]
    assert "shared with partners" in old_regions[0]
    assert "shared with partners" in new_regions[0]

    reverted_old, reverted_new = TextChunker.find_changed_regions_with_context(
        new_text, old_text, context_chars=25
    )
    assert "may not" in reverted_old[0]
    assert "may be" in reverted_new[0]


def test_distant_edits_produce_separate_aligned_windows():
    old_text = "Price: $10." + "Z" * 700 + "Refund: 30 days."
    new_text = "Price: $20." + "Z" * 700 + "Refund: 3 days."
    before, after = TextChunker.find_changed_regions_with_context(
        old_text, new_text, context_chars=40
    )
    assert len(before) == len(after) == 2
    assert "$10" in before[0] and "$20" in after[0]
    assert "30 days" in before[1] and "3 days" in after[1]


def test_nearby_edits_merge_without_repeating_context():
    old_text = "Prices are $10 and refunds last 30 days."
    new_text = "Prices are $20 and refunds last 3 days."
    before, after = TextChunker.find_changed_regions_with_context(
        old_text, new_text, context_chars=100
    )
    assert before == [old_text]
    assert after == [new_text]


def test_large_changed_region_is_not_discarded_or_cut_off():
    old_text = "A" * 1200
    new_text = "B" * 1200
    old_regions, new_regions = TextChunker.find_changed_regions_with_context(
        old_text, new_text, context_chars=0
    )
    assert old_regions == [old_text]
    assert new_regions == [new_text]


def test_empty_side_preserved_for_whole_document_addition_or_removal():
    assert TextChunker.find_changed_regions_with_context("", "New text") == (
        [""], ["New text"]
    )
    assert TextChunker.find_changed_regions_with_context("Old text", "") == (
        ["Old text"], [""]
    )


def test_negative_context_rejected():
    with pytest.raises(ValueError, match="context_chars"):
        TextChunker.find_changed_regions_with_context(
            "old", "new", context_chars=-1
        )


def test_single_edit_in_long_repetitive_document_still_keeps_clause():
    suffix = "Terms and conditions remain unchanged. " * 1500
    old_text = "Users may export data. " + suffix
    new_text = "Users may not export data. " + suffix
    old, new = TextChunker.find_changed_regions_with_context(old_text, new_text)
    assert len(old) == len(new) == 1
    assert "Users may export data" in old[0]
    assert "Users may not export data" in new[0]


def test_large_multiple_changes_are_never_silently_filtered():
    old_text = "Price: $10." + "X" * 4000 + "Refund: 30 days."
    new_text = "Price: $20." + "X" * 4000 + "Refund: 3 days."
    before, after = TextChunker.find_changed_regions_with_context(
        old_text, new_text, context_chars=30
    )
    # A bounded fallback can conservatively return one large window.
    assert any("$10" in s and "30 days" in s for s in before)
    assert any("$20" in s and "3 days" in s for s in after)
