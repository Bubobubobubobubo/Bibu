import importlib
import sys
from functools import wraps
from itertools import product
from pathlib import Path
from string import ascii_lowercase, ascii_uppercase
from typing import Any, Callable, Optional, ParamSpec, TypeVar, Union, overload

from ziffers import z

from . import *
from .io.UserConfig import read_extension_configuration, read_user_configuration
from .logger import print
from .sequences import ListParser, ziffers_factory
from .sequences.tidal_parser import *
from .superdirt import SuperDirtProcess
from .utils import Quant, get_deadline_from_quant, greeter_printer, join, sardine_intro

ParamSpec = ParamSpec("PS")
T = TypeVar("T")


#######################################################################################
# READING USER CONFIGURATION (TAKEN FROM SARDINE-CONFIG)

config = read_user_configuration()
print(greeter_printer(sardine_intro, config))


# Initialisation of the FishBowl (the environment holding everything together)

clock = LinkClock if config.link_clock else InternalClock
parser = ListParser
bowl = FishBowl(
    clock=clock(tempo=config.bpm, bpb=config.beats),
    parser=parser(),
)

#######################################################################################
# OPENING SUPERCOLLIDER/SUPERDIRT SUBPROCESS. DISSOCIATED FROM THE SUPERDIRT HANDLERS.

config = read_user_configuration()
if config.boot_supercollider:
    try:
        SC = SuperDirtProcess(
            startup_file=(
                config.superdirt_config_path if config.sardine_boot_file else None
            ),
            verbose=config.verbose_superdirt,
        )
    except OSError as Error:
        print(f"[red]SuperCollider could not be found: {Error}![/red]")

#######################################################################################
# HANDLERS INITIALIZATION. YOU CAN ADD YOUR MODULAR COMPONENTS HERE.

try:
    # MIDI Handler: matching with the MIDI port defined in the configuration file
    midi = MidiHandler(port_name=str(config.midi))
    bowl.add_handler(midi)
    midi._ziffers_parser = z
except OSError as e:
    print(f"{e}: [red]Invalid MIDI port![/red]")

try:
    # MIDIIn Handler
    midi_in = MidiInHandler(port_name=config.midi)
    bowl.add_handler(midi_in)
    midi._ziffers_parser = z
except OSError as e:
    print(f"{e}: [red]Invalid MIDI port![/red]")


# OSC Loop: handles processing OSC messages
osc_loop = OSCLoop()
bowl.add_handler(osc_loop)  # NOTE: always keep this loop running for OSC handlers

# OSC Handler: dummy OSC handler, mostly used for test purposes
dummy_osc = OSCHandler(
    ip="127.0.0.1",
    port=12345,
    name="Dummy OSC Connexion",
    ahead_amount=0.0,
    loop=osc_loop,
)
O = dummy_osc.send


# # OSC Listener Handler: dummy OSCIn handler, used for test purposes
my_osc_listener = OSCInHandler(
    ip="127.0.0.1", port=23456, name="Dummy OSC Listener", loop=osc_loop
)


# SuperDirt Handler: conditional
if config.superdirt_handler:
    dirt = SuperDirtHandler(loop=osc_loop)
    dirt._ziffers_parser = z

# Adding Players
# player_names = ["P" + l for l in ascii_lowercase + ascii_uppercase]
player_names = [
    "".join(tup) for tup in product(ascii_lowercase + ascii_uppercase, repeat=2)
]
player_names.remove("SC")  # NOTE: used by SuperCollider command
player_names.remove("PC")  # NOTE: used by MIDI Program Change
# player_names += [''.join(tup) for tup in list(product(ascii_lowercase, repeat=3))]
for player in player_names:
    p = Player(name=player)
    globals()[player] = p
    bowl.add_handler(p)
    del p

# Extensions
# An extension configuration file contains the following fields:
# - root: filepath to the directory containing the extension package
#   (must be added to the Python path before importing the package)
# - package: the extension package name
# - handlers: a list of sardine handlers that can be found in the package,
#   each one of them containing the following fields:
#   - module: the package's module containing the handler
#   - class: the handler's class name
#   - send_alias: an alias for the handler's send method that will be part of
#     this session's global variables
#     (make sure it does not conflict with any other global alias)
#   - params: a dictionary with the handler's initialization parameters
for ext_config_path in config.extensions:
    ext_config = read_extension_configuration(ext_config_path)
    sys.path.append(ext_config["root"])
    for ext_handler in ext_config["handlers"]:
        module = importlib.import_module(
            f'{ext_config["package"]}.{ext_handler["module"]}'
        )
        cls = getattr(module, ext_handler["class"])
        instance = cls(ext_handler["params"])
        globals()[ext_handler["send_alias"]] = instance.send
        bowl.add_handler(instance)

#######################################################################################
# BASIC MECHANISMS: SWIMMING, DELAY, SLEEP AND OTHER IMPORTANT CONSTRUCTS


def for_(n: int) -> Callable[[Callable[ParamSpec, T]], Callable[ParamSpec, T]]:
    """Allows to play a swimming function x times. It swims for_ n iterations."""

    def decorator(func: Callable[ParamSpec, T]) -> Callable[ParamSpec, T]:
        @wraps(func)
        def wrapper(*args: ParamSpec.args, **kwargs: ParamSpec.kwargs) -> T:
            nonlocal n
            n -= 1
            if n >= 0:
                return func(*args, **kwargs)

        return wrapper

    return decorator


@overload
def swim(
    func: Union[Callable[ParamSpec, Any], AsyncRunner],
    /,
    # NOTE: AsyncRunner doesn't support generic args/kwargs
    *args: ParamSpec.args,
    quant: Quant = "bar",
    until: Optional[int] = None,
    **kwargs: ParamSpec.kwargs,
) -> AsyncRunner: ...


@overload
def swim(
    *args,
    quant: Quant = "bar",
    until: Optional[int] = None,
    **kwargs,
) -> Callable[[Union[Callable, AsyncRunner]], AsyncRunner]: ...


# FIXME: quant docstring is outdated
# pylint: disable=keyword-arg-before-vararg  # signature is valid
def swim(
    func: Optional[Union[Callable, AsyncRunner]] = None,
    /,
    *args,
    quant: Quant = "bar",
    until: Optional[int] = None,
    background_job: bool = False,
    **kwargs,
):
    """
    Swimming decorator: push a function to the scheduler. The function will be
    declared and followed by the scheduler system to recurse in time if needed.

    Args:
        func (Optional[Union[Callable[P, T], AsyncRunner]]):
            The function to be scheduled. If this is an AsyncRunner,
            the current state is simply updated with new arguments.
        *args: Positional arguments to be passed to `func.`
        quant (Quant):
            If set to a numeric value, the new function will be
            deferred until the next bar + `quant` beats arrives.
            If None, the function is immediately pushed and will
            run on its next interval.
            If `func` is an AsyncRunner, this parameter has no effect.
        until (Optional[int]):
            Specifies the number of iterations this function should run for.
            This is a shorthand for using the `@for_()` decorator.
        background_job (bool):
            Determines if the asyncrunner is a background job or not. Being a
            background job isolates the asyncrunner from any interruption by
            the user (silence() or panic()).
        **kwargs: Keyword arguments to be passed to `func.`
    """

    def decorator(func: Union[Callable, AsyncRunner], /) -> AsyncRunner:

        # This is true when the function is already running on the scheduler
        if isinstance(func, AsyncRunner):
            func.update_state(*args, **kwargs)
            bowl.scheduler.start_runner(func)
            return func

        if until is not None:
            func = for_(until)(func)

        # Checks if the runner already exists, otherwise, create a new one
        runner = bowl.scheduler.get_runner(func.__name__)
        if runner is None:
            runner = AsyncRunner(func.__name__)
            # Some AsyncRunners need to stay in the background and never be
            # interrupted by any user action (c.f. Tidal Vortex Loop)
            if background_job:
                runner.background_job = True
        elif not runner.is_running():
            # Runner has likely stopped swimming, in which case we should
            # make sure the old state doesn't pollute the new function
            # when it's pushed
            runner.reset_states()

        # Runners normally allow the same functions to appear in the stack,
        # but we will treat repeat functions as just reloading the runner
        if runner.states and runner.states[-1].func is func:
            again(runner)
            bowl.scheduler.start_runner(runner)
            return runner

        # We apply the 'quant' policy (start now, on next beat, bar, etc)
        deadline = get_deadline_from_quant(bowl.clock, quant)
        # Deadline is None when the 'quant' policy is now
        if deadline is None:
            runner.push(func, *args, **kwargs)
        else:
            runner.push_deferred(deadline, func, *args, **kwargs)

        # Intentionally avoid interval correction so the user doesn't
        # accidentally nudge the runner
        runner.swim()
        runner.reload()

        bowl.scheduler.start_runner(runner)
        return runner

    if func is not None:
        return decorator(func)
    return decorator


def again(runner: AsyncRunner, *args, **kwargs):
    """
    Keep a runner swimming. User functions should continuously call this
    at the end of their function until they want the function to stop.
    """
    runner.update_state(*args, **kwargs)
    runner.swim()
    # If this is manually called we should wake up the runner sooner
    runner.reload()


def die(func: Union[Callable, AsyncRunner]) -> AsyncRunner:
    """
    Swimming decorator: remove a function from the scheduler. The function
    will not be called again and will likely stop recursing in time.
    """
    if isinstance(func, AsyncRunner):
        bowl.scheduler.stop_runner(func)
        return func

    runner = bowl.scheduler.get_runner(func.__name__)
    if runner is not None:
        bowl.scheduler.stop_runner(runner)
    else:
        runner = AsyncRunner(func.__name__)
        runner.push(func)
    return runner


def sleep(n_beats: Union[int, float]):
    """Artificially sleep in the current function for `n_beats`.

    Example usage: ::

        @swim
        def func(p=4):
            sleep(3)
            for _ in range(3):
                S('909').out()
                sleep(1/2)
            again(func)

    This should *only* be called inside swimming functions.
    Unusual behaviour may occur if sleeping is done globally.

    Using in asynchronous functions
    -------------------------------

    This can be used in `async def` functions and does *not* need to be awaited.

    Sounds scheduled in asynchronous functions will be influenced by
    real time passing. For example, if you sleep for 500ms (based on tempo)
    and await a function that takes 100ms to complete, any sounds sent
    afterwards will occur 600ms from when the function was called.

    ::

        @swim
        async def func(p=4):
            print(bowl.clock.time)  # 0.0s

            sleep(1)     # virtual +500ms (assuming bowl.clock.tempo = 120)
            await abc()  # real +100ms

            S('bd').out()           # occurs 500ms from now
            print(bowl.clock.time)  # 0.6s
            again(func)

    Technical Details
    -----------------

    Unlike `time.sleep(n)`, this function does not actually block
    the function from running. Instead, it temporarily affects the
    value of `BaseClock.time` and extends the perceived time of methods
    using that property, like `SleepHandler.wait_after()`
    and `BaseClock.get_beat_time()`.

    In essence, this maintains the precision of sound scheduling
    without requiring the use of declarative syntax like
    `S('909', at=1/2).out()`.

    """
    duration = bowl.clock.get_beat_time(n_beats, sync=False)
    bowl.time.shift += duration


def silence(*runners: AsyncRunner) -> None:
    """
    Silence is capable of stopping one or all currently running swimming functions. The
    function will also trigger a general MIDI note_off event (all channels, all notes).
    This function will only kill events on the Sardine side. For a function capable of
    killing synthesizers running on SuperCollider, try the more potent 'panic' function.
    """

    if len(runners) == 0:
        midi.all_notes_off()
        bowl.scheduler.reset()
        hush()
        return

    for run in runners:
        if isinstance(run, Player):
            run.stop()
        else:
            if not run.background_job:
                bowl.scheduler.stop_runner(run)
        if config.superdirt_handler:
            hush()


def solo(*args):
    """Soloing a single player out of all running players, excluding background job."""
    foreground_runner_names = {
        runner.name for runner in bowl.scheduler.runners if not runner.background_job
    }
    args_names = {runner.name for runner in args}
    names_to_silence = foreground_runner_names - args_names
    for runner in bowl.scheduler.runners:
        if runner.name in names_to_silence:
            silence(runner)


def runners():
    """Return all currently active AsyncRunners"""
    condition = lambda x: x.name if x.name != "tidal_loop" else "internal"
    return list(map(condition, bowl.scheduler.runners))


def panic(*runners: AsyncRunner) -> None:
    """
    If SuperCollider/SuperDirt is booted, panic acts as a more powerful alternative to
    silence() capable of killing synths on-the-fly. Use as a last ressource if you are
    loosing control of the system.
    """
    silence(*runners)
    if config.superdirt_handler:
        D("superpanic")
        D("superpanic")


def Pat(pattern: str, i: int = 0, div: int = 1, rate: int = 1) -> Any:
    """
    General purpose pattern interface. This function can be used to summon the global
    parser stored in the fish_bowl. It is generally used to pattern outside of the
    handler/sender system, if you are playing with custom libraries, imported code or
    if you want to take the best of the patterning system without having to deal with
    all the built-in I/O.

    The as_text argument allows the study of patterns in textual format. If as_text is
    true, the pattern will print from index 0 up to i.

    Args:
        pattern (str): A pattern to be parsed
        i (int, optional): Index for iterators. Defaults to 0.

    Returns:
        int: The ith element from the resulting pattern
    """
    return Sender.pattern_element(bowl.parser.parse(pattern), i, div, rate)


class Delay:
    """
    Delay is a compound statement providing an alternative syntax to the overridden
    sleep() method. It implements the bare minimum to reproduce sleep behavior using
    extra indentation for marking visually where sleep takes effect.
    """

    def __init__(self, duration: Union[int, float] = 1, delayFirst: bool = True):
        self.duration = duration
        self.delayFirst = delayFirst

    def __call__(self, duration=1, delayFirst=False):
        self.duration = duration
        self.delayFirst = delayFirst
        return self

    def __enter__(self):
        if self.delayFirst:
            sleep(self.duration)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.delayFirst:
            sleep(self.duration)


#######################################################################################
# DEFINITION OF ALIASES

clock = bowl.clock

I, V = bowl.iterators, bowl.variables  # Iterators and Variables from env
P = Pat  # Generic pattern interface

N = midi.send  # For sending MIDI Notes
ZN = midi.send_ziffers
PC = midi.send_program  # For MIDI Program changes
CC = midi.send_control  # For MIDI Control Change messages
SY = midi.send_sysex  # For MIDI Sysex messages
_play_factory = Player._play_factory


def sy(*args, **kwargs):
    return _play_factory(midi, midi.send_sysex, *args, **kwargs)


def n(*args, **kwargs):
    return _play_factory(midi, midi.send, *args, **kwargs)


def zn(*args, **kwargs):
    return _play_factory(midi, midi.send_ziffers, *args, **kwargs)


def cc(*args, **kwargs):
    return _play_factory(midi, midi.send_control, *args, **kwargs)


def pc(*args, **kwargs):
    return _play_factory(midi, midi.send_program, *args, **kwargs)


if config.superdirt_handler:
    D = dirt.send
    ZD = dirt.send_ziffers

    def d(*args, **kwargs):
        return _play_factory(dirt, dirt.send, *args, **kwargs)

    def zd(*args, **kwargs):
        return _play_factory(dirt, dirt.send_ziffers, *args, **kwargs)

    zplay = ziffers_factory.create_zplay(D, N, sleep, swim)


def MIDIInstrument(
    midi: MidiHandler,
    channel: int,
    instrument_map: dict[dict],
    *args,
    **kwargs,
) -> tuple:
    """
    Make a new MIDIInstrument from the definition of an instrument map:
    - a device that can play midi notes
    - a device that can also receive named CC parameters

    Given an instrument map like so:

    hat_drum = {
       'x': { 'control': 21, 'channel': 0, },
       'y': { 'control': 22, 'channel': 0, },
       't': { 'control': 23, 'channel': 0, },
       'len': { 'control': 24, 'channel': 0, },
       'quality': { 'control': 25, 'channel': 0, },
    }

    This function will return a sender capable of playing with a MIDI
    instrument that uses these mappings to control synthesis parameters.

    H = MIDIInstrument(midi_port=midi, channel=0, map=hat_drum)

    H('C E G', x='rand*120')

    This function will return a tuple containing a new MIDIInstrument and
    a new Player based on that MIDIInstrument.
    ...
    """

    def midi_instrument(*args, **kwargs):
        """Build a new sender like D() out of the midi instrument information"""
        midi.send_instrument(channel=channel, map=instrument_map, *args, **kwargs)

    def midi_instrument_player(*args, **kwargs):
        """Build a new palyer like d() out of the midi instrument information"""
        return _play_factory(
            midi,
            midi.send_instrument,
            channel=channel,
            map=instrument_map,
            *args,
            **kwargs,
        )

    return (midi_instrument, midi_instrument_player)


def MIDIController(
    midi: MidiHandler,
    channel: int,
    controller_map: dict[dict],
    *args,
    **kwargs,
) -> tuple:
    """
    Make a new MIDIController from the definition of a controller map:
    - a device that can play midi notes
    - a device that can also receive named CC parameters

    Given a controller map like so:

    hat_drum = {
       'reverb': { 'control': 21, 'channel': 0, },
       'delay': { 'control': 22, 'channel': 0, },
       'chorus': { 'control': 23, 'channel': 0, },
       'flanger': { 'control': 24, 'channel': 0, },
       'quality': { 'control': 25, 'channel': 0, },
    }

    This function will return a sender capable of playing with a MIDI
    controller that uses these mappings to control synthesis parameters.

    H, h = MIDIController(midi_port=midi, channel=0, map=hat_drum)

    H('C E G', x='rand*120')

    This function will return a tuple containing a new MIDIController and
    a new Player based on that MIDIController.
    ...
    """

    def midi_controller(*args, **kwargs):
        """Build a new sender like D() out of the midi controller information"""
        midi.send_controller(channel=channel, map=controller_map, *args, **kwargs)

    def midi_controller_player(*args, **kwargs):
        """Build a new player like d() out of the midi controller information"""
        return _play_factory(
            midi,
            midi.send_controller,
            channel=channel,
            map=controller_map,
            *args,
            **kwargs,
        )

    return (midi_controller, midi_controller_player)


#######################################################################################
# VORTEX

if config.superdirt_handler:
    tidal = tidal_factory(
        osc_client=dirt, env=bowl, tidal_players=bowl._vortex_subscribers
    )
    hush = hush_factory(
        osc_client=dirt, env=bowl, tidal_players=bowl._vortex_subscribers
    )

    class TidalD:
        def __init__(self, name: str, orbit_number: int):
            self.name = name
            self.orbit_number = orbit_number
            self.player: Optional[tidal] = None

        def __mul__(self, pattern):
            self.player = tidal(self.name, pattern.orbit(self.orbit_number))
            return self.player

        def _stream(self):
            """Return the last message played by this stream/player"""
            lst = self.player._last_value
            return {lst[i]: lst[i + 1] for i in range(0, len(lst), 2)}

        @property
        def stream(self):
            """Getter for the stream"""
            data = self._stream()
            return {} if data is None else data

    d1, d2, d3, d4, d5, d6, d7, d8, d9 = (
        TidalD(name="d1", orbit_number=0),
        TidalD(name="d2", orbit_number=1),
        TidalD(name="d3", orbit_number=2),
        TidalD(name="d4", orbit_number=3),
        TidalD(name="d5", orbit_number=4),
        TidalD(name="d6", orbit_number=5),
        TidalD(name="d7", orbit_number=6),
        TidalD(name="d8", orbit_number=7),
        TidalD(name="d9", orbit_number=8),
    )

    # Background asyncrunner for running tidal patterns
    @swim(background_job=True, quant=dirt.nudge)
    def tidal_loop(p=0.05):
        """Background Tidal/Vortex AsyncRunner:
        Notify Tidal Streams of the current passage of time.
        """
        clock.tick += 1

        # Logical time since the clock started ticking: sum of frames
        logical_now, logical_next = (
            clock.internal_origin + (clock.tick * clock._framerate),
            clock.internal_origin + ((clock.tick + 1) * clock._framerate),
        )

        # Current time (needed for knowing wall clock time)
        now = clock.shifted_time + clock._tidal_nudge

        # Wall clock time for the "ideal" logical time
        cycle_from, cycle_to = (
            clock.beatAtTime(logical_now) / (clock.beats_per_bar * 2),
            clock.beatAtTime(logical_next) / (clock.beats_per_bar * 2),
        )

        # Sending to each individual subscriber for scheduling using timestamps
        try:
            for sub in bowl._vortex_subscribers:
                sub.notify_tick(
                    clock=clock,
                    cycle=(cycle_from, cycle_to),
                    cycles_per_second=clock.cps,
                    beats_per_cycle=(clock.beats_per_bar * 2),
                    now=now,
                )
        except Exception as e:
            print(e)
        # clock._notify_tidal_streams()
        again(tidal_loop, p=0.05)


#######################################################################################
# CLOCK START: THE SESSION IS NOW LIVE
bowl.start()


#######################################################################################
# LOADING USER CONFIGURATION

if Path(f"{config.user_config_path}").is_file():
    exec(open(config.user_config_path).read())
else:
    print(f"[red]No user provided configuration file found...")


def spl_debug():
    while True:
        try:
            user_input = input("> ")
            if user_input == "exit":
                break
            message = bowl.parser._parse_debug(user_input)
        except Exception as e:
            print(e)
