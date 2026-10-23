"""Resolve nested mounted APIs identically for HTTP, WebSockets and CORS."""


def resolve(runtime, scope):
    pinned = scope.get("looprd.generation")
    if pinned:
        return pinned
    parts = scope.get("path", "").split("/")
    if len(parts) < 4 or parts[1] not in ("looprd", "dapi"):
        return None
    # At most nine namespace segments; lookup cost is independent of component count.
    for index in range(min(len(parts) - 1, 11), 2, -1):
        generation = runtime.active.get(parts[index - 1])
        if not generation or generation.spec.api.version != parts[index]:
            continue
        mount = "/looprd/" + "/".join(parts[2:index + 1])
        if generation.prefix != mount:
            continue
        scope["looprd.generation"] = generation
        scope["looprd.parameters"] = {"component": generation.spec.component,
            "version": generation.spec.api.version, "path": "/".join(parts[index + 1:])}
        return generation
    return None


def parameters(scope):
    return scope.get("looprd.parameters", scope.get("path_params", {}))
