# SPDX-License-Identifier: Apache-2.0
"""One-second wall-clock guard for an IntegratedSelectedAgent.

The guard preserves the one-controller contract.  Before selection, the only
universally legal fallback is PASS for every observed worker.  Once the producer
has selected an action, that exact action becomes the fallback while the ordered
SELL transform runs.  No parent/controller method is called a second time.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
import signal
import sys
import threading
import time


class DeadlineExceeded(BaseException):
    """Control-flow cancellation, not an ordinary recoverable policy error.

    Production and transform helpers may catch Exception to supply a default.
    The guard's one-shot deadline must cross those handlers and be consumed only
    by DeadlineFallbackAgent.act, or execution continues with its alarm spent.
    """


_ACTIVE_TIMER = ContextVar("titan_active_deadline_timer", default=None)


def _alarm(_signum, _frame):
    timer = _ACTIVE_TIMER.get()
    if timer is not None:
        raise timer.expired
    raise DeadlineExceeded("action deadline exhausted")


def legal_pass(observation):
    seat = int(observation["player"])
    hands = observation["farms"][seat].get("hands", [])
    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


def terminal_liquidation_fallback(observation, configuration=None):
    """Visible-state final action: place reachable cargo, then sell every shed lot.

    Kaggriculture has nine products and a ten-order market limit, so the fallback
    can liquidate every positive post-unit shed lot without pruning.  Workers not
    already on a shed-access tile PASS; there is no speculative movement.
    """
    obs = observation; cfg = dict(configuration or {})
    seat = int(obs["player"]); farm = obs["farms"][seat]; private = obs["private"]
    board = int(cfg.get("boardSize", len(farm["tiles"])))
    half = board // 2
    access = {(half-1, half-1), (half, half-1), (half-1, half), (half, half)}
    shed = {p: max(0, int(q)) for p, q in private["shed"].items()}
    free = max(0, int(cfg.get("shedCapacity", 100)) - sum(shed.values()))
    positions = [farm["farmer"], *farm.get("hands", [])]
    inventories = private.get("inventories", [])
    units = []
    for index, position in enumerate(positions):
        action = ["PASS"]
        inventory = inventories[index] if index < len(inventories) else {}
        if tuple(position) in access and any(int(q) > 0 for q in inventory.values()):
            # DROP is the engine's multi-product terminal transfer. Preserve the
            # observation's inventory order and capacity clamp exactly.
            action = ["DROP"]
            for product, quantity in inventory.items():
                placed = min(free, max(0, int(quantity)))
                shed[product] = shed.get(product, 0) + placed
                free -= placed
        units.append(action)
    market = [["SELL", product, quantity] for product, quantity in shed.items()
              if quantity > 0]
    maximum = int(cfg.get("maxMarketOrdersPerTurn", 10))
    return {"farmer": units[0], "hands": units[1:], "market": market[:maximum]}


class _DeadlineTimer:
    """Share ITIMER_REAL with the caller without restarting its deadline.

    Workers use a thread-local trace guard and never access signal handlers or
    timers. Python execution is cancelled at trace boundaries; a blocking native
    call must return before cancellation can be delivered. The outer runner
    still owns its whole-call timeout, including imports and serialization.

    The main-thread scope schedules the earlier of its own deadline and the
    caller's alarm. A caller handler is invoked with its original signal/frame;
    any exception it raises remains a caller exception, not our fallback signal.
    """
    def __init__(self, seconds):
        self.seconds = seconds
        self.expired = DeadlineExceeded("action deadline exhausted")

    def __enter__(self):
        self.thread_guard = threading.current_thread() is not threading.main_thread()
        if self.thread_guard:
            return self._enter_thread(sys._getframe(1))
        self.previous = signal.getsignal(signal.SIGALRM)
        # Validate main-thread signal access before touching the caller timer.
        signal.signal(signal.SIGALRM, self.previous)
        now = time.monotonic()
        remaining, self.outer_interval = signal.setitimer(signal.ITIMER_REAL, 0)
        self.outer_at = now + remaining if remaining > 0 else None
        self.own_at = now + self.seconds
        self.caller_timer = _ACTIVE_TIMER.get()
        self.context_token = _ACTIVE_TIMER.set(self)
        try:
            signal.signal(signal.SIGALRM, self._dispatch)
            self._schedule()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _enter_thread(self, caller):
        self.own_at = time.monotonic() + self.seconds
        self.previous_trace = sys.gettrace()
        self.caller_frame = caller
        self.previous_local = caller.f_trace
        self.local_traces = {caller: self.previous_local}
        # A debugger may have disabled line events. The guard needs those events
        # for inline loops, independently of the caller's tracing preferences.
        self.local_trace_lines = {caller: caller.f_trace_lines}
        self.context_token = _ACTIVE_TIMER.set(self)
        self.trace_ticks = 0
        self.trace_fired = False
        try:
            trace = (self._trace_plain if self.previous_trace is None and
                     self.previous_local is None else self._trace)
            sys.settrace(trace)
            caller.f_trace = trace
            caller.f_trace_lines = True
            return self
        except BaseException:
            self._exit_thread()
            raise

    def _trace_plain(self, frame, event, arg):
        # No caller tracer is installed in the normal runtime. Avoid per-line
        # multiplexing, but still preserve live and suspended frame settings.
        if frame.f_code.co_filename == __file__:
            return None
        next_trace = self._trace_plain
        if event == "call":
            self.local_traces[frame] = frame.f_trace
            self.local_trace_lines[frame] = frame.f_trace_lines
            frame.f_trace_lines = True
        elif event == "return":
            next_trace = self.local_traces.pop(frame, None)
            frame.f_trace = next_trace
            frame.f_trace_lines = self.local_trace_lines.pop(frame, True)
        self.trace_ticks += 1
        if event in ("call", "return") or self.trace_ticks >= 64:
            self.trace_ticks = 0
            if not self.trace_fired and time.monotonic() >= self.own_at:
                self.trace_fired = True
                raise self.expired
        return next_trace

    def _trace(self, frame, event, arg):
        # Never interrupt guard setup/cleanup. In particular __exit__ must run
        # even when the body raised or tracing disabled itself on cancellation.
        if frame.f_code.co_filename == __file__:
            return None
        prior = self.previous_trace if event == "call" else self.local_traces.get(frame)
        prior_lines = self.local_trace_lines.get(frame, frame.f_trace_lines)
        self.local_trace_lines[frame] = prior_lines
        if prior is not None and (event != "line" or prior_lines):
            # Show the caller its own frame settings, not our forced line flag.
            # Preserve explicit f_trace assignments and CPython's local-None
            # behavior, while retaining a private callback for guard dispatch.
            frame.f_trace = self.local_traces.get(
                frame, frame.f_trace if event == "call" else None)
            frame.f_trace_lines = prior_lines
            try:
                replacement = prior(frame, event, arg)
                if replacement is not None:
                    frame.f_trace = replacement
            finally:
                self.local_traces[frame] = frame.f_trace
                self.local_trace_lines[frame] = frame.f_trace_lines
                frame.f_trace = self._trace
                frame.f_trace_lines = True
        else:
            frame.f_trace_lines = True
        next_trace = self._trace
        if event == "return":
            # A generator yield is also a return event. Restore that suspended
            # frame now, without retaining completed frames until scope exit.
            next_trace = self.local_traces.pop(frame, None)
            frame.f_trace = next_trace
            frame.f_trace_lines = self.local_trace_lines.pop(frame)
        self.trace_ticks += 1
        if event in ("call", "return") or self.trace_ticks >= 64:
            self.trace_ticks = 0
            if not self.trace_fired and time.monotonic() >= self.own_at:
                self.trace_fired = True
                raise self.expired
        return next_trace

    def _exit_thread(self):
        # A trace callback exception clears sys.gettrace(). Restore global and
        # per-frame state, including muted line flags and interrupted frames.
        sys.settrace(self.previous_trace)
        for frame, prior_lines in self.local_trace_lines.items():
            frame.f_trace = self.local_traces.get(frame)
            frame.f_trace_lines = prior_lines
        self.local_traces.clear()
        self.local_trace_lines.clear()
        self.caller_frame = None
        _ACTIVE_TIMER.reset(self.context_token)

    @staticmethod
    def _remaining(deadline):
        # Zero disables a timer, so an overdue caller alarm must use a positive
        # delay when control returns to its original handler.
        return max(0.000001, deadline - time.monotonic())

    def _schedule(self):
        deadline = self.own_at
        if self.outer_at is not None:
            deadline = min(deadline, self.outer_at)
        signal.setitimer(signal.ITIMER_REAL, self._remaining(deadline))

    def _deliver_outer(self, signum, frame):
        now = time.monotonic()
        if self.outer_interval > 0:
            # Preserve periodic cadence; missed ticks coalesce like SIGALRM.
            missed = max(0, int((now - self.outer_at) // self.outer_interval))
            self.outer_at += (missed + 1) * self.outer_interval
        else:
            self.outer_at = None
        signal.setitimer(signal.ITIMER_REAL,
                         self._remaining(self.outer_at) if self.outer_at is not None else 0,
                         self.outer_interval)
        caller_token = _ACTIVE_TIMER.set(self.caller_timer)
        try:
            # Let the caller observe and replace its own binding. Keep our
            # dispatcher installed again while the guarded work resumes.
            signal.signal(signal.SIGALRM, self.previous)
            if callable(self.previous):
                self.previous(signum, frame)
            elif self.previous == signal.SIG_DFL:
                # Respect the caller's default action rather than swallowing it.
                signal.signal(signal.SIGALRM, signal.SIG_DFL)
                signal.raise_signal(signal.SIGALRM)
            # SIG_IGN consumes this occurrence without calling an integer.
        finally:
            _ACTIVE_TIMER.reset(caller_token)
            # A handler may intentionally rearm/disarm its timer (including an
            # enclosing DeadlineFallbackAgent). Preserve that updated schedule.
            now = time.monotonic()
            remaining, self.outer_interval = signal.setitimer(signal.ITIMER_REAL, 0)
            self.outer_at = now + remaining if remaining > 0 else None
            self.previous = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, self._dispatch)

    def _dispatch(self, signum, frame):
        now = time.monotonic()
        if (self.outer_at is not None and self.outer_at <= now
                and self.outer_at <= self.own_at):
            self._deliver_outer(signum, frame)
        if time.monotonic() >= self.own_at:
            raise self.expired
        self._schedule()

    def __exit__(self, _kind, _error, _traceback):
        if self.thread_guard:
            expired = _kind is None and time.monotonic() >= self.own_at
            self._exit_thread()
            if expired:
                raise self.expired
            return False
        signal.setitimer(signal.ITIMER_REAL, 0)
        _ACTIVE_TIMER.reset(self.context_token)
        signal.signal(signal.SIGALRM, self.previous)
        if self.outer_at is not None:
            signal.setitimer(signal.ITIMER_REAL, self._remaining(self.outer_at),
                             self.outer_interval)
        return False


class DeadlineFallbackAgent:
    """Deadline-enforce one already-created IntegratedSelectedAgent.

    SIGALRM acts on the main thread; existing caller alarms keep their remaining
    time and handler. Only this guard's own expiry selects a fallback.
    Worker threads use the trace guard described above; the outer runner retains
    responsibility for blocking native calls and whole-call transport deadlines.
    """
    def __init__(self, integrated, budget_seconds=1.0, reserve_seconds=0.002,
                 before_transform=None):
        if budget_seconds <= reserve_seconds or reserve_seconds < 0:
            raise ValueError("budget_seconds must exceed non-negative reserve_seconds")
        self.integrated = integrated
        self.budget_seconds = float(budget_seconds)
        self.reserve_seconds = float(reserve_seconds)
        self.before_transform = before_transform
        self.diagnostics = {}

    def act(self, observation, configuration=None):
        obs = copy.deepcopy(dict(observation)); cfg = dict(configuration or {})
        step = obs.get("step")
        if step is None:
            step = int(obs["day"])*int(cfg.get("turnsPerDay", 24)) + int(obs["hour"])
        step = int(step)
        obs["step"] = step
        last = int(cfg.get("episodeSteps", 720)) - 2
        fallback = (terminal_liquidation_fallback(obs, cfg)
                    if step == last else legal_pass(obs))
        stage = "production"
        started = time.perf_counter()
        timer = _DeadlineTimer(self.budget_seconds - self.reserve_seconds)
        try:
            with timer:
                selected = self.integrated.production.act(obs)
                fallback = copy.deepcopy(selected)
                stage = "transform"
                if self.before_transform is not None:
                    self.before_transform(obs, cfg, selected)
                output = self.integrated.transform(obs, cfg, selected, fallback_action=selected)
                self.diagnostics = {
                    "status": "completed", "fallback_stage": None,
                    "elapsed_seconds": time.perf_counter() - started,
                    "inner": copy.deepcopy(self.integrated.diagnostics),
                }
                return output
        except DeadlineExceeded as error:
            if error is not timer.expired:
                raise
            self.diagnostics = {
                "status": "deadline_fallback", "fallback_stage": stage,
                "elapsed_seconds": time.perf_counter() - started,
                "inner": copy.deepcopy(getattr(self.integrated, "diagnostics", {})),
            }
            return fallback

    __call__ = act
