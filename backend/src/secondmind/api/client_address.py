"""The address of the person behind a request (S4.12, NFR-3.5), for the per-address cap on new
guests.

Behind proxies, the browser's address is in ``X-Forwarded-For`` with one entry added by each proxy,
on the right. The client's address is therefore the entry ``TRUSTED_PROXY_HOPS`` from the right
(Caddy, then nginx: the second): whatever the client put at the left of the header counts for
nothing. With fewer entries than the proxies we trust (the API reached another way), the address
of the connection is used. An IPv6 address is counted as its /64, which one household holds
entirely.
"""

import ipaddress


def client_address(forwarded_for: str | None, peer: str | None, hops: int) -> str:
    entries = [e.strip() for e in (forwarded_for or "").split(",") if e.strip()]
    raw = entries[-hops] if hops >= 1 and len(entries) >= hops else (peer or "unknown")
    return normalise(raw)


def normalise(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return address[:64]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)
