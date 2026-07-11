"""URL validation and DNS pinning for untrusted discovery results."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import ipaddress
import re
import socket
import threading
import urllib.parse


MAX_URL_LENGTH = 2048
Resolver = Callable[[str], Sequence[str]]
_NAT64_WELL_KNOWN_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")
# RFC 7050 discovery target and the two "any" IPv4 answers it synthesizes to.
_NAT64_DISCOVERY_HOSTNAME = "ipv4only.arpa"
_NAT64_DISCOVERY_IPV4_LITERALS = (
    ipaddress.IPv4Address("192.0.0.170"),
    ipaddress.IPv4Address("192.0.0.171"),
)
# The only Prefix Lengths RFC 6052 Section 2.2 defines for embedding IPv4.
_RFC6052_PREFIX_LENGTHS = (32, 40, 48, 56, 64, 96)


class UnsafeURLError(ValueError):
    """Raised when a URL is unsafe to fetch or persist."""


@dataclass(frozen=True)
class ValidatedURL:
    url: str
    hostname: str
    port: int
    resolved_ips: tuple[str, ...]


def canonicalize_url(raw_url: str) -> str:
    """Return a stable URL or reject syntax that is unsafe in registries/HTTP."""
    if not raw_url or len(raw_url) > MAX_URL_LENGTH:
        raise UnsafeURLError("URL is empty or too long")
    ensure_safe_url_text(raw_url)

    try:
        parsed = urllib.parse.urlsplit(raw_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError(f"URL is malformed: {exc}") from exc

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise UnsafeURLError("URL scheme must be HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("URL userinfo is not allowed")
    if not hostname:
        raise UnsafeURLError("URL hostname is missing")

    if hostname.endswith(".."):
        raise UnsafeURLError("URL hostname contains an empty DNS label")
    try:
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise UnsafeURLError("URL hostname is invalid") from exc
    if not hostname:
        raise UnsafeURLError("URL hostname is missing")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeURLError("localhost URLs are not allowed")
    _validate_hostname_syntax(hostname)
    if port == 0:
        raise UnsafeURLError("URL port 0 is not allowed")

    default_port = 443 if scheme == "https" else 80
    effective_port = default_port if port is None else port
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = (
        display_host
        if effective_port == default_port
        else f"{display_host}:{effective_port}"
    )
    fragment = ""
    if (
        hostname == "docs.google.com" or hostname.endswith(".docs.google.com")
    ) and re.fullmatch(r"gid=\d+", parsed.fragment):
        fragment = parsed.fragment
    return urllib.parse.urlunsplit(
        (scheme, netloc, parsed.path, parsed.query, fragment)
    )


def ensure_safe_url_text(value: str) -> None:
    """Reject characters URL parsers may otherwise strip or reinterpret."""
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise UnsafeURLError("URL contains a malformed percent escape")
    if any(_is_forbidden_character(character) for character in value):
        raise UnsafeURLError("URL contains raw whitespace or control characters")
    decoded_value = urllib.parse.unquote(value)
    if any(_is_control_character(character) for character in decoded_value):
        raise UnsafeURLError("URL contains decoded control characters")
    if "\\" in decoded_value:
        raise UnsafeURLError("URL contains a backslash")


def resolve_hostname(hostname: str) -> tuple[str, ...]:
    """Resolve a hostname once, returning stable de-duplicated IP strings."""
    try:
        answers = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURLError(f"could not resolve hostname {hostname}") from exc
    return tuple(dict.fromkeys(answer[4][0] for answer in answers))


def validate_public_url(
    raw_url: str, *, resolver: Resolver = resolve_hostname
) -> ValidatedURL:
    """Validate syntax and require every resolved address to be globally routable."""
    url = canonicalize_url(raw_url)
    parsed = urllib.parse.urlsplit(url)
    assert parsed.hostname is not None
    hostname = parsed.hostname
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        raw_answers = resolver(hostname)
    else:
        raw_answers = (str(literal_ip),)

    if not raw_answers:
        raise UnsafeURLError(f"hostname {hostname} did not resolve")
    resolved_ips: list[str] = []
    nat64_prefixes: tuple[ipaddress.IPv6Network, ...] | None = None
    for answer in raw_answers:
        try:
            address = ipaddress.ip_address(answer)
        except ValueError as exc:
            raise UnsafeURLError(
                f"resolver returned invalid IP address {answer}"
            ) from exc
        if isinstance(address, ipaddress.IPv6Address) and nat64_prefixes is None:
            nat64_prefixes = (
                _NAT64_WELL_KNOWN_PREFIX,
                *_discover_nat64_prefixes(resolver),
            )
        unsafe_embedded = next(
            (
                embedded
                for embedded in _embedded_ipv4_addresses(
                    address, nat64_prefixes or (_NAT64_WELL_KNOWN_PREFIX,)
                )
                if not embedded.is_global or embedded.is_multicast
            ),
            None,
        )
        if unsafe_embedded is not None:
            raise UnsafeURLError(
                f"IP address {address} contains nonpublic embedded IPv4 "
                f"{unsafe_embedded}"
            )
        if not address.is_global or address.is_multicast:
            raise UnsafeURLError(f"IP address {address} is not globally routable")
        normalized = str(address)
        if normalized not in resolved_ips:
            resolved_ips.append(normalized)

    default_port = 443 if parsed.scheme == "https" else 80
    return ValidatedURL(
        url=url,
        hostname=hostname,
        port=parsed.port or default_port,
        resolved_ips=tuple(resolved_ips),
    )


class PinnedNetworkBackend:
    """httpcore backend that connects hostnames only to prevalidated IPs."""

    def __init__(self, backend: object) -> None:
        self._backend = backend
        self._pinned_ips: dict[str, tuple[str, ...]] = {}
        self._lock = threading.Lock()

    def pin(self, hostname: str, ip_addresses: Sequence[str]) -> None:
        with self._lock:
            self._pinned_ips[hostname.casefold().rstrip(".")] = tuple(ip_addresses)

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> object:
        with self._lock:
            pinned_ips = self._pinned_ips.get(host.casefold().rstrip("."))
        if not pinned_ips:
            raise RuntimeError(f"hostname {host} was not pinned")
        last_error: Exception | None = None
        for pinned_ip in pinned_ips:
            try:
                return self._backend.connect_tcp(
                    host=pinned_ip,
                    port=port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def connect_unix_socket(self, *args: object, **kwargs: object) -> object:
        return self._backend.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


def _is_forbidden_character(character: str) -> bool:
    return character.isspace() or _is_control_character(character)


def _is_control_character(character: str) -> bool:
    ordinal = ord(character)
    return ordinal < 32 or 127 <= ordinal <= 159


def _embedded_ipv4_addresses(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    nat64_prefixes: Sequence[ipaddress.IPv6Network] = (_NAT64_WELL_KNOWN_PREFIX,),
) -> tuple[ipaddress.IPv4Address, ...]:
    if not isinstance(address, ipaddress.IPv6Address):
        return ()
    embedded: list[ipaddress.IPv4Address] = []
    for prefix in nat64_prefixes:
        if address in prefix:
            embedded.append(_extract_rfc6052_ipv4(address, prefix.prefixlen))
    for candidate in (address.ipv4_mapped, address.sixtofour):
        if candidate is not None:
            embedded.append(candidate)
    if address.teredo is not None:
        embedded.extend(address.teredo)
    return tuple(dict.fromkeys(embedded))


def _extract_rfc6052_ipv4(
    address: ipaddress.IPv6Address, prefix_length: int
) -> ipaddress.IPv4Address:
    """Pull the embedded IPv4 out of an address per RFC 6052 Section 2.2.

    Prefix lengths below 96 place a reserved all-zero octet at bits 64-71,
    splitting the 32-bit IPv4 payload around it; dropping that octet first
    lets every non-96 prefix length share one slicing rule.
    """
    address_bytes = address.packed
    if prefix_length == 96:
        embedded_bytes = address_bytes[12:16]
    else:
        without_reserved_octet = address_bytes[:8] + address_bytes[9:]
        start = prefix_length // 8
        embedded_bytes = without_reserved_octet[start : start + 4]
    return ipaddress.IPv4Address(embedded_bytes)


def _discover_nat64_prefixes(resolver: Resolver) -> tuple[ipaddress.IPv6Network, ...]:
    """Discover a network-specific NAT64 prefix per RFC 7050, if any is active.

    A DNS64 resolver can synthesize AAAA records from a locally-configured
    Pref64::/n instead of the well-known 64:ff9b::/96. Querying the reserved
    ipv4only.arpa name through the same resolver used for the real hostname
    and checking which Prefix Length recovers one of RFC 7050's two
    well-known IPv4 answers (192.0.0.170/171) reveals that prefix.
    """
    try:
        answers = resolver(_NAT64_DISCOVERY_HOSTNAME)
    except Exception:
        return ()
    prefixes: list[ipaddress.IPv6Network] = []
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer)
        except ValueError:
            continue
        if not isinstance(address, ipaddress.IPv6Address):
            continue
        for prefix_length in _RFC6052_PREFIX_LENGTHS:
            if _extract_rfc6052_ipv4(address, prefix_length) in (
                _NAT64_DISCOVERY_IPV4_LITERALS
            ):
                network = ipaddress.IPv6Network(
                    f"{address}/{prefix_length}", strict=False
                )
                if network not in prefixes:
                    prefixes.append(network)
                break
    return tuple(prefixes)


def _validate_hostname_syntax(hostname: str) -> None:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if len(hostname) > 253 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or re.fullmatch(r"[a-z0-9-]+", label) is None
            for label in labels
        ):
            raise UnsafeURLError("URL hostname contains an invalid DNS label") from None
