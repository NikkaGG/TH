"""Ephemeral loopback relay for authenticated HTTP proxies.

Chrome handles TLS end to end. Proxy authentication stays outside Playwright's
Fetch interception, which can abort TikTok's scripts in recent Chrome versions.
Every connection goes through the configured upstream; there is no direct route.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlparse


@asynccontextmanager
async def browser_proxy_url(proxy_url: str):
    parsed = urlparse(proxy_url)
    if parsed.scheme not in ("http", "https") or parsed.username is None:
        yield proxy_url
        return
    try:
        host, upstream_port = parsed.hostname, parsed.port
        if not host or not upstream_port:
            raise ValueError()
    except ValueError:
        raise ValueError("Некорректный адрес прокси") from None
    credentials = f"{unquote(parsed.username)}:{unquote(parsed.password or '')}"
    auth = base64.b64encode(credentials.encode("utf-8"))
    active = set()
    writers = set()

    async def pipe(reader, writer):
        try:
            while chunk := await reader.read(65536):
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()

    async def handle(reader, writer):
        task = asyncio.current_task()
        active.add(task)
        writers.add(writer)
        remote = None
        established = False
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            lines = header.split(b"\r\n")
            method, _, _ = lines[0].split(b" ", 2)
            kwargs = {"ssl": True, "server_hostname": host} if parsed.scheme == "https" else {}
            remote_reader, remote = await asyncio.wait_for(
                asyncio.open_connection(host, upstream_port, **kwargs), 20
            )
            writers.add(remote)
            headers = [line for line in lines[1:] if line and not line.lower().startswith(b"proxy-authorization:")]
            remote.write(b"\r\n".join([
                lines[0], *headers, b"Proxy-Authorization: Basic " + auth, b"", b"",
            ]))
            await remote.drain()
            if method == b"CONNECT":
                response = await asyncio.wait_for(remote_reader.readuntil(b"\r\n\r\n"), 30)
                if response.split(b" ", 2)[1] != b"200":
                    raise ConnectionError("Upstream proxy rejected the tunnel")
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            established = True
            pipes = [asyncio.create_task(pipe(reader, remote)), asyncio.create_task(pipe(remote_reader, writer))]
            try:
                await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for transfer in pipes:
                    transfer.cancel()
                await asyncio.gather(*pipes, return_exceptions=True)
        except (OSError, ValueError, IndexError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if not established and not writer.is_closing():
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            writer.close()
            writers.discard(writer)
            if remote:
                remote.close()
                writers.discard(remote)
            active.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=65536)
    try:
        relay_port = server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{relay_port}"
    finally:
        server.close()
        # Python 3.12+ waits for accepted transports, including ones whose
        # handlers already finished but whose write buffers have not drained.
        if hasattr(server, "abort_clients"):
            server.abort_clients()
        for writer in list(writers):
            writer.transport.abort()
        pending = list(active)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            await asyncio.wait_for(server.wait_closed(), 3)
        except asyncio.TimeoutError:
            # Listening sockets are already closed; never hold the task queue
            # forever while an upstream connection is finishing shutdown.
            pass
