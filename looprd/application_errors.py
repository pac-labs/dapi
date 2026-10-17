"""Preserve intentional application rejection status without reflecting secrets."""

from starlette.responses import JSONResponse


def response(error):
    status = error.status_code if type(error.status_code) is int and 400 <= error.status_code <= 599 else 500
    message = "application request rejected" if status < 500 else "component request failed"
    headers = {"Cache-Control": "no-store"}
    retry = next((str(value) for key, value in (error.headers or {}).items() if key.lower() == "retry-after"), "")
    if status in (429, 503) and retry.isascii() and retry.isdecimal() and len(retry) <= 4 and 0 <= int(retry) <= 3600:
        headers["Retry-After"] = str(int(retry))
    return JSONResponse({"error": message}, status_code=status, headers=headers)
