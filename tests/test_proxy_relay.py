import asyncio
import base64
import unittest
from urllib.parse import urlparse

from app.workers.proxy_relay import browser_proxy_url


class ProxyRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.servers = []
        self.connections = []

    async def asyncTearDown(self):
        for reader, writer in self.connections:
            writer.close()
            await writer.wait_closed()
        for server in self.servers:
            server.close()
            await server.wait_closed()

    async def upstream(self, handler):
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    async def connect(self, url):
        parsed = urlparse(url)
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertIsNone(parsed.username)
        connection = await asyncio.open_connection(parsed.hostname, parsed.port)
        self.connections.append(connection)
        return connection

    async def test_connect_authentication_and_bidirectional_tunnel(self):
        captured = asyncio.get_running_loop().create_future()

        async def upstream(reader, writer):
            try:
                captured.set_result(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                payload = await reader.readexactly(4)
                writer.write(payload.upper())
                await writer.drain()
            finally:
                writer.close()

        port = await self.upstream(upstream)
        async with browser_proxy_url(f"http://alice%40test:p%3Ass@127.0.0.1:{port}") as local:
            reader, writer = await self.connect(local)
            writer.write(b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\nProxy-Authorization: Basic wrong\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            self.assertIn(b"200", response)
            header = await captured
            self.assertEqual(header.count(b"Proxy-Authorization:"), 1)
            self.assertIn(b"Proxy-Authorization: Basic " + base64.b64encode(b"alice@test:p:ss"), header)
            self.assertTrue(header.startswith(b"CONNECT example.test:443 HTTP/1.1"))
            writer.write(b"ping")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(4), 2), b"PING")
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", urlparse(local).port)

    async def test_rejected_upstream_does_not_leak_auth_challenge_or_connect_directly(self):
        async def upstream(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="private-upstream"\r\n\r\n')
                await writer.drain()
            finally:
                writer.close()

        port = await self.upstream(upstream)
        async with browser_proxy_url(f"http://user:secret@127.0.0.1:{port}") as local:
            reader, writer = await self.connect(local)
            writer.write(b"CONNECT unreachable.invalid:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 2)
            self.assertIn(b"502 Bad Gateway", response)
            self.assertNotIn(b"private-upstream", response)
            self.assertNotIn(b"secret", response)

    async def test_plain_http_request_body_is_forwarded_through_upstream(self):
        captured = asyncio.get_running_loop().create_future()

        async def upstream(reader, writer):
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                body = await reader.readexactly(4)
                captured.set_result((header, body))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
                await writer.drain()
            finally:
                writer.close()

        port = await self.upstream(upstream)
        async with browser_proxy_url(f"http://user:password@127.0.0.1:{port}") as local:
            reader, writer = await self.connect(local)
            writer.write(b"POST http://example.test/ HTTP/1.1\r\nHost: example.test\r\nContent-Length: 4\r\n\r\ndata")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 2)
            self.assertTrue(response.endswith(b"OK"))
            header, body = await captured
            self.assertIn(b"POST http://example.test/ HTTP/1.1", header)
            self.assertIn(b"Proxy-Authorization: Basic ", header)
            self.assertEqual(body, b"data")

    async def test_context_exit_closes_idle_client_connections(self):
        async def upstream(reader, writer):
            writer.close()

        port = await self.upstream(upstream)
        async with browser_proxy_url(f"http://user:password@127.0.0.1:{port}") as local:
            reader, writer = await self.connect(local)
            writer.write(b"CONNECT")
            await writer.drain()
            await asyncio.sleep(.01)
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")

    async def test_unavailable_upstream_returns_gateway_error(self):
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        async with browser_proxy_url(f"http://user:password@127.0.0.1:{port}") as local:
            reader, writer = await self.connect(local)
            writer.write(b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            self.assertIn(b"502 Bad Gateway", await asyncio.wait_for(reader.read(), 5))

    async def test_proxies_without_http_authentication_are_unchanged(self):
        for proxy in ("http://proxy.test:8080", "socks5://proxy.test:1080"):
            async with browser_proxy_url(proxy) as local:
                self.assertEqual(local, proxy)

    async def test_invalid_proxy_error_does_not_contain_credentials(self):
        with self.assertRaisesRegex(ValueError, "Некорректный адрес прокси") as error:
            async with browser_proxy_url("http://private-user:private-password@host:invalid"):
                self.fail("Invalid proxy accepted")
        self.assertNotIn("private-password", str(error.exception))


if __name__ == "__main__":
    unittest.main()
