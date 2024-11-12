import time
from itertools import chain
from typing import Callable, Optional

from osc4py3 import oscbuildparse
from osc4py3.as_eventloop import *
from osc4py3.oscmethod import *

from sardine_core.utils import alias_param

from .osc_loop import OSCLoop
from .sender import Number, NumericElement, Sender, StringElement, _resolve_if_callable

__all__ = ("OSCHandler",)


class OSCHandler(Sender):
    def __init__(
        self,
        loop: OSCLoop,
        ip: str = "127.0.0.1",
        port: int = 23456,
        name: str = "OSCSender",
        ahead_amount: float = 0.0,
        nudge: float = 0.0,
    ):
        super().__init__()
        self.loop = loop

        # Setting up OSC Connexion
        self._ip, self._port, self._name = (ip, port, name)
        self._ahead_amount = ahead_amount
        self.client = osc_udp_client(address=self._ip, port=self._port, name=self._name)
        self._events = {"send": self._send}
        self._defaults: dict = {}
        self.nudge = nudge

        loop.add_child(self, setup=True)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._name} ip={self._ip!r} port={self._port}>"

    def setup(self):
        for event in self._events:
            self.env.register_hook(event, self)

    def hook(self, event: str, *args):
        func = self._events[event]
        func(*args)

    # Global parameters
    @property
    def defaults(self):
        return self._defaults

    def _send(self, address: str, message: list) -> None:
        bun = self._make_bundle([[address, message]])
        osc_send(bun, self._name)

    def _send_bundle(self, messages: list) -> None:
        bun = self._make_bundle(messages)
        osc_send(bun, self._name)

    def send_raw(self, address: str, message: list, nudge=False) -> None:
        """
        Public alias for the _send function. It can sometimes be useful to have it
        when we do want to write some raw OSC message without formatting it in the
        expected SuperDirt format.
        """
        if nudge:
            self.call_timed_with_nudge(
                self.env.clock.shifted_time, self._send, address, message
            )
        else:
            self._send(address, message)

    def send_raw_bundle(self, messages: list, nudge=False) -> None:
        if nudge:
            self.call_timed_with_nudge(
                self.env.clock.shifted_time, self._send_bundle, messages
            )
        else:
            self._send_bundle(messages)

    def _make_bundle(self, messages: list) -> oscbuildparse.OSCBundle:
        return oscbuildparse.OSCBundle(
            oscbuildparse.unixtime2timetag(time.time() + self._ahead_amount),
            [
                oscbuildparse.OSCMessage(message[0], None, message[1])
                for message in messages
            ],
        )

    @alias_param(name="iterator", alias="i")
    @alias_param(name="divisor", alias="d")
    @alias_param(name="rate", alias="r")
    @alias_param(name="sorted", alias="s")
    def send(
        self,
        address: Optional[StringElement] | Callable[[], StringElement],
        iterator: Number | Callable[[], Number] = 0,
        divisor: NumericElement | Callable[[], NumericElement] = 1,
        rate: NumericElement | Callable[[], NumericElement] = 1,
        sort: bool | Callable[[], bool] = True,
        **rest_of_pattern: dict,
    ) -> None:
        if address is None:
            return

        if self.apply_conditional_mask_to_bars(
            pattern=rest_of_pattern,
        ):
            return

        pattern = {"address": _resolve_if_callable(address)}

        # Evaluate all potential callables
        for key, value in rest_of_pattern.items():
            pattern[key] = _resolve_if_callable(value)

        deadline = self.env.clock.shifted_time
        for message in self.pattern_reduce(
            pattern,
            _resolve_if_callable(iterator),
            _resolve_if_callable(divisor),
            _resolve_if_callable(rate),
        ):
            if message["address"] is None:
                continue
            address = message.pop("address")
            if sort:
                serialized = list(chain(*sorted(message.items())))
            else:
                serialized = list(chain(*message.items()))
            self.call_timed_with_nudge(deadline, self._send, f"/{address}", serialized)
