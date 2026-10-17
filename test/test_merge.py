from simulcast.pipeline.orchestrator import _merge_carry

cases = [
    ("Welcome", "Welcome to the meeting."),
    ("and these efforts", "Can easily be demonstrated by the results."),
    ("and these efforts", "efforts can easily be demonstrated."),
    ("And the second point is", "is that we need data."),
    ("We need to decide", "before Friday."),
]
for a, b in cases:
    print(repr(a), "+", repr(b), "->", repr(_merge_carry(a, b)))
