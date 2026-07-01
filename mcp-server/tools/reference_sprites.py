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
        sprite = normalize_particle_sprite_color(sprite, effect_type, visual_profile)

    output_dir = WORKSPACE_ROOT / "generated" / "reference-sprites" / package_name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{package_name}_primary_sprite.png"
    sprite.save(output_path)
    return str(output_path)


def create_reference_card_source(
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
        card = extract_card_sprite(frame, effect_type, visual_profile)

    output_dir = WORKSPACE_ROOT / "generated" / "reference-sprites" / package_name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{package_name}_reference_card.png"
    card.save(output_path)
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
    if should_extract_particle_component(effect_type, visual_profile):
        component_sprite = extract_representative_particle_sprite(image, mask)
        if component_sprite:
            return component_sprite

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


def extract_card_sprite(frame: Image.Image, effect_type: str, visual_profile: dict[str, Any]) -> Image.Image:
    image = ImageOps.exif_transpose(frame).convert("RGBA")
    mask = build_card_foreground_mask(image, effect_type, visual_profile)
    bbox = mask.getbbox()
    if not bbox:
        return fallback_sprite(effect_type, visual_profile)

    expanded_bbox = expand_bbox(bbox, image.size, 0.08)
    image = image.crop(expanded_bbox)
    mask = mask.crop(expanded_bbox)
    image.putalpha(mask)

    canvas = Image.new("RGBA", (SPRITE_SIZE, SPRITE_SIZE), (0, 0, 0, 0))
    image.thumbnail((SPRITE_SIZE, SPRITE_SIZE), Image.Resampling.LANCZOS)
    offset = ((SPRITE_SIZE - image.width) // 2, (SPRITE_SIZE - image.height) // 2)
    canvas.alpha_composite(image, offset)
    return canvas


def normalize_particle_sprite_color(sprite: Image.Image, effect_type: str, visual_profile: dict[str, Any]) -> Image.Image:
    if not should_extract_particle_component(effect_type, visual_profile):
        return sprite
    palette = visual_profile.get("palette") or ["#FFFFFF", "#FFF0C8"]
    hot = hex_to_rgba(palette[0])
    warm = hex_to_rgba(palette[1] if len(palette) > 1 else "#FFF0C8")
    output = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    pixels = []
    for r, g, b, a in sprite.getdata():
        if a <= 0:
            pixels.append((0, 0, 0, 0))
            continue
        alpha = min(255, int(((a / 255.0) ** 0.52) * 255))
        original_lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        heat = max(alpha / 255.0, original_lum)
        color = mix_rgba(warm, hot, min(1.0, heat * 1.15))
        pixels.append((color[0], color[1], color[2], alpha))
    output.putdata(pixels)
    return output


def should_extract_particle_component(effect_type: str, visual_profile: dict[str, Any]) -> bool:
    return effect_type == "glowing_particles" or visual_profile.get("shape_hint") in {
        "glowing_square_particles",
        "glowing_shard_particles",
    }


def extract_representative_particle_sprite(image: Image.Image, mask: Image.Image) -> Image.Image | None:
    components = find_mask_components(mask)
    if not components:
        return None

    image_area = image.width * image.height
    candidates = []
    for bbox, area in components:
        left, top, right, bottom = bbox
        width = right - left
        height = bottom - top
        if area < 8 or area > min(220, image_area * 0.004):
            continue
        if width < 3 or height < 3:
            continue
        if width > 80 or height > 80:
            continue
        aspect = width / max(height, 1)
        if aspect < 0.22 or aspect > 4.5:
            continue
        fill = area / max(width * height, 1)
        if fill < 0.38:
            continue
        average_lum = average_masked_luminance(image, mask, bbox)
        center_bias = 1.0 - min(0.75, abs(((left + right) * 0.5 / image.width) - 0.5))
        size_score = 1.0 - min(1.0, abs(area - 110) / 180.0)
        candidates.append((average_lum * 2.2 + fill * 0.8 + size_score * 0.45 + center_bias * 0.12, bbox, area))

    if not candidates:
        return None

    _, bbox, _ = max(candidates, key=lambda item: item[0])
    expanded_bbox = expand_bbox(bbox, image.size, 0.18)
    particle = image.crop(expanded_bbox)
    particle_mask = mask.crop(expanded_bbox).filter(ImageFilter.GaussianBlur(radius=0.45))
    particle.putalpha(particle_mask)

    canvas = Image.new("RGBA", (SPRITE_SIZE, SPRITE_SIZE), (0, 0, 0, 0))
    max_size = int(SPRITE_SIZE * 0.72)
    scale = max_size / max(particle.width, particle.height)
    target_size = (max(1, int(particle.width * scale)), max(1, int(particle.height * scale)))
    particle = particle.resize(target_size, Image.Resampling.LANCZOS)
    offset = ((SPRITE_SIZE - particle.width) // 2, (SPRITE_SIZE - particle.height) // 2)
    canvas.alpha_composite(particle, offset)
    return canvas


def average_masked_luminance(image: Image.Image, mask: Image.Image, bbox: tuple[int, int, int, int]) -> float:
    left, top, right, bottom = bbox
    values = []
    for y in range(top, bottom):
        for x in range(left, right):
            if mask.getpixel((x, y)) > 24:
                values.append(luminance(image.getpixel((x, y))))
    return mean(values) if values else 0.0


def find_mask_components(mask: Image.Image) -> list[tuple[tuple[int, int, int, int], int]]:
    width, height = mask.size
    pixels = mask.load()
    visited = set()
    components: list[tuple[tuple[int, int, int, int], int]] = []
    for y in range(height):
        for x in range(width):
            if (x, y) in visited or pixels[x, y] <= 24:
                continue
            stack = [(x, y)]
            visited.add((x, y))
            xs = []
            ys = []
            while stack:
                current_x, current_y = stack.pop()
                xs.append(current_x)
                ys.append(current_y)
                for neighbor_x, neighbor_y in (
                    (current_x + 1, current_y),
                    (current_x - 1, current_y),
                    (current_x, current_y + 1),
                    (current_x, current_y - 1),
                ):
                    if (
                        0 <= neighbor_x < width
                        and 0 <= neighbor_y < height
                        and (neighbor_x, neighbor_y) not in visited
                        and pixels[neighbor_x, neighbor_y] > 24
                    ):
                        visited.add((neighbor_x, neighbor_y))
                        stack.append((neighbor_x, neighbor_y))
            components.append(((min(xs), min(ys), max(xs) + 1, max(ys) + 1), len(xs)))
    return components


def build_foreground_mask(image: Image.Image, effect_type: str, visual_profile: dict[str, Any]) -> Image.Image:
    isolated_particles = visual_profile.get("shape_hint") in {"glowing_square_particles", "glowing_shard_particles"}
    if isolated_particles:
        return build_isolated_particle_mask(image)

    width, height = image.size
    pixels = image.getdata()
    alpha_values = []
    for pixel in pixels:
        if pixel[3] <= 16:
            alpha_values.append(0)
            continue
        lum = luminance(pixel)
        warm = is_warm(pixel)
        if effect_type == "fire_or_flame":
            value = max(smoothstep(0.2, 0.84, lum), smoothstep(0.08, 0.5, lum) * (1.0 if warm else 0.0))
        else:
            value = smoothstep(0.34, 0.94, lum)
        alpha_values.append(int(max(0.0, min(1.0, value)) * 255))

    mask = Image.new("L", (width, height))
    mask.putdata(alpha_values)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=2.4))
    return mask.point(lambda value: 0 if value < 18 else value)


def build_card_foreground_mask(image: Image.Image, effect_type: str, visual_profile: dict[str, Any]) -> Image.Image:
    width, height = image.size
    luminance_image = Image.new("L", (width, height))
    luminance_values = [int(luminance(pixel) * 255) if pixel[3] > 16 else 0 for pixel in image.getdata()]
    luminance_image.putdata(luminance_values)
    local_background = luminance_image.filter(ImageFilter.GaussianBlur(radius=12.0))
    background_values = list(local_background.getdata())

    alpha_values = []
    for pixel, lum_value, background in zip(image.getdata(), luminance_values, background_values):
        if pixel[3] <= 16:
            alpha_values.append(0)
            continue
        warm = is_warm(pixel)
        local_pop = max(0, lum_value - background - 8)
        hot = max(0, lum_value - 210)
        if effect_type == "fire_or_flame":
            alpha = max(local_pop * 2.2, hot * 3.0, 120 if warm and lum_value > 80 else 0)
        else:
            alpha = max(local_pop * 2.6, hot * 3.4)
        alpha_values.append(int(max(0, min(255, alpha))))

    mask = Image.new("L", (width, height))
    mask.putdata(alpha_values)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=1.2))
    return mask.point(lambda value: 0 if value < 18 else min(255, value))


def build_isolated_particle_mask(image: Image.Image) -> Image.Image:
    width, height = image.size
    luminance_image = Image.new("L", (width, height))
    luminance_values = [int(luminance(pixel) * 255) if pixel[3] > 16 else 0 for pixel in image.getdata()]
    luminance_image.putdata(luminance_values)
    local_background = luminance_image.filter(ImageFilter.GaussianBlur(radius=7.0))
    background_values = list(local_background.getdata())

    alpha_values = []
    for pixel, lum_value, background in zip(image.getdata(), luminance_values, background_values):
        if pixel[3] <= 16:
            alpha_values.append(0)
            continue
        local_pop = max(0, lum_value - background - 14)
        hot_pop = max(0, lum_value - 226)
        warm_bonus = 18 if is_warm(pixel) else 0
        alpha = max(local_pop * 3.0, hot_pop * 4.0 + warm_bonus)
        alpha_values.append(int(max(0, min(255, alpha))))

    mask = Image.new("L", (width, height))
    mask.putdata(alpha_values)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=0.35))
    return mask.point(lambda value: 0 if value < 28 else min(255, value))


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


def hex_to_rgba(color: str) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255)


def mix_rgba(a: tuple[int, int, int, int], b: tuple[int, int, int, int], amount: float) -> tuple[int, int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return (
        int(a[0] + (b[0] - a[0]) * amount),
        int(a[1] + (b[1] - a[1]) * amount),
        int(a[2] + (b[2] - a[2]) * amount),
        255,
    )
