import asyncio
import logging

from fastapi import Request

from app.main import unexpected_exception_handler


def test_unexpected_exception_handler_returns_safe_json_and_logs_details(caplog):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "scheme": "http",
        "http_version": "1.1",
    }

    with caplog.at_level(logging.ERROR):
        response = asyncio.run(
            unexpected_exception_handler(Request(scope), RuntimeError("internal secret detail"))
        )

    assert response.status_code == 500
    assert response.body == b'{"detail":"An unexpected server error occurred. Please try again."}'
    assert "internal secret detail" in caplog.text
    assert "internal secret detail" not in response.body.decode()
