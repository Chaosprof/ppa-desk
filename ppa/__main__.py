"""python -m ppa build | serve [port]"""

from __future__ import annotations

import functools
import http.server
import sys

from .config import ROOT


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files with caching off, so a rebuild shows up on refresh."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args) -> None:
        pass


def main(argv: list[str]) -> None:
    command = argv[0] if argv else "build"
    if command == "build":
        from . import build

        build.run()
    elif command == "serve":
        port = int(argv[1]) if len(argv) > 1 else 8000
        handler = functools.partial(Handler, directory=str(ROOT / "site"))
        print(f"http://localhost:{port}")
        http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
