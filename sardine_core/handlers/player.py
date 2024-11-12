from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional, ParamSpec, Self, TypeVar

from sardine_core.base import BaseHandler
from sardine_core.handlers.sender import Number, NumericElement, Sender
from sardine_core.scheduler import AsyncRunner
from sardine_core.utils import Quant, Span, alias_param, get_deadline_from_quant, lerp

__all__ = ("Player",)

P = ParamSpec("P")
T = TypeVar("T")


def for_(n: int) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Allows to play a swimming function x times. It swims for_ n iterations."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            nonlocal n
            n -= 1
            if n >= 0:
                return func(*args, **kwargs)

        return wrapper

    return decorator


@dataclass
class PatternInformation:
    sender: Sender
    send_method: Callable[P, T]
    args: tuple[Any]
    kwargs: dict[str, Any]
    period: NumericElement
    sync: Optional[AsyncRunner]
    iterator: Optional[Number]
    iterator_step: NumericElement
    iterator_limit: Span
    divisor: NumericElement
    rate: NumericElement
    quant: Quant
    timespan: Optional[float]
    until: Optional[int]


class Player(BaseHandler):
    """
    Players are holders used to support one-line specialised swimming functions. Many
    instances of 'Player' are injected in globals() at boot time as a way to provide a
    quick interface for the user to output musical and data patterns. Players are han-
    dling the whole lifetime of a pattern, from its initial entry in the scheduler to
    its death when the silence() or panic() method is called.
    """

    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self.runner = AsyncRunner(name=name)
        self._period: int | float = 1.0

    @property
    def name(self) -> str:
        return self._name

    def fit_period_to_timespan(self, period: NumericElement, timespan: float):
        """
        Fit a given period to a certain timestamp (forcing a pattern to have a fixed
        duration. This feature can be useful for preventing users from creating loops
        that will phase out too easily.
        """

        if isinstance(period, (int, float)):
            return lerp(period, 0, period, 0, timespan)

        period = self.env.parser.parse(period)
        period = list(map(lambda x: lerp(x, 0, sum(period), 0, timespan), period))
        return period

    @staticmethod
    @alias_param(name="period", alias="p")
    @alias_param(name="iterator_step", alias="i")
    @alias_param(name="iterator_limit", alias="l")
    @alias_param(name="divisor", alias="d")
    @alias_param(name="rate", alias="r")
    @alias_param(name="timespan", alias="span")
    def _play_factory(
        sender: Sender,
        send_method: Callable[P, T],
        *args: P.args,
        timespan: Optional[float] = None,
        until: Optional[int] = None,
        period: NumericElement = 1,
        sync: Optional[AsyncRunner] = None,
        iterator: Optional[Number] = None,
        iterator_step: Optional[Number] = 1,
        iterator_limit: Span = "inf",
        divisor: NumericElement = 1,
        rate: NumericElement = 1,
        quant: Quant = "bar",
        **kwargs: P.kwargs,
    ) -> PatternInformation:
        """Entry point of a pattern into the Player"""

        return PatternInformation(
            sender,
            send_method,
            args,
            kwargs,
            period,
            sync,
            iterator,
            iterator_step,
            iterator_limit,
            divisor,
            rate,
            quant,
            timespan,
            until,
        )

    def __rshift__(self, pattern: Optional[PatternInformation]) -> None:
        """
        This method acts as a cosmetic disguise for feeding PatternInformation into a
        given player. Its syntax is inspired by FoxDot (Ryan Kirkbride), another very
        popular live coding library.
        """
        if pattern is not None and pattern.timespan is not None:
            pattern.period = self.fit_period_to_timespan(
                pattern.period, pattern.timespan
            )
        self.push(pattern)

    def __mul__(self, pattern: Optional[PatternInformation]) -> None:
        """
        This method acts as a cosmetic disguise for feeding PatternInformation into a
        given player. Its syntax is inspired by FoxDot (Ryan Kirkbride), another very
        popular live coding library.
        """
        if pattern is not None and pattern.timespan is not None:
            pattern.period = self.fit_period_to_timespan(
                pattern.period, pattern.timespan
            )
        self.push(pattern)

    def get_new_period(self, pattern: PatternInformation) -> Number:
        """Get period value for the current cycle"""
        for message in pattern.sender.pattern_reduce(
            {"period": pattern.period},
            self.iterator,
            pattern.divisor,
            pattern.rate,
            # use_divisor_to_skip=False,
            # TODO: why was this untoggled?
            use_divisor_to_skip=True,
        ):
            return message["period"]
        return 1

    def func(
        self,
        pattern: PatternInformation,
        p: NumericElement = 1,  # pylint: disable=invalid-name,unused-argument
    ) -> None:
        """Central swimming function defined by the player"""
        self._iterator_step = pattern.iterator_step
        self._iterator_limit = pattern.iterator_limit

        self.runner._iter_limit = pattern.iterator_limit
        self.runner._iter_step = pattern.iterator_step

        if pattern.sync is None:
            iterator = self.runner.iter
        else:
            iterator = pattern.sync.runner.iter

        dur = pattern.send_method(
            *pattern.args,
            **pattern.kwargs,
            iterator=iterator,
            divisor=pattern.divisor,
            rate=pattern.rate,
        )

        period = self.get_new_period(pattern)
        if not dur:
            self.again(pattern=pattern, p=period)
        else:
            self.again(pattern=pattern, p=dur)

    def stop(self):
        """Stop the player by removing the Player"""
        self.env.scheduler.stop_runner(self.runner)

    def push(self, pattern: Optional[PatternInformation]):
        """
        Managing lifetime of the pattern, similar to managing a swimming function
        manually. If PatternInformation is hot-swapped by None, the Player will stop
        scheduling its internal function, defined in self.func.
        """
        # This is a local equivalent to the silence() function.
        if pattern is None:
            return self.env.scheduler.stop_runner(self.runner)
        elif not self.runner.is_running():
            # Assume we are queuing the first state
            self.iterator = 0
            self.runner.reset_states()

        # Forcibly reset the interval shift back to 0 to make sure
        # the new pattern can be synchronized
        self.runner.interval_shift = 0.0

        func = for_(pattern.until)(self.func) if pattern.until else self.func
        deadline = get_deadline_from_quant(self.env.clock, pattern.quant)
        period = self.get_new_period(pattern)

        if deadline is None:
            self.runner.push(func, pattern=pattern, p=period)
        else:
            self.runner.push_deferred(deadline, func, pattern=pattern, p=period)

        self.env.scheduler.start_runner(self.runner)
        self.runner.reload()

    def again(self, *args, **kwargs):
        self.runner.update_state(*args, **kwargs)
        self.runner.swim()
