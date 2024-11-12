import asyncio
import heapq
import inspect
import math
import os
import traceback
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, MutableSequence, NamedTuple, Optional, Union

from rich.panel import Panel

from sardine_core.base import BaseClock
from sardine_core.clock import Time
from sardine_core.logger import print
from sardine_core.utils import MISSING, Span, maybe_coro

from .constants import MaybeCoroFunc
from .errors import *

if TYPE_CHECKING:
    from sardine_core.fish_bowl import FishBowl

    from .scheduler import Scheduler

__all__ = ("AsyncRunner", "FunctionState")


def _assert_function_signature(sig: inspect.Signature, args, kwargs):
    if args:
        message = "Positional arguments cannot be used in scheduling"
        if missing := _missing_kwargs(sig, args, kwargs):
            message += "; perhaps you meant `{}`?".format(
                ", ".join(f"{k}={v!r}" for k, v in missing.items())
            )
        raise BadArgumentError(message)


def _discard_kwargs(sig: inspect.Signature, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Discards any kwargs not present in the given signature."""
    pass_through = kwargs.copy()

    for param in sig.parameters.values():
        value = kwargs.get(param.name, MISSING)
        if value is not MISSING:
            pass_through[param.name] = value

    return pass_through


def _extract_new_period(
    sig: inspect.Signature, kwargs: dict[str, Any], default_period: int | float
) -> Union[float, int]:
    period = kwargs.get("p")

    # Assign a default period if necessary
    if period is None:
        param = sig.parameters.get("p")
        period = getattr(param, "default", default_period)

    # Resolve any callable period
    if callable(period):
        try:
            period = period()
        except Exception as e:
            raise BadPeriodError(f"Error evaluating function for period: {e}")

    if not isinstance(period, (float, int)):
        raise BadPeriodError(f"Period must be a float or integer, not {period!r}")
    elif period <= 0:
        raise BadPeriodError(f"Period must be >0, not {period}")

    return period


def _missing_kwargs(
    sig: inspect.Signature, args: tuple[Any], kwargs: dict[str, Any]
) -> dict[str, Any]:
    required = []
    defaulted = []
    for param in sig.parameters.values():
        if param.kind in (
            param.POSITIONAL_ONLY,
            param.VAR_POSITIONAL,
            param.VAR_KEYWORD,
        ):
            continue
        elif param.name in kwargs:
            continue
        elif param.default is param.empty:
            required.append(param.name)
        else:
            defaulted.append(param.name)

    guessed_mapping = dict(zip(required + defaulted, args))
    return guessed_mapping


@dataclass
class FunctionState:
    func: "MaybeCoroFunc"
    args: tuple
    kwargs: dict


class DeferredState(NamedTuple):
    deadline: Union[float, int]
    index: int
    state: FunctionState


class AsyncRunner:
    """Handles calling synchronizing and running a function in
    the background, with support for run-time function patching.

    Runners should only be started from the `Scheduler.start_runner()` method.

    The `Scheduler.deferred` attribute is used to control if AsyncRunner
    runs with an implicit time shift when calling its function or not.
    This helps improve sound synchronization by giving the function
    more time to execute. For example, assuming bpm = 120, `deferred=False`
    would expect its function to complete instantaneously,
    whereas `deferred=True` would allow a function with `p=1`
    to finish execution within 500ms (1 beat) instead.

    In either case, if the function takes too long to execute, it will miss
    its scheduling deadline and cause an unexpected gap between function calls.
    Functions must complete within the time span to avoid this issue.
    """

    MAX_FUNCTION_STATES = 3

    name: str
    """Uniquely identifies a runner when it is added to a scheduler."""

    _iter: int
    """Number of times this asyncrunner has been executed"""

    _iter_step: int
    """The iteration step to use for the next iteration"""

    _iter_limit: Span
    """The maximum number of iterations allowed before going back to 0"""

    _default_period: int | float
    """Default recursion period"""

    background_job: bool
    """Determines if the asyncrunner should be running the background
    and never be interrupted by silence(), panic() or any manual stop
    function.
    """

    scheduler: "Optional[Scheduler]"
    """The scheduler this runner was added to."""
    states: MutableSequence[FunctionState]
    """
    The function stack, used for auto-restoring functions.

    This is implemented with a deque to ensure a limit on how many functions
    are stored in the cache.
    """
    deferred_states: list[DeferredState]
    """
    A heap queue storing (time, index, state) pairs.

    Unlike regular `states`, these states are left in the background until
    their time arrives, at which point they are moved to the `states` sequence
    and will take over the next iteration.
    """
    interval_shift: float
    """
    The amount of time to offset the runner's interval.

    An interval defines the amount of time between each execution
    of the current function. For example, a clock with a beat duration
    of 0.5s and a period of 2 beats means each interval is 1 second.

    Through interval shifting, a function can switch between different
    periods/tempos and then compensate for the clock's current time to
    avoid the next immediate beat being shorter than expected.

    Initially, functions have an interval shift of 0. The runner
    will automatically change its interval shift when the function
    schedules itself with a new period or a change in the clock's beat
    duration occurs. This can lead to functions with the same period
    running at different phases. To synchronize these functions together,
    their interval shifts should be set to the same value (usually 0).
    """
    snap: Optional[Union[float, int]]
    """
    The absolute time that the next interval should start at.

    Setting this attribute will take priority over the regular interval
    on the next iteration and cause the runner to wait until the snap
    deadline has arrived.

    The `delay_interval()` method combines this with interval shifting
    to properly delay a runner and its interval until the given deadline.

    Once this time has been passed and the next iteration was run,
    this attribute will be reset to `None`.

    Note that deferred states will take priority over this, and in fact even
    replace the snap, if one or more of those states specify a deadline earlier
    than the current snap's deadline.
    """

    _swimming: bool
    _stop: bool
    _task: Optional[asyncio.Task]
    _reload_event: asyncio.Event
    _has_reverted: bool
    _jump_start: bool

    _deferred_state_index: int

    _can_correct_interval: bool
    _expected_time: float
    _last_interval: float
    _last_expected_time: float
    _last_state: Optional[FunctionState]

    def __init__(self, name: str):
        self.name = name
        self.scheduler = None
        self.states = deque(maxlen=self.MAX_FUNCTION_STATES)
        self.deferred_states = []
        self.interval_shift = 0.0
        self.snap = None
        self._iter = 0
        self._iter_step = 1
        self._iter_limit = "inf"
        self._default_period = 1
        self.background_job = False

        self._swimming = False
        self._stop = False
        self._task = None
        self._reload_event = asyncio.Event()
        self._has_reverted = False
        self._jump_start = False

        self._deferred_state_index = 0

        self._can_correct_interval = False
        self._expected_time = 0.0
        self._last_interval = 0.0
        self._last_expected_time = -math.inf
        self._last_state = None

    def __repr__(self):
        cls_name = type(self).__name__
        status = "running" if self.is_running() else "stopped"
        attrs = " ".join(
            f"{attr}={getattr(self, attr)!r}"
            for attr in (
                "name",
                "scheduler",
            )
        )
        return f"<{cls_name} {status} {attrs}>"

    # Helper properties

    @property
    def clock(self) -> BaseClock:
        """A shorthand for the current clock."""
        return self.scheduler.env.clock

    @property
    def defer_beats(self) -> float:
        """The number of beats to defer function calls."""
        return float(self.scheduler.deferred)

    @property
    def env(self) -> "FishBowl":
        """A shorthand for the scheduler's fish bowl."""
        return self.scheduler.env

    @property
    def time(self) -> Time:
        """The fish bowl's current time."""
        return self.scheduler.env.time

    @property
    def period(self) -> int | float:
        """The default period duration between recursions"""
        return self._default_period

    @property
    def p(self) -> int | float:
        """Alias for period"""
        return self._default_period

    @property
    def iter(self) -> int | float:
        """The current iteration number"""
        return self._iter

    @property
    def i(self) -> int | float:
        """Alias for iter"""
        return self._iter

    # Setters

    @period.setter
    def period(self, value: int | float):
        self._default_period = value

    @p.setter
    def p(self, value: int | float):
        self._default_period = value

    @iter.setter
    def iter(self, value: int | float):
        self._iter = value

    @i.setter
    def i(self, value: int | float):
        self._iter = value

    # State management

    def push(self, func: "MaybeCoroFunc", *args, **kwargs):
        """Pushes a function state to the runner to be called in the next iteration.

        It is recommended to reload the runner after this in case the
        current iteration sleeps past the deadline.

        Note that this does not take priority over the `snap` attribute;
        if a snap is specified, the runner will continue to wait for that
        deadline to pass. If running a new function immediately is desired,
        the `snap` should be set to `None` before reloading the runner.

        Args:
            func (MaybeCoroFunc): The function to add.
            *args: The positional arguments being passed to `func`.
            **kwargs: The keyword arguments being passed to `func`.

        Raises:
            BadFunctionError: The value given for `func` must be callable.
        """
        if not callable(func):
            raise BadFunctionError(f"Expected a callable, got {func!r}")
        elif not self.states:
            state = FunctionState(func, args, kwargs)
            return self.states.append(state)

        last_state = self.states[-1]

        new_state = FunctionState(func, args, kwargs)
        self._merge_states(last_state, new_state)

        self.states.append(new_state)

    def push_deferred(
        self, deadline: Union[float, int], func: "MaybeCoroFunc", *args, **kwargs
    ):
        """Adds a function to a queue to eventually be run.

        It is recommended to reload the runner after this in case the
        current iteration sleeps past the deadline.

        If there is an existing `snap` deadline, deferred states will take
        priority and replace the `snap` attribute to ensure they run on time.

        Args:
            time (Union[float, int]):
                The absolute clock time to wait before the function state
                is pushed.
            func (MaybeCoroFunc): The function to add.
            *args: The positional arguments being passed to `func`.
            **kwargs: The keyword arguments being passed to `func`.

        Raises:
            BadFunctionError: The value given for `func` must be callable.
        """
        if not callable(func):
            raise BadFunctionError(f"Expected a callable, got {func!r}")

        if not self.deferred_states:
            self._deferred_state_index = 0

        index = self._deferred_state_index
        self._deferred_state_index += 1

        # Create a new function state and push it to the heap queue
        state = FunctionState(func, args, kwargs)
        heapq.heappush(self.deferred_states, DeferredState(deadline, index, state))

    def update_state(self, *args, **kwargs):
        """Updates the top-most function state with new arguments.

        This assumes that the function is rescheduling itself, and
        will therefore allow interval correction to occur in case
        the period or tempo has changed.
        """
        if not self.states:
            return  # reset_states() was likely called
        last_state = self.states[-1]
        last_state.args = args
        last_state.kwargs = kwargs
        self.allow_interval_correction()

    def reload(self):
        """Triggers an immediate state reload.

        This method is useful when changes to the clock occur,
        or when a new function is pushed to the runner.
        """
        self._reload_event.set()

    def _merge_states(self, old: FunctionState, new: FunctionState) -> None:
        """
        Merges the arguments and keyword arguments of two function states.

        The old function state is expected to be the most recent state,
        and the new function state is the one being pushed. The new state
        will inherit any arguments that were not passed to it from the old
        state, while any new keyword arguments will be added to the new state.

        Args:
            old (FunctionState): The most recent function state.
            new (FunctionState): The function state being pushed.
        """
        new.args = new.args + old.args[len(new.args) :]
        new.kwargs = old.kwargs | new.kwargs

    # Lifecycle control

    def start(self):
        """Initializes the background runner task.

        If the task has already started, this is a no-op.
        """
        if self.is_running():
            return

        self._task = asyncio.create_task(self._runner())
        self._task.add_done_callback(self._on_task_done)

    def is_running(self) -> bool:
        """Returns True if the runner is running."""
        return self._task is not None and not self._task.done()

    def swim(self) -> None:
        """Allows the runner to continue the next iteration.

        This method must be called continuously to keep the runner alive.
        """
        self._swimming = True

    def stop(self) -> None:
        """Stops the runner's execution after the current iteration.

        This method takes precedence when `swim()` is also called.
        """
        self._stop = True
        self.reload()

    def reset_states(self) -> None:
        """Clears all function states from the runner.

        This method can safely be called while the runner is running.
        In such case, the runner will stop by itself on the next
        iteration unless a new state is pushed after this method.
        """
        self.states.clear()
        self.deferred_states.clear()

    # Interval shifting

    def allow_interval_correction(self) -> None:
        """Allows the interval to be corrected in the next iteration."""
        self._can_correct_interval = True

    def delay_interval(self, deadline: Union[float, int], period: Union[float, int]):
        """Delays the next iteration until the given deadline has passed.

        This is equivalent to setting the runner's `snap` attribute
        to the deadline and also applying an appropriate interval
        shift to synchronize the period.

        The runner must be started from a scheduler before this method can
        be used. In addition, at least one function state must be pushed to
        the runner (deferred or not) in order to calculate the interval shift.

        To take effect immediately, the runner should be reloaded
        to skip the current iteration.

        Args:
            time (Union[float, int]): The absolute time to wait.
            period (Union[float, int]): The period to synchronize to.

        Raises:
            RuntimeError: A function must be pushed before this can be used.
        """
        self.snap = deadline
        # TODO: explain this line and what it does exactly
        self.interval_shift = self.clock.get_beat_time(period, time=deadline)

    def _check_snap(self, time: float) -> None:
        if self.snap is not None and time + self._last_interval >= self.snap:
            self.snap = None

    def _correct_interval(self, period: Union[float, int]) -> None:
        """Checks if the interval should be corrected.

        Interval correction occurs when `allow_interval_correction()`
        is called, and the given interval is different from the last
        interval *only* for the current iteration. If the interval
        did not change, interval correction must be requested again.

        Args:
            period (Union[float, int]):
                The period being used in the current iteration.
        """
        # NOTE: this should account for tempo change, weird...
        interval = period * self.clock.beat_duration
        if self._can_correct_interval and interval != self._last_interval:
            time = self._expected_time
            self.interval_shift = self.clock.get_beat_time(period, time=time)

        self._last_interval = interval
        self._can_correct_interval = False

    def _correct_interval_background_job(self, period: Union[float, int]) -> None:
        """
        Alternative version for fixed-rate background jobs. The interval or
        period is not indexed on the clock like with the _correct_interval
        method above.
        """
        interval = period
        if self._can_correct_interval and interval != self._last_interval:
            time = self._expected_time
            self.interval_shift = self.clock.get_beat_time(period, time=time)

        self._last_interval = interval
        self._can_correct_interval = False

    def _get_next_deadline(self, period: Union[float, int]) -> float:
        """Returns the amount of time until the next interval.

        The base interval is determined by the `period` argument,
        and then offsetted by the `interval_shift` attribute.

        If the `snap` attribute is set to an absolute time
        and the current clock time has not passed the snap,
        it will take priority over whatever period was passed.

        If this is called earlier than the expected time, we should use
        the current time to avoid calculating the next beat too far ahead,
        which would cause an unusually long gap between iterations.

        If this is called after the expected time has already passed,
        we should continue from that iteration and ignore the current time.
        This allows returning an overdue deadline potentially caused by a
        high delta, letting missed iterations fire ASAP.

        Given the above requirements, this would be the ideal solution:
            time = min(self.clock.time, self._expected_time)

        However, this is complicated by SleepHandler which does not guarantee
        that a successful iteration will never be earlier than the deadline.
        As such, we will additionally prevent the time from being sooner than
        the last successful iteration.
        If we ignored this and allowed deadlines earlier than the last iteration,
        the above solution could potentially trigger non-missed iterations too early.

        Args:
            period (Union[float, int]):
                The number of beats in the interval.

        Returns:
            float: The deadline for the next interval.
        """
        time = max(self._last_expected_time, min(self.clock.time, self._expected_time))

        self._check_snap(time)
        if self.snap is not None:
            return self.snap

        shifted_time = time + self.interval_shift

        # If the interval was corrected, this should equal to:
        #    `period * beat_duration`
        expected_duration = self.clock.get_beat_time(period, time=shifted_time)
        return time + expected_duration

    # Runner loop

    async def _runner(self):
        """
        This function defines the main loop for the runner. TODO: add more.
        """

        # Query time position
        current_beat = self.clock.beat % self.clock.beats_per_bar
        current_bar = self.clock.bar
        current_phase = self.clock.phase

        # Preparing the runner for an incoming iteration
        try:
            self._prepare()
        except Exception as exc:
            self._revert_state()
            raise exc

        if not self.background_job:
            print(f"[yellow][[red]{self.name}[/red] is swimming][/yellow]")

        try:
            while self._is_ready_for_iteration():
                # We try to run the function for real now, catching generic exception!
                try:
                    await self._run_once()
                except Exception as exc:
                    print(f"[red][Function exception | ({self.name})]")
                    traceback.print_exception(type(exc), exc, exc.__traceback__)
                    # Revert the state to the previous one to try to 'save' the runner
                    self._revert_state()
                    self.swim()
        finally:
            print(f"[yellow][Stopped [red]{self.name}[/red]][/yellow]")

    def _prepare(self):
        """
        Prepare the runner for an incoming iteration. This method is called at the
        start of the runner's main loop. TODO: add more.
        """
        self._last_expected_time = self.clock.time
        self._last_state = self._get_state()

        # Setting flags
        self._swimming = True
        self._stop = False

        # Extract period from state
        period = self._get_period(self._last_state)

        self._last_interval = period * self.clock.beat_duration

    async def _run_once(self) -> None:
        """
        This function is called once per iteration. It is responsible for running the function
        and handling any deferred states that have arrived. TODO: complete description.
        """
        # TODO: documentation needed for this very complex function

        self._swimming = False
        self._reload_event.clear()

        # 1) Get the last state
        state = self._get_state()
        if state is not None:
            self._maybe_print_new_state(state)
            self._last_state = state
            signature = inspect.signature(state.func)

            _assert_function_signature(signature, state.args, state.kwargs)
            args = state.args
            # Prevent any TypeErrors when the user reduces the signature
            kwargs = _discard_kwargs(signature, state.kwargs)
            period = _extract_new_period(signature, state.kwargs, self._default_period)

            # TODO: what are we doing here?
            if not self.background_job:
                self._correct_interval(period)
            else:
                self._correct_interval_background_job(period)
            deadline = self._get_next_deadline(period)

        # 2) Push any deferred states that have arrived or will arrive onto the stack
        arriving_states: list[DeferredState] = []
        while self.deferred_states:
            entry = self.deferred_states[0]
            if (
                self.clock.time >= entry.deadline
                or state is not None
                and deadline >= entry.deadline
            ):
                heapq.heappop(self.deferred_states)

                # Transfer any arguments from previous function if any
                # (similar to what `push()` does)
                if state is not None:
                    self._merge_states(state, entry.state)
                arriving_states.append(entry)
            else:
                break

        # 3) Run the function or skip to the next iteration
        if arriving_states:
            latest_entry = arriving_states[-1]
            self.states.extend(e.state for e in arriving_states)
            # In case the new state has a faster interval than before, delay it so it doesn't run too early
            self.delay_interval(
                latest_entry.deadline,
                self._get_period(latest_entry.state),
            )
            return self._skip_iteration()
        elif state is None:
            # Nothing to do until the next deferred state arrives.
            #
            # However, we will need to jump-start the next iteration
            # so it runs exactly on the deadline instead of unnecessarily
            # sleeping a full period.
            deadline = self.deferred_states[0].deadline
            # interrupted is true if we are past the deadline
            interrupted = await self._sleep_until(deadline)
            return self._jump_start_iteration()

        # NOTE: deadline will always be defined at this point
        if not self.background_job:
            # interrupted is true if we are past the deadline
            interrupted = await self._sleep_unless_jump_started(deadline)
            if interrupted:
                return self._skip_iteration()

        try:
            # Use copied context in function by creating it as a task
            await asyncio.create_task(
                self._call_func(state.func, args, kwargs),
                name=f"asyncrunner-func-{self.name}",
            )
        finally:
            self._last_expected_time = self._expected_time
            self._update_iter()

    def _update_iter(self) -> None:
        """Updates the iteration number"""
        self._iter += self._iter_step
        if self._iter_limit != "inf":
            if self._iter >= self._iter_limit:
                self._iter = 0

    async def _call_func(self, func, args, kwargs):
        """Calls the given function and optionally applies time shift
        according to the `defer_beats` attribute.
        """
        signature = inspect.signature(func)
        valid_kwargs = {k: v for k, v in kwargs.items() if k in signature.parameters}

        if self.defer_beats:
            delta = self.clock.time - self._expected_time
            shift = self.defer_beats * self.clock.beat_duration - delta
            self.time.shift += shift

        return await maybe_coro(func, *args, **valid_kwargs)

    def _get_period(self, state: Optional[FunctionState]) -> Union[float, int]:
        """
        TODO: ???

        Args:
            state (Optional[FunctionState]): A function state (typically the
            most recent one).

        Returns:
            Union[float, int]: The period to use for the next iteration.
        """
        # If we don't have a state, we can't extract a period given by the user
        if state is None:
            return 0.0

        # Extract the period from the state or assign default period if missing
        return _extract_new_period(
            inspect.signature(state.func),
            state.kwargs,
            self.period,
        )

    def _get_state(self) -> Optional[FunctionState]:
        """
        Returns the top-most function state, if any.

        Returns:
            Optional[FunctionState]: The top-most function state.
        """
        return self.states[-1] if self.states else None

    def _is_ready_for_iteration(self) -> bool:
        """
        Conditions for the runner to be ready for the next iteration.

        Returns:
            bool: True if the runner is ready for the next iteration.
        """
        return bool(
            (self.states or self.deferred_states)
            and self._swimming  # self.swim()
            and not self._stop  # self.stop()
        )

    def _maybe_print_new_state(self, state: FunctionState) -> None:
        """
        This function is called when the runner is about to start a new iteration and
        a new state has been pushed. It will print a message to the console indicating
        how well the runner is doing (update or saved from crash).
        """
        current_beat = self.clock.beat % self.clock.beats_per_bar
        current_bar = self.clock.bar
        current_phase = self.clock.phase

        if self._last_state is not None and state is not self._last_state:
            if not self._has_reverted:
                print(f"[yellow][Updating [red]{self.name}[/red]]")
            else:
                print(f"[yellow][Saving [red]{self.name}[/red] from crash]")
                self._has_reverted = False

    async def _sleep_until(self, deadline: Union[float, int]) -> bool:
        """Sleeps until the given deadline or until the runner is reloaded.

        Args:
            duration (Union[float, int]): The amount of time to sleep.

        Returns:
            bool: True if the runner was reloaded, False otherwise.
        """
        self._expected_time = deadline
        if self.clock.time >= deadline:
            return self._reload_event.is_set()

        wait_task = asyncio.create_task(self.env.sleeper.sleep_until(deadline))
        reload_task = asyncio.create_task(self._reload_event.wait())
        try:
            done, pending = await asyncio.wait(
                # here, we are waiting for one of the two tasks to finish:
                # - wait task: a task that sleeps until deadline
                # - reload task: a task that waits until the runner is reloaded
                (wait_task, reload_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # The other task is cancelled, we don't need it anymore
            for task in pending:
                task.cancel()
            # Retrieving the result, helpful for error propagation, etc.
            for task in done:
                task.result()

            return reload_task in done

        except asyncio.CancelledError:
            os.system("cls" if os.name == "nt" else "clear")
            print(
                Panel.fit(
                    "[red]/!\ Exit Pressed: Sardine was interrupted. Press Ctrl-C again to quit![/red]"
                )
            )

    async def _sleep_unless_jump_started(self, deadline: Union[float, int]) -> bool:
        if self._jump_start:
            self._jump_start = False
            return False

        return await self._sleep_until(deadline)

    def _revert_state(self) -> None:
        """Reverts the runner to the previous state."""
        if self.states:
            self.states.pop()
        self._has_reverted = True

    def _skip_iteration(self) -> None:
        """
        TODO: document
        """
        self.swim()

    def _jump_start_iteration(self) -> None:
        self._jump_start = True
        self._skip_iteration()

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return  # Suppress CancelledError

        task.result()  # Raise any exception if present
