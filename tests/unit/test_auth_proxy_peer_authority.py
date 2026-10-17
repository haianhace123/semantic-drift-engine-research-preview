"""Security regression: auth rate-limit IP must not trust raw forwarded headers.

Extract the small pure function from source: this test runs without a DB,
config or secrets, and exercises the exact route implementation.
"""
from __future__ import annotations

import ast
import types
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src/api/auth_routes.py"


def _client_ip():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "client_ip"]
    assert len(nodes) == 1
    namespace = {"Request": object}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["client_ip"]


def _request(peer, forwarded=None):
    return types.SimpleNamespace(
        client=None if peer is None else types.SimpleNamespace(host=peer),
        headers={} if forwarded is None else {"x-forwarded-for": forwarded},
    )


def test_raw_spoofed_forwarded_header_cannot_evade_ip_limit():
    fn = _client_ip()
    for fake in ("203.0.113.1", "198.51.100.5, 192.0.2.2", "invalid, 1.1.1.1"):
        assert fn(_request("192.0.2.35", fake)) == "192.0.2.35"


def test_asgi_trusted_proxy_effect_is_authoritative_not_header():
    fn = _client_ip()
    # The ASGI server may have rewritten the scope for a trusted proxy.
    assert fn(_request("198.51.100.4", "203.0.113.253")) == "198.51.100.4"
    assert fn(_request("2001:db8::1")) == "2001:db8::1"


def test_missing_peer_shares_bounded_unknown_bucket():
    fn = _client_ip()
    assert fn(_request(None, "203.0.113.8")) == "unknown"
    assert fn(_request("", "203.0.113.8")) == "unknown"


def test_route_source_has_one_ip_authority():
    source = SOURCE.read_text(encoding="utf-8-sig")
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "client_ip")
    assert not any(isinstance(n, ast.Attribute) and n.attr == "headers" for n in ast.walk(node))
    assert not any(isinstance(n, ast.Attribute) and n.attr == "TRUST_PROXY_HEADERS" for n in ast.walk(node))
    # The production server must define which upstream peers may forward.
    prod = (SOURCE.parents[2] / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert 'FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:?' in prod
    assert 'FORWARDED_ALLOW_IPS:-172.16.0.0/12' not in prod
    # Uvicorn 0.30.x accepts exact IPs only, not network/CIDR ranges.
    requirements = (SOURCE.parents[2] / "requirements.txt").read_text(encoding="utf-8")
    assert "uvicorn[standard]>=0.30.0,<0.31.0" in requirements


def test_uvicorn_restricts_header_authority_to_allowlisted_proxy_peers():
    import asyncio
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    seen = []

    async def target(scope, receive, send):
        seen.append(scope["client"][0])

    async def ignored():
        return {"type": "http.disconnect"}

    async def sent(message):
        return None

    app = ProxyHeadersMiddleware(target, trusted_hosts="172.20.0.5")

    async def exercise(peer, forwarded):
        scope = {
            "type": "http", "scheme": "http",
            "client": (peer, 12345),
            "headers": [(b"x-forwarded-for", forwarded.encode("ascii"))],
        }
        await app(scope, ignored, sent)

    # A direct request may supply any XFF; the peer remains authoritative.
    asyncio.run(exercise("198.51.100.10", "203.0.113.8"))
    assert seen[-1] == "198.51.100.10"

    # Another private peer is NOT trusted just for sharing a subnet.
    asyncio.run(exercise("172.20.0.6", "203.0.113.8"))
    assert seen[-1] == "172.20.0.6"

    # Only the exact configured Caddy peer is authorized to forward.
    # ASGI then provides the verified client identity to the auth route.
    asyncio.run(exercise("172.20.0.5", "203.0.113.8"))
    assert seen[-1] == "203.0.113.8"
