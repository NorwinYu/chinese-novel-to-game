"""Client for the local image generation server (N2G_IMAGE_URL).

The server runs one job at a time: POST /generate returns a job id, GET /jobs/{id} reports status
and output files, GET /files/{name} downloads. Reference images must be uploaded first with
POST /upload/{name} and are then named in `refs`."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx

from ..config import get_settings


class ImageError(RuntimeError):
    pass


class ImageClient:
    def __init__(self, base_url: str | None = None, *, timeout: float = 30.0):
        url = base_url or get_settings().image_url
        if not url:
            raise ImageError("N2G_IMAGE_URL is not set")
        self.client = httpx.AsyncClient(base_url=url, timeout=timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()

    async def capabilities(self) -> dict:
        return (await self.client.get("/capabilities")).json()

    async def upload(self, path: Path, name: str | None = None) -> str:
        name = name or path.name
        resp = await self.client.post(f"/upload/{name}", content=path.read_bytes())
        if resp.status_code >= 400:
            raise ImageError(f"upload {name}: HTTP {resp.status_code} {resp.text[:200]}")
        return name

    async def submit(self, prompt: str, *, preset: str = "portrait", seed: int = 0, refs: list[str] | None = None,
                     prefix: str = "n2g", kind: str = "image", **extra) -> str:
        body = {"kind": kind, "prompt": prompt, "preset": preset, "seed": seed, "prefix": prefix, **extra}
        if refs:
            body["refs"] = refs
        resp = await self.client.post("/generate", json=body)
        if resp.status_code >= 400:
            raise ImageError(f"generate: HTTP {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        job = data.get("id") or data.get("job") or data.get("job_id")
        if not job:
            raise ImageError(f"generate: no job id in {data}")
        return job

    async def wait(self, job: str, *, poll: float = 5.0, timeout: float = 900.0) -> dict:
        t0 = time.monotonic()
        while True:
            info = (await self.client.get(f"/jobs/{job}")).json()
            status = info.get("status")
            if status == "done":
                return info
            if status in ("error", "failed"):
                raise ImageError(f"job {job} failed: {info}")
            if time.monotonic() - t0 > timeout:
                raise ImageError(f"job {job} still {status} after {timeout:.0f}s")
            await asyncio.sleep(poll)

    async def download(self, name: str, dest: Path) -> Path:
        resp = await self.client.get(f"/files/{name}")
        if resp.status_code >= 400:
            raise ImageError(f"download {name}: HTTP {resp.status_code}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest

    async def generate(self, prompt: str, dest_dir: Path, **kw) -> list[Path]:
        """Submit, wait and download every output file into dest_dir."""
        info = await self.wait(await self.submit(prompt, **kw))
        return [await self.download(f, dest_dir / f) for f in info.get("files", [])]
