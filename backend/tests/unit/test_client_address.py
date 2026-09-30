"""S4.12: the address a per-address cap counts is never one the client chose."""

import pytest

from secondmind.api.client_address import client_address, normalise

PEER = "172.20.0.9"


def test_behind_caddy_then_nginx_it_is_the_second_entry_from_the_right() -> None:
    # The client's own address, added by Caddy; then Caddy's, added by nginx.
    assert client_address("203.0.113.7, 10.0.0.2", PEER, 2) == "203.0.113.7"


def test_what_the_client_puts_at_the_left_of_the_header_counts_for_nothing() -> None:
    honest = client_address("203.0.113.7, 10.0.0.2", PEER, 2)
    forged = client_address("1.1.1.1, 2.2.2.2, 203.0.113.7, 10.0.0.2", PEER, 2)
    assert honest == forged == "203.0.113.7"


def test_fewer_entries_than_trusted_proxies_falls_back_to_the_connection() -> None:
    # The API reached another way: a short header can't choose the address either.
    assert client_address("198.51.100.1", PEER, 2) == PEER
    assert client_address(None, PEER, 2) == PEER
    assert client_address("", None, 1) == "unknown"


def test_one_proxy_trusts_the_last_entry() -> None:
    assert client_address("9.9.9.9, 203.0.113.7", PEER, 1) == "203.0.113.7"


def test_no_trusted_proxy_means_the_connection_itself() -> None:
    assert client_address("203.0.113.7", PEER, 0) == PEER


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),  # the same /64: one household
        ("::ffff:198.51.100.4", "198.51.100.4"),  # an IPv4 address in IPv6 clothes
        ("198.51.100.4", "198.51.100.4"),
        ("not-an-address", "not-an-address"),
    ],
)
def test_ipv6_is_counted_per_64(raw: str, expected: str) -> None:
    assert normalise(raw) == expected
