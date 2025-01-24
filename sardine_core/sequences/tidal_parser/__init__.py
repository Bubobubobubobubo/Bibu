from .control import *
from .mini import *
from .pattern import *
from .stream import TidalStream
from .tidal_euclid import *
from .utils import *

__streams = {}


def tidal_factory(env, osc_client, tidal_players):
    """Returns a tidal function to play Tidal patterns on a given OSC client"""
    env = env

    def tidal(key, pattern=None, data_only: bool = False):
        if key not in __streams:
            __streams[key] = TidalStream(
                data_only=data_only,
                name=key,
                osc_client=osc_client,
            )
            tidal_players.append(__streams[key])
        if pattern:
            __streams[key].pattern = pattern
        return __streams[key]

    return tidal


def hush_factory(env, osc_client, tidal_players):
    def _hush_all():
        for stream in __streams.values():
            stream.pattern = silence
            tidal_players.remove(stream)
        __streams.clear()

    def _hush(pat):
        streams = (
            (n, s) for n, s in __streams.items() if pat in [n, s.name]
        )
        name, stream = next(streams, (None, None))
        if not stream:
            raise TypeError(f'Stream "{pat}" not found')

        stream.pattern = silence
        tidal_players.remove(stream)
        __streams.pop(name)

    def hush(pat=None):
        if pat is None:
            return _hush_all()
        if isinstance(pat, str):
            return _hush(pat)
        if not hasattr(pat, 'name') or not isinstance(pat.name, str):
            raise TypeError(f'Object of type {type(pat)} is not recognized')
        _hush(pat.name)

    return hush


class __Streams:
    def __setitem__(self, key, value):
        p(key, value)


d = __Streams()
