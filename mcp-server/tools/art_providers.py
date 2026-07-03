from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.analyze_packages import analyze_effect_package, find_package_media


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
        outputs = annotate_outputs_with_asset_passes(request.package_path, outputs)
        manifest["outputs"] = outputs
        manifest["asset_pass_candidates"] = summarize_asset_pass_candidates(outputs)
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


class OpenAIImageProvider(ArtProvider):
    name = "openai"
    api_url = "https://api.openai.com/v1/images"

    def generate(self, request: ArtGenerationRequest) -> dict[str, Any]:
        options = request.options or {}
        output_dir = provider_output_dir(request)
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = base_manifest(request, self.name, "pending", output_dir)
        model = str(options.get("model") or "gpt-image-2")
        size = str(options.get("size") or "1024x1024")
        quality = str(options.get("quality") or "high")
        output_format = str(options.get("output_format") or "png")
        background = str(options.get("background") or "auto")
        api_key = str(options.get("api_key") or os.environ.get(str(options.get("api_key_env") or "OPENAI_API_KEY")) or "")
        manifest["provider_options"] = {
            "model": model,
            "size": size,
            "quality": quality,
            "output_format": output_format,
            "background": background,
            "pass_selection": options.get("passes") or "required",
            "uses_reference_edit": bool(options.get("use_reference_edit", True)),
        }
        if not api_key:
            manifest["status"] = "provider_unavailable"
            manifest["message"] = "OPENAI_API_KEY is not set. Set it, then rerun the OpenAI art provider pass."
            write_manifest(output_dir, manifest)
            return manifest

        try:
            spec = analyze_effect_package(request.package_path)
            pass_specs = spec.vfx_plan.asset_passes if spec.vfx_plan else []
        except Exception as exc:
            manifest["status"] = "analysis_failed"
            manifest["message"] = f"Could not analyze package before OpenAI generation: {exc}"
            write_manifest(output_dir, manifest)
            return manifest

        selected_passes = select_pass_specs(pass_specs, options)
        if not selected_passes:
            manifest["status"] = "no_passes_selected"
            manifest["message"] = "No asset passes matched the requested OpenAI pass selection."
            write_manifest(output_dir, manifest)
            return manifest

        reference_image = choose_upload_reference(find_package_media(request.package_path))
        outputs: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for pass_spec in selected_passes:
            pass_name = str(pass_spec.get("name") or "vfx_pass")
            pass_prompt = openai_pass_prompt(request.prompt, pass_spec)
            try:
                if reference_image and options.get("use_reference_edit", True):
                    result = self.edit_image(
                        api_key,
                        reference_image,
                        pass_prompt,
                        model=model,
                        size=size,
                        quality=quality,
                        output_format=output_format,
                        background=background,
                        timeout=int(options.get("timeout_seconds") or 180),
                    )
                else:
                    result = self.generate_image(
                        api_key,
                        pass_prompt,
                        model=model,
                        size=size,
                        quality=quality,
                        output_format=output_format,
                        background=background,
                        timeout=int(options.get("timeout_seconds") or 180),
                    )
                image_data = first_image_bytes(result)
                if not image_data:
                    errors.append({"pass": pass_name, "error": "OpenAI response did not include b64 image data."})
                    continue
                output_path = output_dir / f"{request.package_path.name}_{safe_file_token(pass_name)}.{output_format}"
                output_path.write_bytes(image_data)
                outputs.append(
                    {
                        "filename": output_path.name,
                        "path": str(output_path),
                        "candidate_passes": [pass_name],
                        "prompt": pass_prompt,
                        "model": model,
                        "usage": result.get("usage"),
                    }
                )
            except urllib.error.HTTPError as exc:
                errors.append({"pass": pass_name, "error": http_error_message(exc)})
            except Exception as exc:
                errors.append({"pass": pass_name, "error": str(exc)})

        manifest["outputs"] = outputs
        manifest["asset_pass_candidates"] = summarize_asset_pass_candidates(outputs)
        manifest["errors"] = errors
        if outputs and errors:
            manifest["status"] = "partial_success"
            manifest["message"] = "OpenAI image pass finished with some failed passes."
        elif outputs:
            manifest["status"] = "succeeded"
            manifest["message"] = "OpenAI image pass finished."
        else:
            manifest["status"] = "failed"
            manifest["message"] = "OpenAI image pass did not produce usable image outputs."
        write_manifest(output_dir, manifest)
        return manifest

    def generate_image(
        self,
        api_key: str,
        prompt: str,
        *,
        model: str,
        size: str,
        quality: str,
        output_format: str,
        background: str,
        timeout: int,
    ) -> dict[str, Any]:
        payload = clean_payload(
            {
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "output_format": output_format,
                "background": background,
            }
        )
        return post_json_auth(f"{self.api_url}/generations", payload, api_key, timeout)

    def edit_image(
        self,
        api_key: str,
        image_path: Path,
        prompt: str,
        *,
        model: str,
        size: str,
        quality: str,
        output_format: str,
        background: str,
        timeout: int,
    ) -> dict[str, Any]:
        fields = clean_payload(
            {
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "output_format": output_format,
                "background": background,
            }
        )
        return post_multipart_auth(f"{self.api_url}/edits", fields, {"image": image_path}, api_key, timeout)


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
    if provider == "openai":
        return OpenAIImageProvider()
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


def annotate_outputs_with_asset_passes(package_path: Path, outputs: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not outputs:
        return []
    try:
        spec = analyze_effect_package(package_path)
        pass_specs = (spec.vfx_plan.asset_passes if spec.vfx_plan else [])
    except Exception:
        pass_specs = []
    pass_names = [str(pass_spec.get("name")) for pass_spec in pass_specs if pass_spec.get("name")]
    annotated = []
    for output in outputs:
        filename = str(output.get("filename") or Path(str(output.get("path", ""))).name).lower()
        candidates = [name for name in pass_names if filename_matches_pass(filename, name)]
        if not candidates and "beauty_flipbook" in pass_names:
            candidates = ["beauty_flipbook"]
        annotated.append({**output, "candidate_passes": candidates})
    return annotated


def summarize_asset_pass_candidates(outputs: list[dict[str, Any]]) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    for output in outputs:
        path = str(output.get("path") or "")
        for pass_name in output.get("candidate_passes") or []:
            summary.setdefault(pass_name, []).append(path)
    return summary


def filename_matches_pass(filename: str, pass_name: str) -> bool:
    return any(keyword in filename for keyword in keywords_for_pass(pass_name))


def keywords_for_pass(pass_name: str) -> list[str]:
    name = pass_name.lower()
    if "layer" in name and "mask" in name:
        return ["layer", "mask_pack", "packed_mask", "core_edge", "rgba_mask"]
    if "metadata" in name or "layout" in name:
        return ["metadata", "layout", "atlas_meta", "renderer"]
    if "alpha" in name or "mask" in name:
        return ["alpha", "mask", "matte", "opacity"]
    if "motion" in name:
        return ["motion", "vector", "velocity", "mv"]
    if "distortion" in name or "flow" in name:
        return ["distort", "flow", "heat", "normal"]
    if "depth" in name or "thickness" in name:
        return ["depth", "thickness", "height", "volume"]
    if "normal" in name or "lighting" in name:
        return ["normal", "lighting", "light", "sixpoint"]
    if "sdf" in name or "vector_field" in name or "field" in name:
        return ["sdf", "distance", "vector_field", "field", "curl"]
    if "smoke" in name:
        return ["smoke", "heat", "wisp"]
    if "core" in name or "flame" in name:
        return ["core", "flame", "fire", "beauty"]
    if "bolt" in name:
        return ["bolt", "branch", "lightning", "arc"]
    if "impact" in name:
        return ["impact", "flash", "burst"]
    if "ring" in name:
        return ["ring", "rune", "ground"]
    if "reference" in name:
        return ["reference", "overlay"]
    return [name]


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


def post_json_auth(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_multipart_auth(
    url: str,
    fields: dict[str, Any],
    files: dict[str, Path],
    api_key: str,
    timeout: int,
) -> dict[str, Any]:
    boundary = f"----vfxmcp{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, path in files.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode("utf-8"))
        chunks.append(b"Content-Type: application/octet-stream\r\n\r\n")
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        url,
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
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
    understanding = reference_understanding_prompt(package_path)
    contract = asset_pass_contract_prompt(package_path)
    return (
        f"{designer_prompt}\n\n"
        f"{understanding}\n\n"
        "Create a realtime game VFX asset pass bundle, not only one beauty image. "
        "Preserve the reference silhouette, timing, color palette, and energy motion. "
        "Every output must keep the same pivot, frame count, bounds, and camera framing. "
        "No watermark, no character, no UI, no text, no baked environment background.\n\n"
        f"{contract}"
    ).strip()


def reference_understanding_prompt(package_path: Path) -> str:
    try:
        spec = analyze_effect_package(package_path)
        understanding = (spec.visual_profile or {}).get("reference_understanding") or {}
    except Exception:
        understanding = {}
    if not understanding:
        return "Reference understanding: unavailable. First infer the dominant VFX structure before generating passes."
    structure = understanding.get("vfx_structure") or {}
    priorities = understanding.get("asset_pass_priorities") or []
    negative = understanding.get("negative_requirements") or []
    lines = [
        "Reference understanding:",
        f"- Effect category: {understanding.get('effect_category')}",
        f"- Dominant read: {understanding.get('dominant_read')}",
        f"- Primary form: {structure.get('primary_form')}",
        f"- Silhouette: {structure.get('silhouette')}",
        f"- Motion model: {structure.get('motion_model')}",
        f"- Required layers: {', '.join(structure.get('required_layers') or [])}",
        f"- Renderer stack: {', '.join(structure.get('renderer_bias') or [])}",
        f"- Ground role: {structure.get('ground_role')}",
        "",
        "Asset pass priorities from reference understanding:",
    ]
    lines.extend(f"- {item.get('pass')}: {item.get('priority')} priority; {item.get('reason')}" for item in priorities[:8])
    if negative:
        lines.append("")
        lines.append("Do not generate:")
        lines.extend(f"- {item}" for item in negative)
    return "\n".join(lines)


def asset_pass_contract_prompt(package_path: Path) -> str:
    try:
        spec = analyze_effect_package(package_path)
        pass_specs = spec.vfx_plan.asset_passes if spec.vfx_plan else []
    except Exception:
        pass_specs = []
    required = [item for item in pass_specs if item.get("required")]
    optional = [item for item in pass_specs if not item.get("required")]
    lines = [
        "Required output contract:",
        "- Use explicit filenames that include the pass name.",
        "- Include renderer_layout_metadata.json with columns, rows, frame_count, fps, color_space, pivot, bounds, and intended Unreal renderer.",
        "- For flipbooks, output PNG sequence or atlas with matching alpha/data passes.",
        "- Beauty-only output is blockout quality and is not acceptable as final AAA VFX.",
        "",
        "Required passes:",
    ]
    if required:
        lines.extend(f"- {item.get('name')}: {item.get('purpose')} ({item.get('format')})" for item in required)
    else:
        lines.append("- beauty_flipbook, alpha_mask, renderer_layout_metadata")
    if optional:
        lines.append("")
        lines.append("Production-quality optional/data passes:")
        lines.extend(f"- {item.get('name')}: {item.get('purpose')} ({item.get('format')})" for item in optional)
    return "\n".join(lines)


def select_pass_specs(pass_specs: list[dict[str, Any]], options: dict[str, Any]) -> list[dict[str, Any]]:
    requested = str(options.get("passes") or "required").strip().lower()
    if requested in {"all", "production"}:
        selected = list(pass_specs)
    elif requested in {"required", "minimum", "required_only"}:
        selected = [item for item in pass_specs if item.get("required")]
    else:
        requested_names = {name.strip() for name in requested.split(",") if name.strip()}
        selected = [item for item in pass_specs if str(item.get("name") or "").lower() in requested_names]
    if options.get("include_optional") and requested in {"required", "minimum", "required_only"}:
        selected = list(pass_specs)
    max_passes = options.get("max_passes")
    if max_passes:
        try:
            selected = selected[: max(1, int(max_passes))]
        except (TypeError, ValueError):
            pass
    return selected


def openai_pass_prompt(base_prompt: str, pass_spec: dict[str, Any]) -> str:
    pass_name = str(pass_spec.get("name") or "vfx_pass")
    purpose = str(pass_spec.get("purpose") or "realtime game VFX asset pass")
    output_format = str(pass_spec.get("format") or "transparent PNG texture")
    guidance = [
        base_prompt,
        "",
        f"Generate only the {pass_name} pass for this realtime Unreal VFX package.",
        f"Purpose: {purpose}",
        f"Expected format: {output_format}",
        "Center the effect on a stable pivot. Keep bounds, scale, camera framing, and timing compatible with other passes.",
        "Transparent background where possible. No watermark, text, UI, character, weapon, environment, or rectangular card border.",
    ]
    if pass_name in {"alpha_mask", "ground_ring_mask", "impact_flash_mask"}:
        guidance.append("This is a mask/data pass: use clean grayscale or alpha information, not a colored beauty render.")
    elif pass_name in {"motion_vectors", "distortion_flow", "sdf_or_vector_field"}:
        guidance.append("This is a data pass: encode directional field information cleanly; avoid painterly beauty lighting.")
    elif pass_name in {"normal_or_lighting", "depth_or_thickness", "layer_mask_pack"}:
        guidance.append("This is a production data pass: separate volume, lighting, depth, and layer controls rather than making a final beauty image.")
    else:
        guidance.append("This is a beauty/emissive VFX layer: high-detail fluid flame structure, readable silhouette, production game texture quality.")
    return "\n".join(guidance).strip()


def first_image_bytes(result: dict[str, Any]) -> bytes | None:
    for item in result.get("data") or []:
        encoded = item.get("b64_json")
        if encoded:
            return base64.b64decode(encoded)
    return None


def clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in {None, ""}}


def safe_file_token(value: str) -> str:
    token = "".join(character if character.isalnum() else "_" for character in value)
    return token.strip("_") or "asset"


def http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        body = ""
    return f"HTTP {exc.code}: {body or exc.reason}"
