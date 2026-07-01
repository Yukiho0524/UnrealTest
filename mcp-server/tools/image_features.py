from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from PIL import Image, ImageSequence


def analyze_media_files(media_files: list[Path]) -> dict[str, Any]:
    if not media_files:
        return {}

    image_profiles = [analyze_media_file(path) for path in media_files]
    palettes = [color for profile in image_profiles for color in profile["palette"]]
    palette = effect_palette(image_profiles, dominant_palette(palettes))

    return {
        "media_count": len(media_files),
        "animated_count": sum(1 for profile in image_profiles if profile["is_animated"]),
        "palette": palette,
        "brightness": round(mean(profile["brightness"] for profile in image_profiles), 3),
        "bright_pixel_ratio": round(mean(profile["bright_pixel_ratio"] for profile in image_profiles), 3),
        "warm_pixel_ratio": round(mean(profile["warm_pixel_ratio"] for profile in image_profiles), 3),
        "vertical_energy": round(mean(profile["vertical_energy"] for profile in image_profiles), 3),
        "base_energy": round(mean(profile["base_energy"] for profile in image_profiles), 3),
        "center_energy": round(mean(profile["center_energy"] for profile in image_profiles), 3),
        "dark_smoke_ratio": round(mean(profile["dark_smoke_ratio"] for profile in image_profiles), 3),
        "sparks_hint": any(profile["sparks_hint"] for profile in image_profiles),
        "motion_hint": infer_motion_hint(image_profiles),
        "shape_hint": infer_shape_hint(image_profiles),
        "style_hint": infer_style_hint(image_profiles),
        "files": image_profiles,
    }


def analyze_media_file(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        frames = sample_frames(image)
        frame_profiles = [analyze_frame(frame) for frame in frames]

    return {
        "file": str(path),
        "is_animated": path.suffix.lower() == ".gif" or len(frame_profiles) > 1,
        "frame_count_sampled": len(frame_profiles),
        "palette": dominant_palette([color for profile in frame_profiles for color in profile["palette"]]),
        "brightness": round(mean(profile["brightness"] for profile in frame_profiles), 3),
        "bright_pixel_ratio": round(mean(profile["bright_pixel_ratio"] for profile in frame_profiles), 3),
        "warm_pixel_ratio": round(mean(profile["warm_pixel_ratio"] for profile in frame_profiles), 3),
        "vertical_energy": round(mean(profile["vertical_energy"] for profile in frame_profiles), 3),
        "base_energy": round(mean(profile["base_energy"] for profile in frame_profiles), 3),
        "center_energy": round(mean(profile["center_energy"] for profile in frame_profiles), 3),
        "dark_smoke_ratio": round(mean(profile["dark_smoke_ratio"] for profile in frame_profiles), 3),
        "sparks_hint": any(profile["sparks_hint"] for profile in frame_profiles),
    }


def sample_frames(image: Image.Image, max_frames: int = 6) -> list[Image.Image]:
    frames = [frame.copy().convert("RGBA") for frame in ImageSequence.Iterator(image)]
    if not frames:
        return [image.convert("RGBA")]
    if len(frames) <= max_frames:
        return frames

    step = max(1, len(frames) // max_frames)
    return frames[::step][:max_frames]


def analyze_frame(frame: Image.Image) -> dict[str, Any]:
    image = frame.resize((96, 96)).convert("RGBA")
    pixels = list(image.getdata())
    visible = [pixel for pixel in pixels if pixel[3] > 16]
    if not visible:
        visible = pixels

    warm_pixels = [pixel for pixel in visible if is_warm(pixel)]
    bright_pixels = [pixel for pixel in visible if luminance(pixel) > 0.72]
    dark_warm_pixels = [pixel for pixel in visible if luminance(pixel) < 0.25 and is_warm(pixel, loose=True)]
    spark_pixels = [pixel for pixel in visible if luminance(pixel) > 0.82 and pixel[0] > 190 and pixel[1] > 120]

    heat_map = build_heat_map(image)
    return {
        "palette": dominant_palette([rgb_to_hex(pixel[:3]) for pixel in warm_pixels or visible]),
        "brightness": average_luminance(visible),
        "bright_pixel_ratio": ratio(len(bright_pixels), len(visible)),
        "warm_pixel_ratio": ratio(len(warm_pixels), len(visible)),
        "vertical_energy": heat_map["vertical_energy"],
        "base_energy": heat_map["base_energy"],
        "center_energy": heat_map["center_energy"],
        "dark_smoke_ratio": ratio(len(dark_warm_pixels), len(visible)),
        "sparks_hint": ratio(len(spark_pixels), len(visible)) > 0.015,
    }


def build_heat_map(image: Image.Image) -> dict[str, float]:
    width, height = image.size
    weighted_points: list[tuple[int, int, float]] = []
    for y in range(height):
        for x in range(width):
            r, g, b, a = image.getpixel((x, y))
            if a <= 16:
                continue
            energy = max(luminance((r, g, b, a)), 0.0)
            if is_warm((r, g, b, a), loose=True):
                energy *= 1.35
            if energy > 0.2:
                weighted_points.append((x, y, energy))

    total = sum(point[2] for point in weighted_points) or 1.0
    center_band = sum(point[2] for point in weighted_points if width * 0.36 <= point[0] <= width * 0.64)
    vertical_band = sum(point[2] for point in weighted_points if point[1] <= height * 0.72 and width * 0.38 <= point[0] <= width * 0.62)
    base_band = sum(point[2] for point in weighted_points if point[1] >= height * 0.62)
    return {
        "center_energy": round(center_band / total, 3),
        "vertical_energy": round(vertical_band / total, 3),
        "base_energy": round(base_band / total, 3),
    }


def infer_motion_hint(profiles: list[dict[str, Any]]) -> str:
    vertical = mean(profile["vertical_energy"] for profile in profiles)
    base = mean(profile["base_energy"] for profile in profiles)
    if vertical > 0.24:
        return "vertical_column_rise"
    if base > 0.45:
        return "ground_ring_burst"
    return "radial_burst"


def infer_shape_hint(profiles: list[dict[str, Any]]) -> str:
    vertical = mean(profile["vertical_energy"] for profile in profiles)
    base = mean(profile["base_energy"] for profile in profiles)
    center = mean(profile["center_energy"] for profile in profiles)
    if vertical > 0.24 and center > 0.32:
        return "bright_core_column_with_outer_flames"
    if base > 0.42:
        return "ground_ring_with_upward_flare"
    return "compact_sprite_burst"


def infer_style_hint(profiles: list[dict[str, Any]]) -> str:
    bright = mean(profile["bright_pixel_ratio"] for profile in profiles)
    warm = mean(profile["warm_pixel_ratio"] for profile in profiles)
    smoke = mean(profile["dark_smoke_ratio"] for profile in profiles)
    vertical = mean(profile["vertical_energy"] for profile in profiles)
    if (bright > 0.08 and warm > 0.12) or (vertical > 0.4 and any(profile["sparks_hint"] for profile in profiles)):
        return "high_intensity_stylized_fire"
    if smoke > 0.08:
        return "smoky_fire_impact"
    return "stylized_energy"


def effect_palette(profiles: list[dict[str, Any]], palette: list[str]) -> list[str]:
    shape_hint = infer_shape_hint(profiles)
    style_hint = infer_style_hint(profiles)
    if shape_hint == "bright_core_column_with_outer_flames" or style_hint == "high_intensity_stylized_fire":
        warm = next((color for color in palette if color not in {"#FFF8C8", "#FFD36A"}), "#FF6A21")
        return ["#FFF8C8", "#FFD36A", warm, "#371008"]
    return palette


def dominant_palette(colors: list[str], max_colors: int = 4) -> list[str]:
    if not colors:
        return []
    return [color for color, _ in Counter(colors).most_common(max_colors)]


def average_luminance(pixels: list[tuple[int, int, int, int]]) -> float:
    return round(mean(luminance(pixel) for pixel in pixels), 3)


def luminance(pixel: tuple[int, int, int, int]) -> float:
    r, g, b, _ = pixel
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def is_warm(pixel: tuple[int, int, int, int], loose: bool = False) -> bool:
    r, g, b, _ = pixel
    if loose:
        return r > b * 1.15 and r > 70
    return r > 135 and g > 60 and r > b * 1.35


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    quantized = tuple(round(channel / 24) * 24 for channel in (r, g, b))
    return "#{:02X}{:02X}{:02X}".format(*[min(255, value) for value in quantized])


def ratio(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(value / total, 3)
