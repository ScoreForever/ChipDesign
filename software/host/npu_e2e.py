"""Observe the production Npu driver without duplicating its mailbox protocol."""
import time

from nocpu_jtag import Op


class PhaseTimer:
    def __init__(self, clock=time.perf_counter_ns):
        self.clock = clock
        self.samples = []
        self.name = None

    def begin(self, name):
        self.samples = []
        self.name = None
        self.start = self.clock()
        self.mark(name)
        self.first_at = self.at

    def mark(self, name):
        now = self.clock()
        if self.name is not None and name != self.name:
            self.samples.append(dict(phase=self.name, wall_ns=now - self.at))
        if name != self.name:
            self.name, self.at = name, now

    def finish(self):
        now = self.clock()
        self.samples.append(dict(phase=self.name, wall_ns=now - self.at))
        # Include the small entry overhead in the first phase so totals reconcile.
        self.samples[0]['wall_ns'] += (self.first_at - self.start)
        self.name = None
        return dict(total_wall_ms=(now - self.start) / 1e6,
                    phases=[dict(phase=s['phase'], wall_ms=s['wall_ns'] / 1e6) for s in self.samples])



class TimedTransport:
    """Phase boundaries are before commands; spans include RPC/USB and host sleeps.

    Optional burst methods are delegated only if supported by the underlying
    transport. No transaction is added, removed, retried or reordered.
    """
    def __init__(self, transport, timer, wordwise=False):
        self.transport, self.timer, self.wordwise = transport, timer, wordwise

    def __getattr__(self, name):
        if self.wordwise and name in ('burst_write', 'burst_read'):
            raise AttributeError(name)
        return getattr(self.transport, name)

    def exchange(self, op, addr=0, data=0):
        if self.timer.name is not None:
            phase = {Op.MODEL_BEGIN: 'model_upload', Op.MODEL_COMMIT: 'model_commit',
                     Op.INPUT_BEGIN: 'input_upload', Op.INPUT_COMMIT: 'input_commit',
                     Op.START: 'start', Op.ACK: 'ack'}.get(op)
            if op == Op.READ and addr == 4 and self.timer.name in ('start', 'completion_poll'):
                phase = 'completion_poll'
            if op == Op.READ and addr == 0x20 and self.timer.name == 'completion_poll':
                phase = 'output_metadata'
            if op == Op.READ and addr >= 0x20000:
                phase = 'output_readout'
            if phase:
                self.timer.mark(phase)
        return self.transport.exchange(op, addr, data)


def measure(timer, phase, operation):
    timer.begin(phase)
    try:
        value = operation()
    except Exception:
        timer.failed = timer.finish()
        raise
    return value, timer.finish()
