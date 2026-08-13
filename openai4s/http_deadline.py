"""Absolute wall-clock deadlines for pure-stdlib HTTP exchanges.

``urllib`` passes its ``timeout`` to a socket as an *idle* timeout.  That
bounds a silent peer, but a peer that drips one response-header byte before
each idle timeout can keep ``opener.open()`` inside ``HTTPResponse.begin()``
forever.  This module adds the missing wall-clock boundary without moving the
request or its credentials to a worker thread.

The watchdog knows only the active socket.  It never retains a Request,
headers, body, URL, or credential.  On expiry it shuts down and closes that
socket, unblocking connect, proxy CONNECT, TLS handshake, status/header reads,
or body reads.  DNS resolution is the one deliberate stdlib limitation:
``socket.getaddrinfo`` exposes neither a cancellation handle nor a portable
timeout.  The watchdog records expiry during DNS and refuses to connect once
resolution returns, but cannot make a stuck system resolver return sooner.
"""

from __future__ import annotations

import functools
import http.client
import socket
import threading
import time
import urllib.request
from collections.abc import Iterable
from typing import Any, Callable


class HTTPExchangeTimeout(TimeoutError):
    """One HTTP exchange exceeded its absolute wall-clock deadline."""


def socket_timeout_setter(response: Any) -> Callable[[float], Any] | None:
    """Find a live response socket's timeout setter through urllib wrappers."""

    pending = [response]
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        setter = getattr(candidate, "settimeout", None)
        if callable(setter):
            return setter
        for attribute in ("fp", "raw", "_sock"):
            child = getattr(candidate, attribute, None)
            if child is not None:
                pending.append(child)
    return None


def _response_socket(response: Any) -> socket.socket | None:
    """Return the actual socket retained by an urllib response, if visible."""

    pending = [response]
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(candidate, socket.socket):
            return candidate
        for attribute in ("fp", "raw", "_sock"):
            child = getattr(candidate, attribute, None)
            if child is not None:
                pending.append(child)
    return None


def _close_socket(sock: Any) -> None:
    """Best-effort abort that wakes a concurrent blocking socket operation."""

    try:
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:  # noqa: BLE001 - already closed/not connected is expected
        pass
    try:
        sock.close()
    except Exception:  # noqa: BLE001 - expiry cleanup must remain best effort
        pass


class HTTPExchangeDeadline:
    """A cancellable absolute deadline shared by one urllib exchange.

    Use as a context manager and keep the context open until the response body
    has been consumed.  ``build_opener`` preserves urllib's normal proxy
    discovery while replacing only its HTTP(S) connection classes.  Additional
    handlers, such as a redirect-refusal policy, retain their normal semantics.
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = float(timeout)
        self.deadline = time.monotonic() + self.timeout
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._timer: threading.Timer | None = None
        self._started = False
        self._cancelled = False
        self._expired = False

    @property
    def expired(self) -> bool:
        with self._lock:
            return self._expired

    def __enter__(self) -> HTTPExchangeDeadline:
        with self._lock:
            if self._started:
                raise RuntimeError("HTTP exchange deadline cannot be reused")
            self._started = True
            delay = max(0.0, self.deadline - time.monotonic())
            timer = threading.Timer(delay, self._expire)
            timer.name = "openai4s-http-exchange-deadline"
            timer.daemon = True
            self._timer = timer
            timer.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        del exc, traceback
        with self._lock:
            expired = self._expired
        self.cancel()
        # Only the watchdog decides an exchange was aborted.  Promoting a merely
        # late clock here converted a fully-read reply into a timeout, and
        # raising over an in-flight exception replaced the real failure (an
        # ``HTTPError`` carrying an expired MCP session, say) with a timeout the
        # caller cannot recover from.  A block that finished, finished.
        if expired and exc_type is None:
            raise HTTPExchangeTimeout(
                "HTTP exchange exceeded its absolute deadline"
            ) from None
        return False

    def _expire(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._expired = True
            sock = self._socket
            self._socket = None
        if sock is not None:
            _close_socket(sock)

    def cancel(self) -> None:
        """Disarm and join the watchdog, releasing every retained reference."""

        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            self._socket = None
            timer = self._timer
        if timer is not None:
            timer.cancel()
            if timer is not threading.current_thread():
                timer.join()

    def remaining(self) -> float:
        """Return the remaining budget or raise the shared timeout signal."""

        remaining = self.deadline - time.monotonic()
        with self._lock:
            expired = self._expired or remaining <= 0
            if expired:
                self._expired = True
        if expired:
            self._expire()
            raise HTTPExchangeTimeout(
                "HTTP exchange exceeded its absolute deadline"
            ) from None
        return remaining

    def _register_socket(self, sock: socket.socket) -> None:
        with self._lock:
            expired = self._expired or self._cancelled
            if not expired:
                self._socket = sock
        if expired:
            _close_socket(sock)
            raise HTTPExchangeTimeout(
                "HTTP exchange exceeded its absolute deadline"
            ) from None

    def _unregister_socket(self, sock: socket.socket) -> None:
        with self._lock:
            if self._socket is sock:
                self._socket = None

    def create_connection(
        self,
        address: tuple[str, int],
        timeout: Any = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        """``socket.create_connection`` with the socket registered pre-connect.

        ``timeout`` is accepted for the ``HTTPConnection`` callback contract;
        every concrete socket instead receives the smaller, current absolute
        budget.  DNS remains synchronous for the reason in the module docstring.
        """

        del timeout
        host, port = address
        last_error: OSError | None = None
        addresses: Iterable[tuple[Any, ...]] = socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM
        )
        for family, socktype, proto, _canonname, sockaddr in addresses:
            sock: socket.socket | None = None
            try:
                remaining = self.remaining()
                sock = socket.socket(family, socktype, proto)
                self._register_socket(sock)
                sock.settimeout(remaining)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sockaddr)
                sock.settimeout(self.remaining())
                return sock
            except HTTPExchangeTimeout:
                if sock is not None:
                    self._unregister_socket(sock)
                    _close_socket(sock)
                raise
            except OSError as error:
                last_error = error
                if sock is not None:
                    self._unregister_socket(sock)
                    _close_socket(sock)
                # A watchdog-closed connect commonly reports EBADF or an
                # implementation-specific OSError.  Never try another address
                # after the absolute boundary has already won.
                self.remaining()
        if last_error is not None:
            raise last_error
        raise OSError("getaddrinfo returned no usable address")

    def wrap_tls(
        self,
        raw_socket: socket.socket,
        context: Any,
        *,
        server_hostname: str,
    ) -> socket.socket:
        """Wrap then register before performing the blocking TLS handshake."""

        wrapped: socket.socket | None = None
        try:
            # ``do_handshake_on_connect=False`` makes wrapping local. Holding
            # the lock closes the tiny fd-ownership handoff where the raw
            # socket is detached but the SSLSocket is not registered yet.
            with self._lock:
                if self._expired or self._cancelled:
                    raise HTTPExchangeTimeout(
                        "HTTP exchange exceeded its absolute deadline"
                    )
                raw_socket.settimeout(max(0.001, self.deadline - time.monotonic()))
                wrapped = context.wrap_socket(
                    raw_socket,
                    server_hostname=server_hostname,
                    do_handshake_on_connect=False,
                )
                self._socket = wrapped
            wrapped.settimeout(self.remaining())
            wrapped.do_handshake()
            wrapped.settimeout(self.remaining())
            return wrapped
        except Exception:
            if wrapped is not None:
                self._unregister_socket(wrapped)
                _close_socket(wrapped)
            else:
                self._unregister_socket(raw_socket)
                _close_socket(raw_socket)
            raise

    def register_response(self, response: Any) -> None:
        """Retarget the watchdog to urllib's body socket after headers arrive."""

        sock = _response_socket(response)
        if sock is not None:
            self._register_socket(sock)
            sock.settimeout(self.remaining())

    def http_handler(
        self,
        connection_class: type[http.client.HTTPConnection] | None = None,
    ) -> urllib.request.HTTPHandler:
        return _DeadlineHTTPHandler(
            self,
            connection_class=connection_class or _DeadlineHTTPConnection,
        )

    def https_handler(
        self,
        connection_class: type[http.client.HTTPSConnection] | None = None,
    ) -> urllib.request.HTTPSHandler:
        return _DeadlineHTTPSHandler(
            self,
            connection_class=connection_class or _DeadlineHTTPSConnection,
        )

    def build_opener(self, *handlers: Any) -> urllib.request.OpenerDirector:
        """Build the normal urllib chain with deadline-aware HTTP(S) handlers."""

        return urllib.request.build_opener(
            *handlers,
            self.http_handler(),
            self.https_handler(),
        )

    def open(self, opener: Any, request: urllib.request.Request) -> Any:
        """Open through urllib while the same budget covers response headers."""

        response = opener.open(request, timeout=self.remaining())  # noqa: S310
        self.register_response(response)
        self.remaining()
        return response


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, deadline: HTTPExchangeDeadline, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._absolute_deadline = deadline
        self._create_connection = deadline.create_connection

    def connect(self) -> None:
        super().connect()
        if self.sock is not None:
            self._absolute_deadline._register_socket(self.sock)
            self.sock.settimeout(self._absolute_deadline.remaining())


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, deadline: HTTPExchangeDeadline, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._absolute_deadline = deadline
        self._create_connection = deadline.create_connection

    def connect(self) -> None:
        # Calling the HTTP base performs TCP connect and an optional proxy
        # CONNECT tunnel through our registered socket. TLS is deliberately
        # split into wrap + handshake so the SSLSocket is registered before
        # the blocking handshake begins.
        http.client.HTTPConnection.connect(self)
        if self.sock is None:  # pragma: no cover - defensive stdlib contract
            raise OSError("HTTPS connection did not create a socket")
        server_hostname = self._tunnel_host or self.host
        self.sock = self._absolute_deadline.wrap_tls(
            self.sock,
            self._context,
            server_hostname=server_hostname,
        )


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(
        self,
        deadline: HTTPExchangeDeadline,
        *,
        connection_class: type[http.client.HTTPConnection],
    ) -> None:
        super().__init__()
        self._deadline = deadline
        self._connection_class = connection_class

    def http_open(self, request: urllib.request.Request) -> Any:
        connection = functools.partial(
            self._connection_class,
            deadline=self._deadline,
        )
        return self.do_open(connection, request)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(
        self,
        deadline: HTTPExchangeDeadline,
        *,
        connection_class: type[http.client.HTTPSConnection],
    ) -> None:
        super().__init__()
        self._deadline = deadline
        self._connection_class = connection_class

    def https_open(self, request: urllib.request.Request) -> Any:
        connection = functools.partial(
            self._connection_class,
            deadline=self._deadline,
        )
        connection_args = {"context": self._context}
        # Python 3.10/3.11 carry this compatibility field; newer stdlib
        # versions removed both it and the HTTPSConnection parameter. Preserve
        # the stock handler contract exactly on versions where it exists.
        if hasattr(self, "_check_hostname"):
            connection_args["check_hostname"] = self._check_hostname
        return self.do_open(connection, request, **connection_args)


__all__ = [
    "HTTPExchangeDeadline",
    "HTTPExchangeTimeout",
    "socket_timeout_setter",
]
