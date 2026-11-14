"""Documents deeper than the caller's thread can go.

Reading, writing, copying and walking a document recurse, level by level:
in Python (at most ``sys.getrecursionlimit()`` calls, 1,000 by default: 60
nested tables, to read one the slow way) and in C (``json``'s, bounded by
that limit on 3.10 and 3.11, by a fixed count on 3.12 and 3.13, by the
thread's stack on 3.14). What goes deeper than the caller's thread allows
goes on in a thread of its own, with a large stack and a high recursion
limit (``run``): what is pure (reading, writing, copying, ``repr``) is done
again there after a ``RecursionError``; a walk, which can't be (its
functions have done what they did), moves there once it is deep
(``_Walker``).

Only a document nested deeper than the usual ones pays for it (a thread,
about 0.1 ms), and Python's recursion limit is raised meanwhile, for every
thread: it is one for all of them. Deeper than that thread can go is an
error, ``ASTDepthError`` (``ASTDecodeError`` when reading), not a crash.

On 3.12 and 3.13, ``json`` counts its levels and stops at 10,000, whatever
the stack: in a thread with a small one (macOS's 512 kB, Windows's 1 MB, a
thread pool's), it would crash first. There, outside the main thread, text
that may be deep is read in panir's thread from the start, a document that
may be deep written there, and a node's ``repr`` made there (``risky``).
"""

from __future__ import annotations

import contextvars
import sys
import threading
from collections.abc import Callable
from typing import Any, TypeVar

__all__ = ["LIMIT", "in_deep_thread", "risky", "run"]

T = TypeVar("T")

# Python calls in panir's thread, and its stack (address space: a page is
# used once touched). 3.10's calls take the most stack, about 1 kB each.
LIMIT = 50_000
STACK = 256 << 20

_lock = threading.Lock()
_users = 0  # threads of panir's running, which keep the limit raised
_saved = 0  # the limit before
_local = threading.local()

# json's own count of levels on 3.12 and 3.13, which a small stack doesn't
# hold (see above): text with fewer brackets than this is read in place
_RISKY = (3, 12) <= sys.version_info[:2] < (3, 14)
SAFE_LEVELS = 2_000


def in_deep_thread() -> bool:
    return getattr(_local, "deep", False)


def risky() -> bool:
    """Whether recursion in C (json's, repr's) may crash this thread before
    it raises: on 3.12 and 3.13, outside the main thread and panir's."""
    return (
        _RISKY
        and threading.current_thread() is not threading.main_thread()
        and not in_deep_thread()
    )


class TooDeep(Exception):
    """Raised by ``run`` when even its thread went too deep."""


def run(fn: Callable[..., T], *args: Any) -> T:
    """``fn(*args)`` in a thread with a large stack and a high recursion
    limit, in a copy of the caller's context; its result, or its exception.
    ``TooDeep`` if it went too deep there too."""
    global _users, _saved
    out: list = []
    context = contextvars.copy_context()

    def target() -> None:
        _local.deep = True
        try:
            out.append((True, context.run(fn, *args)))
        except RecursionError as e:
            if type(e) is RecursionError:
                out.append((False, TooDeep()))
            else:
                out.append((False, e))  # (one of ours, from deeper)
        except BaseException as e:  # (re-raised in the caller's thread)
            out.append((False, e))

    with _lock:
        old = threading.stack_size()
        try:
            threading.stack_size(STACK)
        except (ValueError, RuntimeError):  # (a platform that won't: no deeper)
            raise TooDeep from None
        try:
            if _users == 0:
                _saved = sys.getrecursionlimit()
                sys.setrecursionlimit(max(_saved, LIMIT))
            _users += 1
            try:
                thread = threading.Thread(target=target, name="panir-deep", daemon=True)
                thread.start()
            except BaseException as e:
                _release()
                if isinstance(e, RuntimeError):  # (no threads: Pyodide's)
                    raise TooDeep from None
                raise
        finally:
            threading.stack_size(old)
    try:
        thread.join()
    finally:
        with _lock:
            _release()
    ok, value = out[0]
    if ok:
        return value
    raise value


def _release() -> None:
    global _users
    _users -= 1
    if _users == 0 and sys.getrecursionlimit() == max(_saved, LIMIT):
        sys.setrecursionlimit(_saved)  # (unless someone set it meanwhile)


def deep(fn: Callable[..., T], too_deep: Callable[[], BaseException], *args: Any) -> T:
    """``fn(*args)``, again in panir's thread if it went too deep here.
    For what is pure: what it did before is done again. ``too_deep()`` is
    the error when it went too deep there too."""
    try:
        return fn(*args)
    except RecursionError as e:
        if type(e) is not RecursionError or in_deep_thread():
            raise
    return retry(fn, too_deep, *args)


def retry(fn: Callable[..., T], too_deep: Callable[[], BaseException], *args: Any) -> T:
    try:
        return run(fn, *args)
    except TooDeep:
        raise too_deep() from None
