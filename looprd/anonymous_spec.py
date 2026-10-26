"""Narrow, explicitly required public JSON reads inside database-auth components."""

CAPABILITY = "auth.anonymous-read-handler.v1"


def check_anonymous(spec):
    for handler in spec.handlers:
        if handler.access != "anonymous":
            continue
        if (spec.auth.mode != "database" or spec.database is None
                or CAPABILITY not in spec.requires):
            raise ValueError("anonymous reads require database auth/storage and their explicit capability")
        if (handler.method != "GET" or handler.scopes or handler.contract is not None
                or handler.execution != "inline" or handler.status != 200):
            raise ValueError("anonymous reads are inline GET/200 handlers without scopes or service contracts")
        if handler.path == "/" or handler.path.endswith("/"):
            raise ValueError("anonymous reads require an exact named path without a trailing slash")
        others = [other.path.rstrip("/") for other in spec.handlers if other.path != handler.path]
        if spec.websocket:
            others.append(spec.websocket.path.rstrip("/"))
        if any(path == handler.path or path.startswith(handler.path + "/")
               or handler.path.startswith(path + "/") for path in others):
            raise ValueError("anonymous reads cannot overlap handler/WebSocket path prefixes")
