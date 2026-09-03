"""Local dev entrypoint for Windows only - runs the ASGI app under a Selector-based event
loop instead of uvicorn's own default.

`uvicorn app.main:app` (the command README.md documents as the normal way to run this
app) picks Windows' ProactorEventLoop - uvicorn's own hardcoded choice there
(`uvicorn.loops.asyncio.asyncio_loop_factory`), passed directly as `asyncio.run()`'s
`loop_factory`, which bypasses any process-wide `asyncio.set_event_loop_policy()` (see
app/main.py's own docstring on that policy - it still matters for anything else that
imports app.main and drives its own event loop, e.g. the test suite via TestClient/
anyio, just not uvicorn's own `Server.run()`). Psycopg's async mode refuses to run under
ProactorEventLoop at all (see app/db/connection.py), so `uvicorn app.main:app` alone
cannot start this app on Windows.

Not needed on Linux/macOS (where this app is actually deployed, per PR 192's
docker-compose) - uvicorn's own loop factory already picks a Selector-based loop there
unconditionally, so plain `uvicorn app.main:app --reload` keeps working as documented.

Usage: `uv run python run_windows.py` (equivalent to `uvicorn app.main:app`, minus
`--reload` - restart it by hand after an edit, same as running any other plain Python
script during development)."""

import asyncio
import selectors

from uvicorn import Config, Server


async def _serve() -> None:
    config = Config("app.main:app", host="127.0.0.1", port=8000)
    server = Server(config)
    await server.serve()


if __name__ == "__main__":
    loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_serve())
