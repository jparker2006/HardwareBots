"""Energy-threshold speech detection: feed int16 blocks in, get utterances out. Pure (numpy only), so it is testable."""
from __future__ import annotations

import numpy as np


def rms(block) -> float:
    block = np.asarray(block)
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))


class Utterance:
    def __init__(self, samples, rate: int, silence_s: float, cut: bool):
        self.samples, self.rate, self.silence_s, self.cut = samples, rate, silence_s, cut

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.rate


class Vad:
    """Speech starts after `start_s` of loud audio and ends after `end_silence_s` of quiet (or at `max_s`).

    With no fixed threshold, the threshold is `ratio` x the room's noise floor (at least `minimum`), and the
    floor follows the room slowly while nobody is talking, so it keeps up as the hall gets louder or quieter.
    """

    def __init__(self, threshold: float | None = None, floor: float = 50.0, rate: int = 16000, start_s: float = 0.15,
                 end_silence_s: float = 0.8, max_s: float = 15.0, pre_roll_s: float = 0.3, keep_tail_s: float = 0.2,
                 ratio: float = 3.0, minimum: float = 300.0, adapt: float = 0.02):
        self.fixed = threshold
        self.floor, self.rate, self.ratio, self.minimum, self.adapt = floor, rate, ratio, minimum, adapt
        self.start_n = int(start_s * rate)
        self.end_n = int(end_silence_s * rate)
        self.max_n = int(max_s * rate)
        self.pre_n = int((start_s + pre_roll_s) * rate)
        self.tail_n = int(keep_tail_s * rate)
        self.level = 0.0
        self.reset()

    @property
    def threshold(self) -> float:
        return self.fixed if self.fixed is not None else max(self.minimum, self.floor * self.ratio)

    def calibrate(self, noise) -> float:
        self.floor = rms(noise) or self.floor
        return self.threshold

    def reset(self) -> None:
        self.active = False
        self._pre: list = []
        self._blocks: list = []
        self._voiced = self._silent = self._len = 0

    def feed(self, block) -> Utterance | None:
        block = np.asarray(block, dtype=np.int16).reshape(-1)
        n = len(block)
        self.level = rms(block)
        loud = self.level > self.threshold
        if not self.active:
            self._pre.append(block)
            while sum(len(b) for b in self._pre[1:]) >= self.pre_n:
                self._pre.pop(0)
            self._voiced = self._voiced + n if loud else 0
            if not loud and self.fixed is None:
                self.floor += self.adapt * (self.level - self.floor)
            if self._voiced >= self.start_n:
                self.active, self._blocks, self._silent = True, self._pre, 0
                self._len, self._pre = sum(len(b) for b in self._blocks), []
            return None
        self._blocks.append(block)
        self._len += n
        self._silent = 0 if loud else self._silent + n
        if self._silent >= self.end_n:
            return self._finish(cut=False)
        if self._len >= self.max_n:
            return self._finish(cut=True)
        return None

    def _finish(self, cut: bool) -> Utterance:
        samples = np.concatenate(self._blocks)
        silence = self._silent
        if cut:
            samples = samples[:self.max_n]
        elif silence > self.tail_n:
            samples = samples[:len(samples) - (silence - self.tail_n)]
        self.reset()
        return Utterance(samples, self.rate, silence / self.rate, cut)
