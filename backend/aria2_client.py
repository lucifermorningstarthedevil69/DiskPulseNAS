"""
Minimal async JSON-RPC client for aria2c.

DiskPulse NAS talks to a real aria2c process over its RPC interface instead
of simulating downloads. aria2c must be installed and running as a daemon
on the NAS host (or in a sidecar container).

--- Setup ---

Debian/Ubuntu:
    sudo apt install aria2

Alpine:
    apk add aria2

Docker (sidecar, recommended if DiskPulse itself runs in a container):
    docker run -d --name aria2 -p 6800:6800 \
      -e RPC_SECRET=YOUR_SECRET \
      -v /path/on/host/downloads:/downloads \
      p3terx/aria2-pro

Bare metal daemon:
    aria2c --enable-rpc --rpc-listen-all=false --rpc-listen-port=6800 \
           --rpc-secret=YOUR_SECRET --dir=/path/to/downloads \
           --continue=true --max-connection-per-server=8 --split=8 \
           --daemon

Then set in backend/config.py:
    ARIA2_HOST = "http://127.0.0.1"
    ARIA2_PORT = 6800
    ARIA2_SECRET = "YOUR_SECRET"
"""
import itertools
from typing import Any, Dict, List, Optional

import aiohttp


class Aria2RpcError(Exception):
    """Raised when aria2c returns a JSON-RPC error, or is unreachable."""


class Aria2Client:
    def __init__(self, host: str = "http://127.0.0.1", port: int = 6800, secret: str = ""):
        self.endpoint = f"{host}:{port}/jsonrpc"
        self.secret = secret
        self._id_counter = itertools.count(1)

    def _params(self, *args) -> List[Any]:
        params = list(args)
        if self.secret:
            params.insert(0, f"token:{self.secret}")
        return params

    async def _call(self, method: str, *args) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": method,
            "params": self._params(*args),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ConnectionError) as e:
            raise Aria2RpcError(f"Could not reach aria2c RPC at {self.endpoint}: {e}") from e

        if "error" in data:
            raise Aria2RpcError(data["error"].get("message", "aria2 RPC error"))
        return data.get("result")

    async def add_uri(self, uris: List[str], options: Optional[Dict[str, Any]] = None) -> str:
        """Add an HTTP/HTTPS/FTP/SFTP or magnet URI. Returns the download GID."""
        return await self._call("aria2.addUri", uris, options or {})

    async def add_torrent(self, torrent_b64: str, options: Optional[Dict[str, Any]] = None) -> str:
        """Add a .torrent file (base64-encoded contents). Returns the GID."""
        return await self._call("aria2.addTorrent", torrent_b64, [], options or {})

    async def tell_status(self, gid: str) -> Dict[str, Any]:
        return await self._call(
            "aria2.tellStatus",
            gid,
            [
                "gid", "status", "totalLength", "completedLength",
                "downloadSpeed", "uploadSpeed", "connections",
                "numSeeders", "numPeers", "errorMessage", "files", "dir",
            ],
        )

    async def pause(self, gid: str) -> str:
        return await self._call("aria2.pause", gid)

    async def unpause(self, gid: str) -> str:
        return await self._call("aria2.unpause", gid)

    async def remove(self, gid: str) -> str:
        return await self._call("aria2.remove", gid)

    async def force_remove(self, gid: str) -> str:
        return await self._call("aria2.forceRemove", gid)

    async def global_stat(self) -> Dict[str, Any]:
        return await self._call("aria2.getGlobalStat")

    async def ping(self) -> bool:
        """Health check — True if aria2c RPC is reachable."""
        try:
            await self._call("aria2.getVersion")
            return True
        except Aria2RpcError:
            return False
