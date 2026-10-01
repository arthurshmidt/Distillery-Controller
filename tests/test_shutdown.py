from still.runner import ControlLoop


class _Ctl:
    def __init__(self, fail=False):
        self.fail = fail
        self.reasons = []

    def tick(self):
        return None

    def failsafe(self, reason, **kw):
        self.reasons.append((reason, kw))
        if self.fail:
            raise RuntimeError("boom")


def test_stop_commands_failsafe():
    ctl = _Ctl()
    loop = ControlLoop(ctl, interval_s=0.01)
    loop.start()
    loop.stop()
    assert ("shutdown", {"is_fault": False}) in ctl.reasons


def test_stop_survives_failsafe_error():
    loop = ControlLoop(_Ctl(fail=True), interval_s=0.01)
    loop.start()
    loop.stop()
