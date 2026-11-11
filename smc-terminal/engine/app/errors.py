"""Expected failures of the engine, mapped to HTTP status codes by main.py."""

from __future__ import annotations


class EngineError(Exception):
    status = 500
    code = "engine_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_json(self) -> dict:
        return {"error": self.code, "message": self.message}


class InvalidSymbol(EngineError):
    status = 400
    code = "invalid_symbol"


class InvalidTimeframe(EngineError):
    status = 400
    code = "invalid_timeframe"


class NoData(EngineError):
    status = 404
    code = "no_data"


class UpstreamError(EngineError):
    status = 502
    code = "upstream_error"
