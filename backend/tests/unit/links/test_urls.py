"""S4.6: the rules about what a link may point at. Each rule has a test; the unusual spellings of an
address are normalised before the check, so the check sees what a connection would reach."""

import ipaddress

import pytest

from secondmind.links.urls import (
    FetchRefusedError,
    canonical_url,
    check_resolved,
    extract_urls,
    host_for_log,
    parse_ipv4_spelling,
    parse_url,
    refusal_for,
    site_name,
    video_site,
)


def refused(url: str) -> str:
    with pytest.raises(FetchRefusedError) as caught:
        parse_url(url)
    return caught.value.rule


# ------------------------------------------------------------------ the plain rules


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a",
        "http://example.com/a?b=1#c",
        "https://example.com:443/",
        "http://example.com:80/",
        "https://sub.example.co.uk/path/",
        "https://bücher.example/straße",
    ],
)
def test_ordinary_web_links_are_accepted(url: str) -> None:
    parsed = parse_url(url)
    assert parsed.scheme in ("http", "https")
    assert parsed.port in (80, 443)


@pytest.mark.parametrize(
    ("url", "rule"),
    [
        ("ftp://example.com/file", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("gopher://example.com/", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("data:text/html,<script>", "scheme"),
        ("https://user:pass@example.com/", "credentials"),
        ("https://user@example.com/", "credentials"),
        ("https://example.com:8080/", "port"),
        ("https://example.com:22/", "port"),
        ("http://example.com:6379/", "port"),
        ("https:///path", "bad_url"),
        ("https://exa mple.com/", "bad_url"),
        ("https://example.com/\x00", "bad_url"),
    ],
)
def test_only_http_and_https_on_ports_80_and_443_with_no_credentials(url: str, rule: str) -> None:
    assert refused(url) == rule


def test_a_very_long_link_is_refused() -> None:
    assert refused("https://example.com/" + "a" * 3000) == "url_too_long"


# ------------------------------------------------------------------ addresses, in every spelling


@pytest.mark.parametrize(
    ("url", "rule"),
    [
        ("http://127.0.0.1/", "loopback"),
        ("http://127.1/", "loopback"),
        ("http://2130706433/", "loopback"),
        ("http://0x7f.1/", "loopback"),
        ("http://0x7f000001/", "loopback"),
        ("http://0177.0.0.1/", "loopback"),
        ("http://127.0.0.1./", "loopback"),
        ("http://[::1]/", "loopback"),
        ("http://[::ffff:127.0.0.1]/", "loopback"),
        ("http://localhost/", "loopback"),
        ("http://LOCALHOST./", "loopback"),
        ("http://app.localhost/", "loopback"),
        ("http://10.0.0.5/", "private_address"),
        ("http://172.16.0.1/", "private_address"),
        ("http://192.168.1.1/", "private_address"),
        ("http://[fd00::1]/", "private_address"),
        ("http://169.254.169.254/latest/meta-data/", "link_local"),
        ("http://169.254.1.1/", "link_local"),
        ("http://2852039166/", "link_local"),
        ("http://0xa9fea9fe/", "link_local"),
        ("http://[fe80::1]/", "link_local"),
        ("http://[::ffff:169.254.169.254]/", "link_local"),
        ("http://100.64.0.1/", "carrier_grade_nat"),
        ("http://100.127.255.255/", "carrier_grade_nat"),
        ("http://224.0.0.1/", "multicast"),
        ("http://[ff02::1]/", "multicast"),
        ("http://0.0.0.0/", "reserved_address"),
        ("http://0x/", "reserved_address"),
        ("http://255.255.255.255/", "reserved_address"),
        ("http://240.0.0.1/", "reserved_address"),
        ("http://192.0.2.1/", "reserved_address"),
        ("http://198.18.0.1/", "reserved_address"),
        ("http://[::]/", "reserved_address"),
        ("http://[2002:7f00:1::]/", "reserved_address"),  # 6to4 wrapping 127.0.0.1
        ("http://[64:ff9b::7f00:1]/", "reserved_address"),  # NAT64 wrapping 127.0.0.1
    ],
)
def test_every_private_reserved_and_loopback_address_is_refused_in_every_spelling(
    url: str, rule: str
) -> None:
    assert refused(url) == rule


@pytest.mark.parametrize(
    "url", ["http://8.8.8.8/", "https://[2606:4700:4700::1111]/", "http://1.1.1.1"]
)
def test_public_addresses_pass(url: str) -> None:
    parse_url(url)


@pytest.mark.parametrize("host", ["1.2.3.4.5", "999.1.1.1", "1.2.3.256", "256.256.256.256"])
def test_a_host_that_looks_like_an_address_but_isnt_one_is_refused(host: str) -> None:
    assert refused(f"http://{host}/") == "bad_url"


def test_unusual_spellings_normalise_to_one_address() -> None:
    expected = ipaddress.IPv4Address("127.0.0.1")
    for spelling in ("127.0.0.1", "127.1", "2130706433", "0x7f.1", "0177.0.0.1", "0x7f.0.0.0x1"):
        assert parse_ipv4_spelling(spelling) == expected, spelling
    assert parse_ipv4_spelling("example.com") is None
    assert parse_ipv4_spelling("1.2.3.4.5") is None


def test_an_ipv4_mapped_ipv6_address_is_checked_as_the_ipv4_it_wraps() -> None:
    assert refusal_for(ipaddress.ip_address("::ffff:10.0.0.1")) == (
        "private_address",
        "That's a private address.",
    )
    assert refusal_for(ipaddress.ip_address("::ffff:8.8.8.8")) is None


# ------------------------------------------------------------------ what a name resolved to


def test_one_bad_address_refuses_the_whole_name() -> None:
    check_resolved(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
    with pytest.raises(FetchRefusedError) as caught:
        check_resolved(["93.184.216.34", "10.0.0.5"])  # a name with a public and a private address
    assert caught.value.rule == "private_address"
    with pytest.raises(FetchRefusedError) as dns:
        check_resolved([])
    assert dns.value.rule == "dns"


# ------------------------------------------------------------------ finding and naming links


def test_urls_are_found_in_a_message_without_the_punctuation_around_them() -> None:
    text = (
        "Save this (https://example.com/a-post?x=1), and https://example.org/b. "
        "Also www.example.net/c! And ftp://files.example.com/x"
    )
    assert extract_urls(text) == [
        "https://example.com/a-post?x=1",
        "https://example.org/b",
        "https://www.example.net/c",
        "ftp://files.example.com/x",
    ]
    assert extract_urls("see https://a.example/x and https://a.example/x again") == [
        "https://a.example/x"
    ]
    assert extract_urls("no links here, just a.b and 3.5") == []


def test_a_url_with_balanced_brackets_keeps_them() -> None:
    assert extract_urls("https://en.example.org/wiki/Foo_(bar)") == [
        "https://en.example.org/wiki/Foo_(bar)"
    ]


def test_the_canonical_form_ignores_the_fragment_tracking_and_default_ports() -> None:
    base = canonical_url("https://example.com/post")
    for variant in (
        "http://example.com/post",
        "https://EXAMPLE.com/post/",
        "https://www.example.com/post#section-2",
        "https://example.com:443/post?utm_source=x&utm_medium=y",
        "https://example.com/post?fbclid=abc",
    ):
        assert canonical_url(variant) == base, variant
    assert canonical_url("https://example.com/post?b=2&a=1") == canonical_url(
        "https://example.com/post?a=1&b=2&utm_campaign=z"
    )
    assert canonical_url("https://example.com/post?id=1") != base


def test_only_the_host_is_ever_logged() -> None:
    assert host_for_log("https://Example.com/secret/path?token=abc#x") == "example.com"
    assert host_for_log("not a url") == ""
    assert site_name("www.example.com") == "example.com"


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://www.youtube.com/watch?v=abc123", "youtube"),
        ("https://youtu.be/abc123", "youtube"),
        ("https://m.youtube.com/shorts/abc123", "youtube"),
        ("https://vimeo.com/123456", "vimeo"),
        ("https://player.vimeo.com/video/123456", "vimeo"),
        ("https://www.youtube.com/", None),
        ("https://vimeo.com/about", None),
        ("https://www.dailymotion.com/video/x7", None),  # other video sites are treated as pages
        ("https://example.com/watch?v=1", None),
    ],
)
def test_youtube_and_vimeo_links_are_videos_and_nothing_else_is(url: str, site: str | None) -> None:
    assert video_site(url) == site
