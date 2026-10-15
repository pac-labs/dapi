"""Single bounded JSON computation executed in a separate trusted process."""

import asyncio
import contextlib
import json
import sys
from types import SimpleNamespace

from .worker_limits import apply


def namespace(value):
    return SimpleNamespace(**{key: namespace(item) for key, item in value.items()}) if isinstance(value, dict) else value


def main():
    try:
        data = json.loads(sys.stdin.buffer.read(2097152))
        apply(data["memory"], data["timeout"])
        principal = namespace(data["principal"])
        principal.permits = lambda scopes: set(scopes) <= set(principal.scopes)
        ctx = SimpleNamespace(body=data["body"], principal=principal, request=namespace(data["request"]),
            specification=namespace(data["specification"]), session_factory=None, job_id=data.get("jobId"),
            secret=lambda name: data["secrets"][name])
        module = {"__name__": "looprd_process_handler"}
        with contextlib.redirect_stdout(sys.stderr):
            exec(compile(data["source"], "<looprd-process>", "exec"), module)
            result = asyncio.run(module["handle"](ctx))
        output = json.dumps(result, allow_nan=False)
        if len(output.encode()) > 1048576:
            raise ValueError("worker output exceeds limit")
        sys.stdout.write(output)
    except BaseException:
        # Never serialize exception strings, input data or source to the caller/logs.
        sys.exit(1)


if __name__ == "__main__":
    main()
