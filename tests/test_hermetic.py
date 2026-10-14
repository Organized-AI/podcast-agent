import os
import socket

import pytest


def test_outbound_network_is_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="must not open network connections"):
            sock.connect(("93.184.216.34", 443))
    finally:
        sock.close()


def test_credentials_are_scrubbed():
    assert "ANTHROPIC_API_KEY" not in os.environ and os.environ["OTEL_SDK_DISABLED"] == "true"
