"""B2.4: round-1 training languages have evidence-assigned normalizers."""

from pipeline.normalizers import for_language, strip_tones


def test_round1_training_languages_are_registered_not_defaulted():
    expectations = {
        "kinyarwanda": ("generic-norm-v1", False),
        "serer": ("generic-norm-v1", False),
        "pulaar": ("generic-norm-v1", False),
        "yemba": ("tonal-norm-v1", True),
    }
    for language, (version, tonal) in expectations.items():
        norm = for_language(language)
        assert norm.version == version, language
        assert norm.tonal is tonal, language


def test_hook_letters_survive_normalisation():
    """ɓ/ɗ/ƴ/ŋ are letters, not punctuation — pulaar/serer text keeps them."""
    norm = for_language("pulaar")
    assert norm("Ɓe puɗara ƴoo ŋari!") == "ɓe puɗara ƴoo ŋari"


def test_yemba_keeps_tone_marks_and_strip_tones_removes_them():
    norm = for_language("yemba")
    marked = norm("Mbeŋ àzhʉ́")
    assert "̀" in __import__("unicodedata").normalize("NFD", marked)
    assert "̀" not in __import__("unicodedata").normalize("NFD", strip_tones(marked))


def test_intra_word_question_mark_corruption_is_visible_not_silently_eaten():
    """The kallaama defect: '?' replacing hook letters. The normalizer must
    not quietly make 'pu?ara' equal 'puɗara' — the defect stays measurable
    so the exclusion policy (audit finding) can act on it upstream."""
    norm = for_language("pulaar")
    assert norm("pu?ara") != norm("puɗara")
