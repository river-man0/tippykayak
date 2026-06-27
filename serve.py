#!/usr/bin/env python3
"""A tiny static server that supports HTTP Range requests (byte serving).

PMTiles is read with HTTP range requests, but Python's stock ``http.server``
ignores ``Range`` and returns the whole file — which breaks PMTiles for any
archive larger than its first read. Use this instead:

    python serve.py            # serves the repo root on http://localhost:8000
    python serve.py 8077 .     # custom port / directory

Then open http://localhost:8000/viewer/index.html
"""

from __future__ import annotations

import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class RangeRequestHandler(SimpleHTTPRequestHandler):
    def send_head(self):  # noqa: C901 - mirrors the stdlib method's shape
        header = self.headers.get("Range")
        if not header:
            return super().send_head()

        match = _RANGE.fullmatch(header.strip())
        path = self.translate_path(self.path)
        if not match or not os.path.isfile(path):
            return super().send_head()

        size = os.path.getsize(path)
        start_s, end_s = match.group(1), match.group(2)
        if start_s == "":
            # Suffix range: last N bytes.
            length = min(int(end_s), size)
            start, end = size - length, size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)

        if start > end or start >= size:
            self.send_error(416, "Requested Range Not Satisfiable")
            return None

        length = end - start + 1
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        # Hand back a bounded reader so copyfile sends exactly `length` bytes.
        return _Bounded(f, length)


class _Bounded:
    def __init__(self, f, remaining):
        self.f = f
        self.remaining = remaining

    def read(self, n=-1):
        if self.remaining <= 0:
            return b""
        if n < 0 or n > self.remaining:
            n = self.remaining
        data = self.f.read(n)
        self.remaining -= len(data)
        return data

    def close(self):
        self.f.close()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    directory = sys.argv[2] if len(sys.argv) > 2 else "."
    handler = partial(RangeRequestHandler, directory=directory)
    with ThreadingHTTPServer(("", port), handler) as httpd:
        print(f"Serving {os.path.abspath(directory)} with Range support on http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
