import socket

import pytest

from url_security import (
    PinnedNetworkBackend,
    UnsafeURLError,
    canonicalize_url,
    resolve_hostname,
    validate_public_url,
)


PUBLIC_IP = "93.184.216.34"


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        (
            "HTTPS://FORUM.BLU-RAY.COM:443/showthread.php?t=123#post",
            "https://forum.blu-ray.com/showthread.php?t=123",
        ),
        (
            "https://docs.google.com/spreadsheets/d/id/edit#gid=7",
            "https://docs.google.com/spreadsheets/d/id/edit#gid=7",
        ),
        (
            "https://www.reddit.com/r/test/comments/abc/title/",
            "https://www.reddit.com/r/test/comments/abc/title/",
        ),
        (
            "https://bücher.example/catalog",
            "https://xn--bcher-kva.example/catalog",
        ),
        (
            "https://example.com/a%20space",
            "https://example.com/a%20space",
        ),
    ],
)
def test_canonicalize_url_preserves_safe_representative_sources(raw_url, expected):
    assert canonicalize_url(raw_url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/list",
        "file:///etc/passwd",
        "https://user:secret@example.com/list",
        "https://example.com/list\\next",
        "https://example.com/list%5Cnext",
        "https://example.com/list\nhttps://attacker.test",
        "https://example.com/list%0D%0AInjected:true",
        "https://example.com/list%09tab",
        "https://example.com/raw space",
        "https://example.com/list%zz",
        "https://example.com/trailing%",
        "https://localhost/admin",
        "https://api.localhost/admin",
        "https://-bad.example/list",
        "https://bad-.example/list",
        "https://example..com/list",
        "https://example.com../list",
        "https://example.com:0/list",
        "https://example.com:99999/list",
        "https:///missing-host",
        "https://./missing-host",
        "https://\ud800.example/list",
        "https://example.com/" + ("x" * 2048),
    ],
)
def test_canonicalize_url_rejects_unsafe_syntax(url):
    with pytest.raises(UnsafeURLError):
        canonicalize_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.1.2.3/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/admin",
        "http://192.0.2.1/documentation",
        "http://224.0.0.1/multicast",
        "http://[ff02::1]/multicast",
    ],
)
def test_validate_public_url_rejects_non_global_ip_literals(url):
    with pytest.raises(UnsafeURLError, match="not globally routable"):
        validate_public_url(url, resolver=lambda _hostname: [PUBLIC_IP])


def test_validate_public_url_rejects_when_any_dns_answer_is_non_global():
    with pytest.raises(UnsafeURLError, match="not globally routable"):
        validate_public_url(
            "https://rebinding.example/list",
            resolver=lambda _hostname: [PUBLIC_IP, "127.0.0.1"],
        )


@pytest.mark.parametrize(
    "embedded_address",
    [
        "64:ff9b::7f00:1",
        "64:ff9b::a9fe:a9fe",
        "::ffff:127.0.0.1",
        "2002:7f00:1::",
        "2001:0000:4136:e378:8000:63bf:3fff:fdd2",
    ],
)
def test_validate_public_url_rejects_nonpublic_embedded_ipv4(embedded_address):
    with pytest.raises(UnsafeURLError, match="embedded IPv4"):
        validate_public_url(f"https://[{embedded_address}]/list")

    with pytest.raises(UnsafeURLError, match="embedded IPv4"):
        validate_public_url(
            "https://translated.example/list",
            resolver=lambda _hostname: [embedded_address],
        )


def test_validate_public_url_accepts_public_nat64_embedded_ipv4():
    address = "64:ff9b::5db8:d822"

    validated = validate_public_url(f"https://[{address}]/list")

    assert validated.resolved_ips == (address,)


def test_validate_public_url_returns_canonical_url_and_all_public_answers():
    validated = validate_public_url(
        "HTTPS://EXAMPLE.COM:443/list#fragment",
        resolver=lambda hostname: [PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946"],
    )

    assert validated.url == "https://example.com/list"
    assert validated.hostname == "example.com"
    assert validated.port == 443
    assert validated.resolved_ips == (
        PUBLIC_IP,
        "2606:2800:220:1:248:1893:25c8:1946",
    )


def test_validate_public_url_rejects_empty_or_invalid_dns_answers():
    with pytest.raises(UnsafeURLError, match="did not resolve"):
        validate_public_url("https://empty.example", resolver=lambda _hostname: [])
    with pytest.raises(UnsafeURLError, match="invalid IP address"):
        validate_public_url(
            "https://broken.example", resolver=lambda _hostname: ["not-an-ip"]
        )


def test_resolve_hostname_uses_getaddrinfo_and_deduplicates(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0)),
        ],
    )
    assert resolve_hostname("example.com") == (PUBLIC_IP,)


def test_resolve_hostname_wraps_dns_failures(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(UnsafeURLError, match="could not resolve"):
        resolve_hostname("missing.example")


def test_pinned_network_backend_connects_only_to_prevalidated_ip():
    calls = []

    class FakeBackend:
        def connect_tcp(self, **kwargs):
            calls.append(kwargs)
            return object()

        def connect_unix_socket(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "unix-stream"

        def sleep(self, seconds):
            calls.append(seconds)

    backend = PinnedNetworkBackend(FakeBackend())
    backend.pin("forum.blu-ray.com", (PUBLIC_IP,))

    stream = backend.connect_tcp(
        host="forum.blu-ray.com",
        port=443,
        timeout=5.0,
        local_address=None,
        socket_options=None,
    )

    assert stream is not None
    assert calls[0]["host"] == PUBLIC_IP
    assert backend.connect_unix_socket("socket-path") == "unix-stream"
    backend.sleep(0.25)
    assert calls[1:] == [(("socket-path",), {}), 0.25]
    with pytest.raises(RuntimeError, match="not pinned"):
        backend.connect_tcp(
            host="unvalidated.example",
            port=443,
            timeout=5.0,
            local_address=None,
            socket_options=None,
        )


def test_pinned_network_backend_fails_over_validated_ips_without_dns():
    calls = []
    first_ip = "93.184.216.34"
    second_ip = "1.1.1.1"

    class FakeBackend:
        def connect_tcp(self, **kwargs):
            calls.append(kwargs["host"])
            if kwargs["host"] == first_ip:
                raise OSError("first address unavailable")
            return "connected"

    backend = PinnedNetworkBackend(FakeBackend())
    backend.pin("forum.blu-ray.com", (first_ip, second_ip))

    assert (
        backend.connect_tcp(
            host="forum.blu-ray.com",
            port=443,
            timeout=5.0,
            local_address=None,
            socket_options=None,
        )
        == "connected"
    )
    assert calls == [first_ip, second_ip]


def test_pinned_network_backend_raises_last_ip_failure():
    errors = [OSError("first"), OSError("second")]

    class FailingBackend:
        def connect_tcp(self, **kwargs):
            raise errors.pop(0)

    backend = PinnedNetworkBackend(FailingBackend())
    backend.pin("forum.blu-ray.com", ("93.184.216.34", "1.1.1.1"))

    with pytest.raises(OSError, match="second"):
        backend.connect_tcp(
            host="forum.blu-ray.com",
            port=443,
            timeout=5.0,
            local_address=None,
            socket_options=None,
        )
