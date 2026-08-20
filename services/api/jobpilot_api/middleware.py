"""Request logging.

One line per request, with a stable id that also goes back to the client in a
header and into any error body. That id is the whole point: "it failed at about
half two" is not a way to find anything in a log.
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .errors import REQUEST_ID_HEADER, new_request_id, request_id_var

log = logging.getLogger("jobpilot.api.access")


def configure_logging() -> None:
    """Give the `jobpilot` loggers a level and somewhere to write.

    Uvicorn configures its own `uvicorn.*` loggers and leaves the root logger
    untouched, so without this an INFO record here falls through to
    `logging.lastResort`, which drops anything below WARNING — every request log
    line written and immediately discarded.

    Only the `jobpilot` tree is touched. A host that has already set up logging
    keeps its own configuration: if a handler is reachable, none is added.
    """
    from jobpilot_shared.settings import get_settings

    root = logging.getLogger("jobpilot")
    root.setLevel(get_settings().log_level.upper())

    reachable = root
    while reachable is not None:
        if reachable.handlers:
            return
        reachable = reachable.parent

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
    root.addHandler(handler)


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # An id supplied by a proxy is honoured so a trace survives the hop.
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handler builds the body; this line records that the
            # request is over, so a 500 is not an entry that simply stops.
            log.warning(
                "%s %s -> unhandled after %.1fms [%s]",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
                request_id,
            )
            # Deliberately *not* reset here. The catch-all handler runs outside
            # this middleware, and it needs the id to put in the error body. The
            # variable belongs to this request's task and dies with it.
            raise

        request_id_var.reset(token)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        # Query strings can carry a location filter and little else, but they are
        # left out anyway — logs outlive the reasons they were turned on.
        log.info(
            "%s %s -> %s in %.1fms [%s]",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response
