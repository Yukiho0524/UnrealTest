from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REFERENCES_ROOT = WORKSPACE_ROOT / "samples" / "references"
MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def ingest_reference_url(url: str, package_name: str | None = None, references_root: Path = DEFAULT_REFERENCES_ROOT) -> dict[str, Any]:
    url = (url or "").strip()
    if not url:
        raise ValueError("URL is required.")
    references_root = resolve_from_workspace(references_root)
    package = package_name or package_name_from_url(url)
    package_dir = references_root / package
    images_dir = package_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    downloads: list[dict[str, Any]] = []
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in MEDIA_EXTENSIONS:
        downloads.append(download_media(url, images_dir))
    elif is_youtube_url(url):
        downloads.extend(download_youtube_thumbnails(url, images_dir))
    else:
        downloads.extend(download_page_images(url, images_dir))

    config_path = package_dir / "config.json"
    if not config_path.exists():
        config_path.write_text(
            json.dumps(
                {
                    "source_url": url,
                    "high_similarity": True,
                    "reference_card": {"enabled": False},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    prompt_path = package_dir / "prompt.md"
    if not prompt_path.exists():
        prompt_path.write_text(f"Reference source URL: {url}\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "source_url": url,
        "package": package,
        "package_path": str(package_dir),
        "downloads": downloads,
        "media_count": len([item for item in downloads if item.get("status") == "downloaded"]),
        "status": "ready" if any(item.get("status") == "downloaded" for item in downloads) else "no_media_found",
    }
    manifest_path = package_dir / "url_ingest_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def download_media(url: str, images_dir: Path, filename: str | None = None) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in MEDIA_EXTENSIONS:
        suffix = ".jpg"
    output_name = safe_filename(filename or Path(parsed.path).name or f"reference_{int(time.time())}{suffix}")
    if not Path(output_name).suffix:
        output_name += suffix
    output_path = images_dir / output_name
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VFXMCP/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        output_path.write_bytes(data)
        return {"url": url, "path": str(output_path), "status": "downloaded", "bytes": len(data)}
    except Exception as exc:
        return {"url": url, "path": str(output_path), "status": "failed", "error": str(exc)}


def download_youtube_thumbnails(url: str, images_dir: Path) -> list[dict[str, Any]]:
    video_id = youtube_video_id(url)
    if not video_id:
        return [{"url": url, "status": "failed", "error": "Could not parse YouTube video id."}]
    candidates = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    ]
    results = []
    for index, candidate in enumerate(candidates, start=1):
        result = download_media(candidate, images_dir, filename=f"youtube_{video_id}_{index}.jpg")
        results.append(result)
        if result.get("status") == "downloaded" and int(result.get("bytes") or 0) > 2048:
            break
    return results


def download_page_images(url: str, images_dir: Path, limit: int = 8) -> list[dict[str, Any]]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VFXMCP/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        return [{"url": url, "status": "failed", "error": str(exc)}]
    image_urls = extract_image_urls(html, url)
    results = []
    for index, image_url in enumerate(image_urls[:limit], start=1):
        results.append(download_media(image_url, images_dir, filename=f"reference_url_{index}{Path(urllib.parse.urlparse(image_url).path).suffix or '.jpg'}"))
    return results


def extract_image_urls(html: str, page_url: str) -> list[str]:
    candidates = []
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<img[^>]+src=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, html, flags=re.IGNORECASE))
    result = []
    for candidate in candidates:
        absolute = urllib.parse.urljoin(page_url, candidate)
        suffix = Path(urllib.parse.urlparse(absolute).path).suffix.lower()
        if suffix in MEDIA_EXTENSIONS and absolute not in result:
            result.append(absolute)
    return result


def is_youtube_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def youtube_video_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if "youtu.be" in parsed.netloc.lower():
        return parsed.path.strip("/") or None
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]
    match = re.search(r"/(?:shorts|embed)/([^/?#]+)", parsed.path)
    return match.group(1) if match else None


def package_name_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if is_youtube_url(url):
        video_id = youtube_video_id(url)
        if video_id:
            return safe_filename(f"url_{video_id}")
    stem = Path(parsed.path).stem or parsed.netloc or "url_reference"
    return safe_filename(stem.lower())


def safe_filename(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return token or "reference"


def resolve_from_workspace(path: Path) -> Path:
    return path if path.is_absolute() else WORKSPACE_ROOT / path
