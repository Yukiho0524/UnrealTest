from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

from PIL import Image, ImageFilter, ImageOps, ImageSequence


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SPRITE_SIZE = 256


def create_reference_sprite_source(
    package_name: str,
    media_files: list[Path],
    effect_type: str,
    visual_profile: dict[str, Any],
) -> str | None:
    if not media_files:
        return None

    source = choose_source_media(media_files, visual_profile)
    if not source:
        return None

    with Image.open(source) as image:
        frame = choose_frame(image)
        sprite = extract_sprite(frame, effect_type, visual_profile)

    output_dir = WORKSPACE_ROOT / "generated" / "reference-sprites" / package_name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{package_name}_primary_sprite.png"
    sprite.save(output_path)
    return str(output_path)


def choose_source_media(media_files: list[Path], visual_profile: dict[str, Any]) -> Path | None:
    file_profiles = visual_profile.get("files", [])
    if not file_profiles:
        return media_files[0] if media_files else None

    scores: list[tuple[float, Path]] = []
    for path in media_files:
        profile = next((item for item in file_profiles if Path(item.get("file", "")) == path), None)
        if not profile:
            scores.append((0.0, path))
            continue
        score = float(profile.get("bright_pixel_ratio", 0.0)) * 2.0
        score += float(profile.get("warm_pixel_ratio", 0.0)) * 2.8
        score += float(profile.get("vertical_energy", 0.0)) * 0.8
        score += 0.35 if profile.get("is_animated") else 0.0
        scores.append((score, path))

    return max(scores, key=lambda item: item[0])[1] if scores else None


def choose_frame(image: Image.Image) -> Image.Image:
    frames = [frame.copy().convert("RGBA") for frame in ImageSequence.Iterator(image)]
    if not frames:
        return image.convert("RGBA")
    if len(frames) == 1:
        return frames[0]
    return max(frames, key=frame_energy)


def frame_energy(frame: Image.Image) -> float:
    small = frame.resize((96, 96)).convert("RGBA")
    pixels = [pixel for pixel in small.getdata() if pixel[3] > 16]
    if not pixels:
        return 0.0
    return mean(luminance(pixel) + (0.4 if is_warm(pixel) else 0.0) for pixel in pixels)


def extract_sprite(frame: Image.Image, effect_type: str, visual_profile: dict[str, Any]) -> Image.Image:
    image = ImageOps.exif_transpose(frame).convert("RGBA")
    mask = build_foreground_mask(image, effect_type, visual_profile)
    bbox = mask.getbbox()
    if not bbox:
        return fallback_sprite(effect_type, visual_profile)

    expanded_bbox = expand_bbox(bbox, image.size, 0.16)
    image = image.crop(expanded_bbox)
    mask = mask.crop(expanded_bbox)
    image.putalpha(mask)

    canvas = Image.new("RGBA", (SPRITE_SIZE, SPRITE_SIZE), (0, 0, 0, 0))
    image.thumbnail((SPRITE_SIZE, SPRITE_SIZE), Image.Resampling.LANCZOS)
    offset = ((SPRITE_SIZE - image.width) // 2, (SPRITE_SIZE - image.height) // 2)
    canvas.alpha_composite(image, offset)
    return canvas


def build_foreground_mask(image: Image.Image, effect_type: str, visual_profile: dict[str, Any]) -> Image.Image:
    width, height = image.size
    pixels = image.getdata()
    alpha_values = []
    square_particles = visual_profile.get("shape_hint") == "glowing_square_particles"
    for pixel in pixels:
        if pixel[3] <= 16:
            alpha_values.append(0)
            continue
        lum = luminance(pixel)
        warm = is_warm(pixel)
        if square_particles:
            value = smoothstep(0.76, 0.98, lum)
        elif effect_type == "fire_or_flame":
            value = max(smoothstep(0.2, 0.84, lum), smoothstep(0.08, 0.5, lum) * (1.0 if warm else 0.0))
        else:
            value = smoothstep(0.34, 0.94, lum)
        alpha_values.append(int(max(0.0, min(1.0, value)) * 255))

    mask = Image.new("L", (width, height))
    mask.putdata(alpha_values)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=1.2 if square_particles else 2.4))
    return mask.point(lambda value: 0 if value < 18 else value)


def expand_bbox(bbox: tuple[int, int, int, int], size: tuple[int, int], padding_ratio: float) -> tuple[int, int, int, int]:
    left, top, right, bottom = bbox
    width = right - left
    height = bottom - top
    padding = int(max(width, height) * padding_ratio)
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(size[0], right + padding),
        min(size[1], bottom + padding),
    )


def fallback_sprite(effect_type: str, visual_profile: dict[str, Any]) -> Image.Image:
    color = (255, 255, 255, 255) if effect_type != "fire_or_flame" else (255, 210, 96, 255)
    canvas = Image.new("RGBA", (SPRITE_SIZE, SPRITE_SIZE), (0, 0, 0, 0))
    pixels = []
    square_particles = visual_profile.get("shape_hint") == "glowing_square_particles"
    for y in range(SPRITE_SIZE):
        ny = abs((y / (SPRITE_SIZE - 1) - 0.5) * 2.0)
        for x in range(SPRITE_SIZE):
            nx = abs((x / (SPRITE_SIZE - 1) - 0.5) * 2.0)
            distance = max(nx, ny) if square_particles else (nx * nx + ny * ny) ** 0.5
            alpha = int((1.0 - smoothstep(0.62, 1.0, distance)) * 255)
            pixels.append((color[0], color[1], color[2], alpha))
    canvas.putdata(pixels)
    return canvas


def luminance(pixel: tuple[int, int, int, int]) -> float:
    r, g, b, _ = pixel
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def is_warm(pixel: tuple[int, int, int, int]) -> bool:
    r, g, b, _ = pixel
    return r > 120 and g > 40 and r > b * 1.18


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 0.0
    x = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return x * x * (3.0 - 2.0 * x)
