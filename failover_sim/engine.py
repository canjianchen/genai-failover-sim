import heapq


class Engine:
    def __init__(self):
        self.now = 0.0
        self._q = []
        self._seq = 0

    def schedule(self, delay, callback, *args):
        when = self.now + float(delay)
        self._seq += 1
        heapq.heappush(self._q, (when, self._seq, callback, args))

    def schedule_at(self, when, callback, *args):
        self._seq += 1
        heapq.heappush(self._q, (float(when), self._seq, callback, args))

    def run_until(self, t):
        t = float(t)
        while self._q and self._q[0][0] <= t:
            when, _, cb, args = heapq.heappop(self._q)
            self.now = when
            cb(*args)
        self.now = t

    def run(self):
        while self._q:
            when, _, cb, args = heapq.heappop(self._q)
            self.now = when
            cb(*args)
