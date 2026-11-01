"""A repair may only read complete, contiguous audio still inside the budget."""
import numpy as np

from simulcast.audio.buffer import AudioBuffer


def main():
    buffer = AudioBuffer(sample_rate=16000, max_seconds=.1)
    for i in range(10):
        buffer.append((i + 1) * .02, np.full(320, i, dtype=np.float32))
    assert buffer.slice(0, .02) is None, "evicted audio cannot be used as a repair"
    result = buffer.slice(.12, .18)
    assert result is not None and len(result) == 960
    assert np.array_equal(result, np.repeat([6, 7, 8], 320))
    assert buffer.slice(.12, .22) is None, "the buffer cannot invent future audio"
    buffer.append(.26, np.full(320, 12, dtype=np.float32))
    assert buffer.slice(.18, .26) is None, "a dropped frame gap cannot be concealed by concatenation"
    assert buffer.slice(.24, .26).shape == (320,)
    print("bounded contiguous repair audio OK")


main()
