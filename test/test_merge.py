from simulcast.pipeline.utterances import UtteranceAssembler, WindowDecode

cases = [
    ("Welcome", "Welcome to the meeting.", "Welcome to the meeting."),
    ("and these efforts", "Can easily be demonstrated by the results.",
     "and these efforts Can easily be demonstrated by the results."),
    ("and these efforts", "efforts can easily be demonstrated.", "and these efforts can easily be demonstrated."),
    ("And the second point is", "the second point is that we need data.", "And the second point is that we need data."),
    ("We need to decide", "before Friday.", "We need to decide before Friday."),
]
for a, b, expected in cases:
    assembler = UtteranceAssembler()
    assembler.ingest(WindowDecode("a", "file", 0, 10, a, True, "hard"))
    updates = assembler.ingest(WindowDecode("b", "file", 8.5, 12, b, True, "silence", 1.5))
    finals = [u.text for u in updates if u.final]
    assert finals == [expected], (a, b, finals)
print("cross-window phrase joining OK")
