from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.analyze_packages import find_package_media


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = WORKSPACE_ROOT / "generated" / "ai-art"


@dataclass(frozen=True)
class ArtGenerationRequest:
    package_path: Path
    provider: str
    prompt: str
    output_root: Path = DEFAULT_OUTPUT_ROOT
    options: dict[str, Any] | None = None


class ArtProvider:
    name = "base"

    def generate(self, request: ArtGenerationRequest) -> dict[str, Any]:
        raise NotImplementedError


class PendingProvider(ArtProvider):
    name = "pending"

    def generate(self, request: ArtGenerationRequest) -> dict[str, Any]:
        output_dir = provider_output_dir(request)
        manifest = base_manifest(request, self.name, "pending_configuration", output_dir)
        manifest["message"] = "Provider is not configured yet. Use comfyui first, or add a provider adapter."
        write_manifest(output_dir, manifest)
        return manifest


class ComfyUIProvider(ArtProvider):
    name = "comfyui"

    def generate(self, request: ArtGenerationRequest) -> dict[str, Any]:
        options = request.options or {}
        base_url = str(options.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
        workflow_path = options.get("workflow_path")
        output_dir = provider_output_dir(request)
        output_dir.mkdir(parents=True, exist_ok=True)

        manifest = base_manifest(request, self.name, "pending", output_dir)
        manifest["provider_options"] = {
            "base_url": base_url,
            "workflow_path": str(workflow_path) if workflow_path else None,
        }

        health = self.health(base_url)
        manifest["health"] = health
        if not health.get("ok"):
            manifest["status"] = "provider_unavailable"
            manifest["message"] = "ComfyUI is not reachable. Start ComfyUI, then rerun the AI art pass."
            write_manifest(output_dir, manifest)
            return manifest

        if not workflow_path:
            manifest["status"] = "needs_workflow"
            manifest["message"] = "ComfyUI is reachable, but no workflow JSON was provided."
            manifest["workflow_template"] = str(default_workflow_template())
            write_manifest(output_dir, manifest)
            return manifest

        workflow_file = resolve_from_workspace(Path(str(workflow_path)))
        if not workflow_file.exists():
            manifest["status"] = "workflow_missing"
            manifest["message"] = f"ComfyUI workflow was not found: {workflow_file}"
            write_manifest(output_dir, manifest)
            return manifest

        workflow = json.loads(workflow_file.read_text(encoding="utf-8"))
        media_files = find_package_media(request.package_path)
        reference_image = choose_upload_reference(media_files)
        uploaded_reference = None
        if reference_image and options.get("upload_reference", True):
            uploaded_reference = self.upload_image(base_url, reference_image)

        patched_workflow = patch_workflow_placeholders(
            workflow,
            {
                "PROMPT": request.prompt,
                "NEGATIVE_PROMPT": str(options.get("negative_prompt") or "watermark, text, logo, character, UI, frame"),
                "REFERENCE_IMAGE": uploaded_reference or (reference_image.name if reference_image else ""),
                "OUTPUT_PREFIX": f"vfxmcp_{request.package_path.name}_{int(time.time())}",
            },
        )

        prompt_result = self.queue_prompt(base_url, patched_workflow)
        prompt_id = prompt_result.get("prompt_id")
        manifest["prompt_id"] = prompt_id
        manifest["queue_result"] = prompt_result
        if not prompt_id:
            manifest["status"] = "queue_failed"
            manifest["message"] = "ComfyUI did not return a prompt_id."
            write_manifest(output_dir, manifest)
            return manifest

        timeout_seconds = int(options.get("timeout_seconds") or 240)
        history = self.wait_for_history(base_url, prompt_id, timeout_seconds)
        manifest["history"] = history
        outputs = download_history_images(base_url, history, output_dir)
        manifest["outputs"] = outputs
        manifest["status"] = "succeeded" if outputs else "finished_no_images"
        manifest["message"] = "AI art pass finished." if outputs else "ComfyUI finished, but no image outputs were found."
        write_manifest(output_dir, manifest)
        return manifest

    def health(self, base_url: str) -> dict[str, Any]:
        try:
            payload = get_json(f"{base_url}/system_stats", timeout=5)
            return {"ok": True, "system_stats": payload}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def upload_image(self, base_url: str, image_path: Path) -> str:
        boundary = f"----vfxmcp{uuid.uuid4().hex}"
        data = image_path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("name") or image_path.name

    def queue_prompt(self, base_url: str, workflow: dict[str, Any]) -> dict[str, Any]:
        payload = {"prompt": workflow, "client_id": f"vfxmcp-{uuid.uuid4().hex}"}
        return post_json(f"{base_url}/prompt", payload, timeout=30)

    def wait_for_history(self, base_url: str, prompt_id: str, timeout_seconds: int) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            history = get_json(f"{base_url}/history/{urllib.parse.quote(prompt_id)}", timeout=10)
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(1.5)
        raise TimeoutError(f"Timed out waiting for ComfyUI prompt: {prompt_id}")


def generate_art_pass(
    package_path: Path,
    provider: str,
    prompt: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_name = (provider or "comfyui").lower()
    request = ArtGenerationRequest(
        package_path=package_path,
        provider=provider_name,
        prompt=prompt or default_prompt(package_path),
        output_root=resolve_from_workspace(output_root),
        options=options or {},
    )
    adapter = provider_for_name(provider_name)
    return adapter.generate(request)


def provider_for_name(provider: str) -> ArtProvider:
    if provider == "comfyui":
        return ComfyUIProvider()
    return PendingProvider()


def base_manifest(request: ArtGenerationRequest, provider: str, status: str, output_dir: Path) -> dict[str, Any]:
    media_files = find_package_media(request.package_path)
    return {
        "status": status,
        "provider": provider,
        "package": request.package_path.name,
        "package_path": str(request.package_path),
        "prompt": request.prompt,
        "reference_media": [str(path) for path in media_files],
        "output_dir": str(output_dir),
        "outputs": [],
    }


def provider_output_dir(request: ArtGenerationRequest) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return resolve_from_workspace(request.output_root) / request.package_path.name / request.provider / timestamp


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def patch_workflow_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: patch_workflow_placeholders(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [patch_workflow_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        result = value
        for key, replacement in replacements.items():
            result = result.replace(f"{{{{{key}}}}}", replacement)
        return result
    return value


def download_history_images(base_url: str, history: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    for node_output in (history.get("outputs") or {}).values():
        for image in node_output.get("images", []):
            filename = image.get("filename")
            if not filename:
                continue
            query = urllib.parse.urlencode(
                {
                    "filename": filename,
                    "subfolder": image.get("subfolder") or "",
                    "type": image.get("type") or "output",
                }
            )
            data = get_bytes(f"{base_url}/view?{query}", timeout=60)
            output_path = output_dir / filename
            output_path.write_bytes(data)
            outputs.append({"filename": filename, "path": str(output_path)})
    return outputs


def choose_upload_reference(media_files: list[Path]) -> Path | None:
    static_suffixes = {".png", ".jpg", ".jpeg", ".bmp"}
    static_files = [path for path in media_files if path.suffix.lower() in static_suffixes]
    if static_files:
        return max(static_files, key=lambda path: path.stat().st_size)
    return media_files[0] if media_files else None


def get_json(url: str, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_bytes(url: str, timeout: int) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def resolve_from_workspace(path: Path) -> Path:
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def default_workflow_template() -> Path:
    return WORKSPACE_ROOT / "mcp-server" / "art_workflows" / "comfyui_vfx_img2img_template.json"


def default_prompt(package_path: Path) -> str:
    prompt_path = package_path / "prompt.md"
    designer_prompt = prompt_path.read_text(encoding="utf-8").strip() if prompt_path.exists() else ""
    return (
        f"{designer_prompt}\n\n"
        "Create a clean game VFX flipbook on transparent or black background. "
        "Preserve the reference silhouette, timing, color palette, and energy motion. "
        "No watermark, no character, no UI, no text."
    ).strip()
