from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from project_registry import find_unreal_projects
from tools.analyze_packages import analyze_effect_package, list_effect_packages
from tools.art_providers import generate_art_pass
from tools.asset_passes import apply_asset_pass_manifest_to_spec_dict, build_asset_pass_manifest
from tools.review_gates import review_effect_package
from tools.unreal_bridge import create_niagara_from_spec_command, open_unreal_asset, run_unreal_generation, write_package_spec, write_spec_dict


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def run_ui(host: str, port: int, references_root: Path, output_root: Path) -> None:
    references_root = resolve_from_workspace(references_root)
    output_root = resolve_from_workspace(output_root)

    class VFXMCPRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self.respond_html(render_index_html())
                return
            if path == "/api/state":
                self.respond_json(build_state(references_root))
                return
            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/analyze":
                payload = self.read_json()
                package_path = package_path_from_payload(references_root, payload)
                spec = analyze_effect_package(package_path)
                self.respond_json({"spec": spec.to_dict()})
                return
            if path == "/api/generate":
                payload = self.read_json()
                package_path = package_path_from_payload(references_root, payload)
                destination_path = payload.get("destinationPath") or f"/Game/VFX/Generated/{package_path.name}"
                spec = analyze_effect_package(package_path)
                spec_path = write_package_spec(spec, output_root)
                command = create_niagara_from_spec_command(spec_path, destination_path)
                self.respond_json(
                    {
                        "spec": spec.to_dict(),
                        "specFile": str(spec_path),
                        "destinationPath": destination_path,
                        "unrealCommand": command,
                        "message": "Generated VFXSpec. Unreal asset creation is ready for the Unreal bridge step.",
                    }
                )
                return
            if path == "/api/generate-art":
                payload = self.read_json()
                package_path = package_path_from_payload(references_root, payload)
                result = generate_art_pass(
                    package_path,
                    payload.get("artProvider") or "comfyui",
                    prompt=payload.get("artPrompt"),
                    options={
                        "base_url": payload.get("comfyBaseUrl") or "http://127.0.0.1:8188",
                        "workflow_path": payload.get("comfyWorkflowPath") or None,
                        "negative_prompt": payload.get("negativePrompt") or None,
                    },
                )
                asset_manifest = build_asset_pass_manifest(package_path)
                self.respond_json({"art": result, "assetPassManifest": asset_manifest})
                return
            if path == "/api/prepare-assets":
                payload = self.read_json()
                package_path = package_path_from_payload(references_root, payload)
                result = build_asset_pass_manifest(package_path)
                self.respond_json({"assetPassManifest": result})
                return
            if path == "/api/review":
                payload = self.read_json()
                package_path = package_path_from_payload(references_root, payload)
                destination_path = payload.get("destinationPath") or f"/Game/VFX/Generated/{package_path.name}"
                result = review_effect_package(package_path, destination_path=destination_path)
                self.respond_json({"review": result})
                return
            if path == "/api/generate-unreal":
                payload = self.read_json()
                package_path = package_path_from_payload(references_root, payload)
                destination_path = payload.get("destinationPath") or f"/Game/VFX/Generated/{package_path.name}"
                project = project_from_payload(payload)
                spec = analyze_effect_package(package_path)
                asset_manifest = build_asset_pass_manifest(package_path)
                spec_dict = apply_asset_pass_manifest_to_spec_dict(spec.to_dict(), asset_manifest)
                spec_path = write_spec_dict(spec_dict, output_root, spec.name)
                editor_cmd_path = editor_cmd_from_editor_path(Path(project["editorPath"]))
                script_path = WORKSPACE_ROOT / "unreal" / "Plugins" / "VFXMCP" / "Scripts" / "create_niagara_from_spec.py"
                result = run_unreal_generation(
                    editor_cmd_path,
                    Path(project["path"]),
                    script_path,
                    spec_path,
                    destination_path,
                )
                self.respond_json(
                    {
                        "spec": spec_dict,
                        "specFile": str(spec_path),
                        "destinationPath": destination_path,
                        "assetPassManifest": asset_manifest,
                        "unreal": result,
                    }
                )
                return
            if path == "/api/open-unreal":
                payload = self.read_json()
                package_path = package_path_from_payload(references_root, payload)
                destination_path = payload.get("destinationPath") or f"/Game/VFX/Generated/{package_path.name}"
                project = project_from_payload(payload)
                spec = analyze_effect_package(package_path)
                asset_path = payload.get("assetPath") or f"{destination_path}/BP_{spec.name}_VFXPreview"
                fallback_asset_path = f"{destination_path}/NS_{spec.name}"
                result = open_unreal_asset(
                    Path(project["editorPath"]),
                    Path(project["path"]),
                    asset_path,
                    fallback_asset_path=fallback_asset_path,
                )
                self.respond_json({"assetPath": asset_path, "fallbackAssetPath": fallback_asset_path, "unreal": result})
                return
            self.send_error(404, "Not found")

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            body = self.rfile.read(length).decode("utf-8")
            return json.loads(body)

        def respond_html(self, html: str) -> None:
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def respond_json(self, payload: dict) -> None:
            encoded = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            print(f"[vfx-mcp-ui] {self.address_string()} - {format % args}")

    server = ThreadingHTTPServer((host, port), VFXMCPRequestHandler)
    print(f"VFX MCP UI: http://{host}:{port}")
    print(f"References: {references_root}")
    print(f"Output: {output_root}")
    server.serve_forever()


def resolve_from_workspace(path: Path) -> Path:
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def build_state(references_root: Path) -> dict:
    return {
        "workspaceRoot": str(WORKSPACE_ROOT),
        "referencesRoot": str(references_root),
        "packages": list_effect_packages(references_root),
        "projects": find_unreal_projects(WORKSPACE_ROOT),
        "artProviders": [
            {
                "id": "comfyui",
                "label": "ComfyUI",
                "defaultBaseUrl": "http://127.0.0.1:8188",
                "workflowTemplate": str(WORKSPACE_ROOT / "mcp-server" / "art_workflows" / "comfyui_vfx_img2img_template.json"),
            }
        ],
    }


def package_path_from_payload(references_root: Path, payload: dict) -> Path:
    package_name = payload.get("packageName")
    if not package_name:
        raise ValueError("packageName is required")
    return references_root / package_name


def project_from_payload(payload: dict) -> dict[str, str]:
    project_path = payload.get("projectPath")
    if not project_path:
        raise ValueError("projectPath is required")
    projects = find_unreal_projects(WORKSPACE_ROOT)
    for project in projects:
        if project["path"] == project_path:
            return project
    raise ValueError(f"Unknown Unreal project: {project_path}")


def editor_cmd_from_editor_path(editor_path: Path) -> Path:
    if editor_path.name.lower() == "unrealeditor.exe":
        return editor_path.with_name("UnrealEditor-Cmd.exe")
    return editor_path


def render_index_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VFX MCP</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "Segoe UI", Arial, sans-serif;
      color: #202124;
      background: #f6f7f9;
    }
    body {
      margin: 0;
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px;
    }
    h1 {
      font-size: 28px;
      margin: 0 0 6px;
      letter-spacing: 0;
    }
    .subtitle {
      margin: 0 0 24px;
      color: #62666d;
    }
    .layout {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 18px;
      align-items: start;
    }
    section, aside {
      background: #ffffff;
      border: 1px solid #dfe3e8;
      border-radius: 8px;
      padding: 18px;
    }
    label {
      display: block;
      font-weight: 600;
      margin: 14px 0 6px;
    }
    select, input {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #c6ccd3;
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 14px;
      background: #fff;
    }
    button {
      border: 0;
      border-radius: 6px;
      background: #1663d8;
      color: white;
      padding: 10px 14px;
      font-weight: 700;
      cursor: pointer;
      margin-top: 14px;
      margin-right: 8px;
    }
    button.secondary {
      background: #394150;
    }
    textarea {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #c6ccd3;
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 14px;
      min-height: 96px;
      resize: vertical;
      background: #fff;
      font-family: inherit;
    }
    pre {
      min-height: 420px;
      overflow: auto;
      background: #101418;
      color: #d9f5e5;
      border-radius: 8px;
      padding: 16px;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    .meta {
      color: #62666d;
      font-size: 13px;
      line-height: 1.5;
      margin-top: 12px;
    }
    @media (max-width: 820px) {
      main {
        padding: 18px;
      }
      .layout {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <h1>VFX MCP</h1>
    <p class="subtitle">Generate a VFXSpec from a designer reference package and target an Unreal project path.</p>
    <div class="layout">
      <aside>
        <label for="project">Unreal Project</label>
        <select id="project"></select>

        <label for="package">Effect Package</label>
        <select id="package"></select>

        <label for="destination">Destination Path</label>
        <input id="destination" value="/Game/VFX/Generated/fire">

        <label for="artProvider">Art Provider</label>
        <select id="artProvider"></select>

        <label for="comfyBaseUrl">ComfyUI Base URL</label>
        <input id="comfyBaseUrl" value="http://127.0.0.1:8188">

        <label for="comfyWorkflowPath">ComfyUI Workflow JSON</label>
        <input id="comfyWorkflowPath" placeholder="mcp-server/art_workflows/my_workflow.json">

        <label for="artPrompt">AI Art Prompt</label>
        <textarea id="artPrompt" placeholder="Optional. Empty uses prompt.md plus VFX flipbook instructions."></textarea>

        <button id="analyze">Analyze Package</button>
        <button class="secondary" id="generate">Generate Spec</button>
        <button class="secondary" id="generateArt">Generate AI Art Pass</button>
        <button class="secondary" id="prepareAssets">Prepare AAA Passes</button>
        <button class="secondary" id="generateUnreal">Generate Unreal Assets</button>
        <button class="secondary" id="openUnreal">Open In Unreal</button>
        <button class="secondary" id="review">Review Gates</button>

        <div class="meta" id="meta"></div>
      </aside>
      <section>
        <pre id="output">Loading...</pre>
      </section>
    </div>
  </main>
  <script>
    const projectSelect = document.querySelector("#project");
    const packageSelect = document.querySelector("#package");
    const destinationInput = document.querySelector("#destination");
    const artProviderSelect = document.querySelector("#artProvider");
    const comfyBaseUrlInput = document.querySelector("#comfyBaseUrl");
    const comfyWorkflowPathInput = document.querySelector("#comfyWorkflowPath");
    const artPromptInput = document.querySelector("#artPrompt");
    const output = document.querySelector("#output");
    const meta = document.querySelector("#meta");

    async function request(path, options = {}) {
      const response = await fetch(path, {
        headers: {"Content-Type": "application/json"},
        ...options
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }

    function show(value) {
      output.textContent = JSON.stringify(value, null, 2);
    }

    function selectedPayload() {
      return {
        projectPath: projectSelect.value,
        packageName: packageSelect.value,
        destinationPath: destinationInput.value,
        artProvider: artProviderSelect.value,
        comfyBaseUrl: comfyBaseUrlInput.value,
        comfyWorkflowPath: comfyWorkflowPathInput.value,
        artPrompt: artPromptInput.value
      };
    }

    async function loadState() {
      const state = await request("/api/state");
      projectSelect.innerHTML = state.projects.map(project =>
        `<option value="${project.path}">${project.name} (${project.engineAssociation})</option>`
      ).join("");
      packageSelect.innerHTML = state.packages.map(pkg =>
        `<option value="${pkg.name}">${pkg.name} (${pkg.media_count} media)</option>`
      ).join("");
      artProviderSelect.innerHTML = state.artProviders.map(provider =>
        `<option value="${provider.id}">${provider.label}</option>`
      ).join("");
      if (state.artProviders[0]) {
        comfyBaseUrlInput.value = state.artProviders[0].defaultBaseUrl;
        comfyWorkflowPathInput.placeholder = state.artProviders[0].workflowTemplate;
      }
      if (state.packages[0]) {
        destinationInput.value = `/Game/VFX/Generated/${state.packages[0].name}`;
      }
      meta.textContent = `References: ${state.referencesRoot}`;
      show(state);
    }

    document.querySelector("#analyze").addEventListener("click", async () => {
      show(await request("/api/analyze", {
        method: "POST",
        body: JSON.stringify(selectedPayload())
      }));
    });

    document.querySelector("#generate").addEventListener("click", async () => {
      show(await request("/api/generate", {
        method: "POST",
        body: JSON.stringify(selectedPayload())
      }));
    });

    document.querySelector("#generateArt").addEventListener("click", async () => {
      output.textContent = "Running AI art provider pass...";
      show(await request("/api/generate-art", {
        method: "POST",
        body: JSON.stringify(selectedPayload())
      }));
    });

    document.querySelector("#prepareAssets").addEventListener("click", async () => {
      output.textContent = "Preparing AAA asset pass manifest...";
      show(await request("/api/prepare-assets", {
        method: "POST",
        body: JSON.stringify(selectedPayload())
      }));
    });

    document.querySelector("#generateUnreal").addEventListener("click", async () => {
      output.textContent = "Launching Unreal Engine 5.7.4. This can take a minute...";
      show(await request("/api/generate-unreal", {
        method: "POST",
        body: JSON.stringify(selectedPayload())
      }));
    });

    document.querySelector("#openUnreal").addEventListener("click", async () => {
      output.textContent = "Opening Unreal Editor and focusing the generated VFX asset...";
      show(await request("/api/open-unreal", {
        method: "POST",
        body: JSON.stringify(selectedPayload())
      }));
    });

    document.querySelector("#review").addEventListener("click", async () => {
      output.textContent = "Reviewing generated VFX gates...";
      show(await request("/api/review", {
        method: "POST",
        body: JSON.stringify(selectedPayload())
      }));
    });

    loadState().catch(error => output.textContent = error.stack || String(error));
  </script>
</body>
</html>"""
