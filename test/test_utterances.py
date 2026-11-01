"""Logical caption regressions; no ASR/MT service or audio device required."""
from __future__ import annotations

import re

try:
    from simulcast.pipeline.utterances import UtteranceAssembler, WindowDecode
except ModuleNotFoundError as exc:
    raise AssertionError("logical utterance assembler is not implemented") from exc


def test_forced_boundaries_hold_long_last_sentence():
    for reason in ("hard", "hard_pause"):
        assembler = UtteranceAssembler()
        first = assembler.ingest(WindowDecode("w1", "file", 0, 10,
            "I do not think the proposal you mentioned yesterday.", True, reason))
        assert len(first) == 1 and not first[0].final
        second = assembler.ingest(WindowDecode("w2", "file", 8.5, 16,
            "mentioned yesterday was ready for approval.", True, "silence", 1.5))
        assert [(u.text, u.final) for u in second] == [
            ("I do not think the proposal you mentioned yesterday was ready for approval.", True)]
        assert first[0].id == second[0].id
        assert (second[0].t0, second[0].t1) == (0, 16)
        assert assembler.flush() == []


def test_drafts_replace_and_final_keeps_id():
    assembler = UtteranceAssembler()
    first = assembler.ingest(WindowDecode("w", "mic", 0, 1, "We should go"))
    second = assembler.ingest(WindowDecode("w", "mic", 0, 2, "We should stay"))
    final = assembler.ingest(WindowDecode("w", "mic", 0, 3, "We should stay here.", True, "silence"))
    assert [u.text for u in first + second + final] == ["We should go", "We should stay", "We should stay here."]
    assert first[0].id == second[0].id == final[0].id
    assert final[0].final
    assert assembler.ingest(WindowDecode("w", "mic", 0, 2, "late draft")) == []


def test_stable_interior_commits_without_repeating_prefix():
    assembler = UtteranceAssembler()
    first = assembler.ingest(WindowDecode("w", "file", 0, 3, "First sentence. We could"))
    second = assembler.ingest(WindowDecode("w", "file", 0, 4, "First sentence. We could leave"))
    assert [(u.text, u.final) for u in second] == [("First sentence.", True), ("We could leave", False)]
    assert first[0].id == second[0].id
    final = assembler.ingest(WindowDecode("w", "file", 0, 5, "First sentence. We could leave now.", True, "silence"))
    assert [(u.text, u.final) for u in final] == [("We could leave now.", True)]
    assert second[1].id == final[0].id


def test_cjk_overlap_and_intentional_repeats():
    for left, right, expected in (
        ("今天我们讨论这个问题。", "这个问题需要大家一起解决。", "今天我们讨论这个问题需要大家一起解决。"),
        ("昨日の会議で決めたことは", "決めたことは明日実行します。", "昨日の会議で決めたことは明日実行します。"),
    ):
        assembler = UtteranceAssembler()
        draft = assembler.ingest(WindowDecode("w1", "file", 0, 10, left, True, "hard"))
        final = assembler.ingest(WindowDecode("w2", "file", 8.5, 14, right, True, "silence", 1.5))
        assert [(u.text, u.final) for u in final] == [(expected, True)]
        assert final[0].id == draft[0].id
    assembler = UtteranceAssembler()
    assembler.ingest(WindowDecode("w1", "file", 0, 10, "Go go", True, "hard"))
    result = assembler.ingest(WindowDecode("w2", "file", 10, 13, "go go now.", True, "silence"))
    assert [u.text for u in result] == ["Go go go go now."]


def test_uncertain_alignment_retains_words_and_can_be_repaired():
    assembler = UtteranceAssembler()
    first = assembler.ingest(WindowDecode("w1", "file", 0, 10, "We need the blue folder", True, "hard"))
    uncertain = assembler.ingest(WindowDecode("w2", "file", 8.5, 15,
        "green folder for tomorrow.", True, "hard", 1.5))
    assert [(u.text, u.final, u.reason) for u in uncertain] == [
        ("We need the blue folder green folder for tomorrow.", False, "alignment_uncertain")]
    assert uncertain[0].id == first[0].id
    repaired = assembler.repair("file", "We need the green folder for tomorrow.", 0, 15)
    assert [(u.text, u.final, u.reason) for u in repaired] == [
        ("We need the green folder for tomorrow.", False, "alignment_repaired")]
    assert repaired[0].id == first[0].id
    assert assembler.flush()[0].text == "We need the green folder for tomorrow."


def test_uncertain_silence_explicitly_degrades_and_repeated_overlap_is_not_guessed():
    assembler = UtteranceAssembler()
    assembler.ingest(WindowDecode("a", "file", 0, 10, "go go", True, "hard"))
    result = assembler.ingest(WindowDecode("b", "file", 8.5, 12, "go go now", True, "silence", 1.5))
    assert [(u.text, u.final, u.reason) for u in result] == [("go go go go now", True, "alignment_degraded")]
    assembler = UtteranceAssembler()
    assembler.ingest(WindowDecode("a", "file", 0, 10, "Thank you.", True, "hard"))
    repeated = assembler.ingest(WindowDecode("b", "file", 8.5, 12, "Thank you.", True, "silence", 1.5))
    assert [(u.text, u.reason) for u in repeated] == [("Thank you Thank you.", "alignment_degraded")]


def test_source_isolation_stale_windows_and_flush():
    assembler = UtteranceAssembler()
    mic = assembler.ingest(WindowDecode("m1", "mic", 0, 10, "microphone words", True, "hard"))
    file = assembler.ingest(WindowDecode("f1", "file", 0, 10, "file words", True, "hard"))
    newer = assembler.ingest(WindowDecode("m2", "mic", 10, 15, "continue", True, "hard"))
    assert assembler.ingest(WindowDecode("m1", "mic", 0, 10, "stale result", True, "silence")) == []
    assert newer[0].text == "microphone words continue"
    assert assembler.flush("file")[0].id == file[0].id
    flushed = assembler.flush()
    assert [(u.id, u.text, u.final) for u in flushed] == [(mic[0].id, "microphone words continue", True)]
    assert assembler.flush() == []


def test_empty_decode_cannot_erase_pending_and_repair_cannot_revert():
    assembler = UtteranceAssembler()
    first = assembler.ingest(WindowDecode("w", "file", 0, 3, "Keep the existing words"))
    assert assembler.ingest(WindowDecode("w", "file", 0, 4, "")) == []
    final = assembler.ingest(WindowDecode("w", "file", 0, 5, "", True, "silence"))
    assert [(u.id, u.text, u.final) for u in final] == [
        (first[0].id, "Keep the existing words", True)]
    assembler.ingest(WindowDecode("new", "file", 6, 10, "wrong wording", True, "hard"))
    fixed = assembler.repair("file", "correct wording", 6, 10)
    assert assembler.ingest(WindowDecode("new", "file", 6, 10, "wrong wording", True, "hard")) == []
    assert assembler.flush()[0].id == fixed[0].id


def test_committed_prefix_revision_surfaces_uncertainty_instead_of_suppressing_words():
    assembler = UtteranceAssembler()
    assembler.ingest(WindowDecode("w", "mic", 0, 2, "Come here. Please wait"))
    committed = assembler.ingest(WindowDecode("w", "mic", 0, 3, "Come here. Please wait now"))
    assert committed[0].final
    revised = assembler.ingest(WindowDecode("w", "mic", 0, 4, "Go there. Please wait now"))
    assert revised[0].reason == "alignment_uncertain"
    assert "Go there." in revised[0].text
    assert "Please wait now" in revised[0].text


def test_forced_period_is_revisable_even_without_an_overlap_anchor():
    assembler = UtteranceAssembler()
    first = assembler.ingest(WindowDecode("a", "file", 0, 10,
        "I do not think the proposal you mentioned yesterday.", True, "hard_pause"))
    final = assembler.ingest(WindowDecode("b", "file", 10, 15,
        "will work for everyone.", True, "silence"))
    assert [(u.text, u.final) for u in final] == [
        ("I do not think the proposal you mentioned yesterday will work for everyone.", True)]
    assert final[0].id == first[0].id


def test_caption_times_are_monotonic_and_audio_repair_skips_estimated_onsets():
    assembler = UtteranceAssembler()
    result = assembler.ingest(WindowDecode("a", "file", 0, 10,
        "One complete sentence. An unfinished thought", True, "hard"))
    assert len(result) == 2 and result[0].final and not result[1].final
    assert result[0].t0 == 0 and 0 < result[0].t1 <= result[1].t0 < result[1].t1 == 10
    assert assembler.pending_audio_bounds("file") is None
    assert assembler.pending_audio_bounds("unknown") is None
    final = assembler.ingest(WindowDecode("b", "file", 10, 16,
        "can continue for a while.", True, "silence"))
    assert final[0].t0 == result[1].t0 and final[0].t1 == 16
    assert final[0].id == result[1].id
    # Finishing many sentences across >30 s resets the *logical* timer.
    assembler = UtteranceAssembler(max_seconds=15)
    finals = []
    for i in range(5):
        updates = assembler.ingest(WindowDecode(str(i), "mic", i * 10, (i + 1) * 10,
            "A sentence is completed. The next one", True, "hard"))
        finals.extend(u for u in updates if u.final)
    assert len(finals) == 5
    assert all(not u.reason.startswith("budget") for u in finals)
    assert all(a.t1 <= b.t0 for a, b in zip(finals, finals[1:]))


def test_committed_prefix_alignment_is_anchored_and_preserves_real_repeated_sentence():
    assembler = UtteranceAssembler()
    assembler.ingest(WindowDecode("w", "file", 0, 3,
        "Some of our coworkers are less senior to us. Is it"))
    committed = assembler.ingest(WindowDecode("w", "file", 0, 5,
        "Some of our coworkers are less senior to us. Is it really"))
    assert committed[0].final
    final = assembler.ingest(WindowDecode("w", "file", 0, 8,
        "But some of our coworkers are less senior to us. Is it really necessary to connect with them?",
        True, "silence"))
    assert [(u.text, u.final) for u in final] == [("Is it really necessary to connect with them?", True)]
    assert committed[1].id == final[0].id
    repeated = UtteranceAssembler()
    repeated.ingest(WindowDecode("w", "mic", 0, 2, "Come here. Wait"))
    repeated.ingest(WindowDecode("w", "mic", 0, 3, "Come here. Wait now"))
    updates = repeated.ingest(WindowDecode("w", "mic", 0, 5,
        "Come here. Come here. Wait now.", True, "silence"))
    assert [u.text for u in updates] == ["Come here.", "Wait now."]


def test_final_decodes_are_idempotent_but_later_audio_is_accepted():
    assembler = UtteranceAssembler()
    decoded = WindowDecode("w", "file", 0, 5, "Finished sentence.", True, "silence")
    assert assembler.ingest(decoded)[0].final
    assert assembler.ingest(decoded) == []
    assert assembler.ingest(WindowDecode("w", "file", 0, 5,
        "A stale changed final.", True, "silence")) == []
    hard = UtteranceAssembler()
    decoded = WindowDecode("h", "mic", 0, 10,
        "One complete sentence. Next phrase", True, "hard")
    first = hard.ingest(decoded)
    assert hard.ingest(decoded) == []
    extended = hard.ingest(WindowDecode("h", "mic", 0, 11,
        "One complete sentence. Next phrase continues", True, "hard"))
    assert [u.text for u in extended] == ["Next phrase continues"]
    assert extended[0].id == first[1].id


def test_overlap_can_begin_inside_already_committed_context():
    assembler = UtteranceAssembler()
    first = assembler.ingest(WindowDecode("a", "file", 0, 10,
        "One complete sentence. Next phrase", True, "hard"))
    second = assembler.ingest(WindowDecode("b", "file", 8.5, 15,
        "sentence. Next phrase continues.", True, "silence", 1.5))
    assert [(u.text, u.final) for u in second] == [("Next phrase continues.", True)]
    assert second[0].id == first[1].id
    assert second[0].t0 >= first[0].t1
    # Matching is punctuation-insensitive and maps the new raw offset correctly.
    revised = UtteranceAssembler()
    first = revised.ingest(WindowDecode("a", "mic", 0, 10,
        "One complete sentence. Next phrase", True, "hard"))
    second = revised.ingest(WindowDecode("b", "mic", 8.5, 15,
        "sentence!  Next phrase continues.", True, "silence", 1.5))
    assert [u.text for u in second] == ["Next phrase continues."]
    assert second[0].id == first[1].id


def test_recorded_repeated_audio_keeps_all_three_cycles_without_overlap_duplication():
    # Literal ASR responses from the 25.02 s real-service replay, calls 1..29.
    # Window starts are 0, 8.5 and 17 s; final calls 11/21/29 end each window.
    snapshots = [
        (1.0, "Hello, everyone."),
        (1.9, "Hello, everyone. Welcome to the meeting."),
        (2.8, "Hello, everyone. Welcome to the meeting. Today we will discuss."),
        (3.7, "Hello, everyone. Welcome to the meeting. Today, we will discuss the projects."),
        (4.62, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule, the budget."),
        (5.54, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule, the budget for next quarter."),
        (6.46, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule. The budget for next quarter looks tight. We need to prioritize our resources."),
        (7.38, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision."),
        (8.3, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday."),
        (9.22, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday. Hello everyone."),
        (10.0, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday. Hello everyone. Welcome to the meeting."),
        (2.42, "Hello, everyone. Welcome to the meeting. Today we will."),
        (3.34, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project."),
        (4.26, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule. The"),
        (5.18, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule, the budget for next quarter."),
        (6.1, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight."),
        (7.02, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision."),
        (7.94, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday."),
        (8.86, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday. Hello everyone."),
        (9.78, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday. Hello everyone. Welcome to the."),
        (10.0, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday. Hello everyone. Welcome to the meeting."),
        (2.42, "Hello, everyone. Welcome to the meeting. Today we will discuss."),
        (3.34, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project."),
        (4.26, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule, the budget."),
        (5.18, "Hello, everyone. Welcome to the meeting. Today, we will discuss the project schedule, the budget for next quarter."),
        (6.1, "Hello everyone. Welcome to the meeting. Today we will discuss the project schedule. The budget for next quarter looks tight. We"),
        (7.02, "Hello everyone. Welcome to the meeting. Today, we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision."),
        (7.94, "Hello everyone. Welcome to the meeting. Today, we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday."),
        (8.02, "Hello everyone. Welcome to the meeting. Today, we will discuss the project schedule. The budget for next quarter looks tight. We need to make a decision before Friday."),
    ]
    assembler = UtteranceAssembler()
    updates = []
    for call, (seconds, text) in enumerate(snapshots, 1):
        index = 0 if call <= 11 else (1 if call <= 21 else 2)
        start = index * 8.5
        final = call in (11, 21, 29)
        updates.extend(assembler.ingest(WindowDecode(str(index), "file", start, start + seconds,
            text, final, "flush" if call == 29 else ("hard" if final else ""), 1.5 if index else 0)))
    updates.extend(assembler.flush())
    finals = [u for u in updates if u.final]
    normalized = [re.sub("[^a-z]", "", u.text.lower()) for u in finals]
    assert normalized == [
        "helloeveryone", "welcometothemeeting", "todaywewilldiscusstheprojectschedule",
        "thebudgetfornextquarterlookstight", "weneedtomakeadecisionbeforefriday",
        "helloeveryone", "welcometothemeeting", "todaywewilldiscusstheprojectschedule",
        "thebudgetfornextquarterlookstight", "weneedtomakeadecisionbeforefriday",
        "helloeveryone", "welcometothemeeting", "todaywewilldiscusstheprojectschedule",
        "thebudgetfornextquarterlookstight", "weneedtomakeadecisionbeforefriday",
    ], [(u.text, u.reason) for u in finals]
    assert len({u.id for u in finals}) == 15
    assert all(a.t1 <= b.t0 for a, b in zip(finals, finals[1:]))
    assert not any("alignment" in u.reason for u in updates)


def test_tiny_actual_overlap_cannot_delete_an_entire_repeated_phrase():
    assembler = UtteranceAssembler()
    assembler.ingest(WindowDecode("a", "mic", 0, 10,
        "A completed sentence. Keep these words.", True, "hard"))
    result = assembler.ingest(WindowDecode("b", "mic", 9.99, 15,
        "Keep these words. Continue now.", True, "silence", 1.5))
    assert [(u.text, u.reason) for u in result] == [
        ("Keep these words Keep these words. Continue now.", "alignment_degraded")]


def test_budgets_bound_pending_without_dropping_text():
    assembler = UtteranceAssembler(max_seconds=30, max_chars=12)
    result = assembler.ingest(WindowDecode("w1", "file", 0, 10, "abcdefghijklmnopqrstuvwxYZ", True, "hard"))
    assert [(u.text, u.final, u.reason) for u in result] == [
        ("abcdefghijkl", True, "budget_chars"), ("mnopqrstuvwx", True, "budget_chars"), ("YZ", False, "hard")]
    again = assembler.ingest(WindowDecode("w1", "file", 0, 11, "abcdefghijklmnopqrstuvwxYZ!", True, "hard"))
    assert [u.text for u in again] == ["YZ!"]
    assert again[0].id == result[-1].id
    assert [u.text for u in assembler.flush()] == ["YZ!"]
    timed = UtteranceAssembler(max_seconds=12)
    first = timed.ingest(WindowDecode("a", "mic", 0, 10, "Still speaking", True, "hard"))
    second = timed.ingest(WindowDecode("b", "mic", 10, 14, "without a break", True, "hard"))
    assert [(u.text, u.final, u.reason) for u in second] == [
        ("Still speaking without a break", True, "budget_seconds")]
    assert first[0].id == second[0].id and timed.flush() == []
    # Retained snapshots must stay bounded even with an extremely large single window.
    large = UtteranceAssembler(max_chars=20)
    large.ingest(WindowDecode("huge", "file", 0, 10, "x" * 20000, True, "hard"))
    retained_strings = [value for state in large._sources.values() for value in vars(state).values() if isinstance(value, str)]
    assert max(map(len, retained_strings), default=0) <= 20


def main():
    for name, test in tuple(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
    print("OK")


main()
