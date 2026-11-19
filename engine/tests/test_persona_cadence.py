"""A persona client fires at its declared cadence, however long a fire takes.

Measured on `pace-test` (2026-11-19): a `cyclic:5s / med` dig emit (96 queries a minute
declared) put ~76 a minute on the wire. The loop scheduled `now + interval` with `now` taken
before every emit of the pass fired, and polls at 1 s, so the fire time of the emit itself and
of its siblings stretched every period. The client now holds each emit to a jitter-free anchor.

Run the rendered artifact, not a copy of its algorithm: the client's own loop is exec'd with a
virtual clock in place of `time` and a stub `fire` that costs virtual seconds.
"""
import pytest

from axrange.personas.schema import load_persona


class _Done(Exception):
    pass


class _Clock:
    def __init__(self, until):
        self.t, self.until = 1_800_000_000.0, until

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.t - 1_800_000_000.0 >= self.until:
            raise _Done


def _persona(drive):
    return load_persona({
        "id": "p", "os_family": "linux", "drive": drive,
        "emits": [{"proto": "dns", "ports": [53], "peer_role": "dns", "direction": "EW",
                   "cadence": "cyclic:5s", "volume": "med"},
                  {"proto": "http", "ports": [80], "peer_role": "web", "direction": "EW",
                   "cadence": "cyclic:10s", "volume": "low"}],
        "requires": [], "knobs": {"schedule": "24x7"}})


def _render(drive):
    peers = {"dns": "10.0.0.11", "web": "10.0.0.12"}
    if drive == "command":
        from axrange.personas.drivers.command import render_command_client
        return render_command_client(_persona(drive), instance_id="h1-p", peers=peers, seed=1)
    from axrange.personas.drivers.scripted import render_scripted_client
    return render_scripted_client(_persona(drive), instance_id="h1-p", peers=peers, seed=1)


@pytest.mark.parametrize("drive", ["scripted", "command"])
def test_the_long_run_fire_rate_is_the_declared_cadence(drive, tmp_path):
    src = _render(drive)
    head, sep, tail = src.partition("log('armed")
    ns: dict = {}
    exec(compile(head, "<client>", "exec"), ns)
    clock = _Clock(until=600)
    fires: dict = {}

    def fire(emit):
        fires[emit["proto"]] = fires.get(emit["proto"], 0) + 1
        clock.t += 2.0                       # a slow fire: 8 digs, a curl, an SMB pull

    ns.update(time=clock, fire=fire, LOG=str(tmp_path / "persona.log"))
    with pytest.raises(_Done):
        exec(compile(sep + tail, "<client-loop>", "exec"), ns)
    # 600 s at 5 s and 10 s: 120 and 60 fires, within one period of jitter/phase.
    assert 118 <= fires["dns"] <= 121, fires
    assert 58 <= fires["http"] <= 61, fires
