# VFX MCP Unreal Scripts

These scripts are intended to run inside the Unreal Editor Python environment.

The first integration target is:

```powershell
py unreal/Plugins/VFXMCP/Scripts/create_niagara_from_spec.py generated/specs/NS_magic_burst.json /Game/VFX/Generated
```

When executed in Unreal, this will eventually create:

- A Niagara System named by `spec.name`
- A starter emitter matching `spec.effect_type`
- A material instance using the spec color palette
- A preview actor or placement helper

The current script validates the input shape and prepares the destination folder. Niagara graph authoring is intentionally isolated behind `build_niagara_from_spec` for the next implementation pass.
