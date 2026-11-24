import copy
import fnmatch
import json
import random
import re
import shutil
import sys
from pathlib import Path

# --- Layout settings ---
POS_JITTER = 0.35       # Max X/Z nudge per chunk
FULL_360_ROT = True     # True = any Y angle (0-359°), False = 90° steps +/- 20°

VS_ASSETS_DIR = Path(r"D:\Vintagestory\assets\survival")
OUT_SHAPES_DIR = Path("assets/mineralpiles/shapes/item")
OUT_PATCHES_DIR = Path("assets/mineralpiles/patches")

# Standalone crystal items (auto-located under itemtypes/ and textures read from vanilla files)
STANDALONE_CRYSTALS = [
    "clearquartz",
    "rosequartz",
    "smokyquartz",
    "amethyst",
]

# Ungraded ores inside ore-ungraded.json (Normal Quartz is added automatically using quartz-pile.json)
UNGRADED_ORES = [
    "olivine",
    "cinnabar",
    "sylvite",
    "fluorite",      # Fluorine
    "sulfur",
    "phosphorite",
    "corundum",
    "kernite",
    "graphite",
    "lapislazuli",   # Rough lapis lazuli
    "borax",
    "alum",          # Taller chunk -> auto-capped layers & stackingCapacity
]


def load_vs_json(path: Path):
    """Parse Vintage Story's lenient JSON (comments, single quotes, unquoted keys, trailing commas, .5 floats)."""
    raw = path.read_text(encoding="utf-8-sig")

    def _sq_to_dq(inner: str) -> str:
        converted = re.sub(
            r'\\(.)|"',
            lambda m: (
                "'" if m.group(1) == "'"
                else f"\\{m.group(1)}" if m.group(1) is not None
                else '\\"'
            ),
            inner,
        )
        return f'"{converted}"'

    token_re = re.compile(
        r'(?P<dstr>"(?:\\.|[^"\\])*")'
        r"|(?P<sstr>'(?:\\.|[^'\\])*')"
        r"|(?P<lcom>//[^\r\n]*)"
        r"|(?P<bcom>/\*[\s\S]*?\*/)"
        r"|(?P<ldot>(?<![A-Za-z0-9_.])[+-]?\.\d+(?:[eE][+-]?\d+)?)"
        r"|(?P<tdot>(?<![A-Za-z0-9_.])-?\d+\.(?=\s*[,}\]]))"
        r"|(?P<lplus>(?<![A-Za-z0-9_.])\+\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    )

    def _replace_token(m: re.Match) -> str:
        kind = m.lastgroup
        if kind == "dstr":
            return m.group(0)
        if kind == "sstr":
            return _sq_to_dq(m.group(0)[1:-1])
        if kind == "lcom":
            return ""
        if kind == "bcom":
            return re.sub(r"[^\r\n]", "", m.group(0))
        if kind == "ldot":
            val = m.group(0).lstrip("+")
            return f"-0.{val[2:]}" if val.startswith("-.") else f"0.{val[1:]}"
        if kind == "tdot":
            return f"{m.group(0)}0"
        if kind == "lplus":
            return m.group(0)[1:]
        return m.group(0)

    text = token_re.sub(_replace_token, raw)

    # Quote unquoted object keys outside strings
    text = re.sub(
        r'("(?:\\.|[^"\\])*")|([{,]\s*)([A-Za-z_$][A-Za-z0-9_$-]*)(\s*:)',
        lambda m: m.group(1) if m.group(1) is not None else f'{m.group(2)}"{m.group(3)}"{m.group(4)}',
        text,
    )

    # Strip trailing commas (including nested ones) outside strings
    text = re.sub(
        r'("(?:\\.|[^"\\])*")|,\s*(?=[}\]])',
        lambda m: m.group(1) if m.group(1) is not None else "",
        text,
    )

    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError as e:
        lines = text.splitlines()
        bad_line = lines[e.lineno - 1] if 0 < e.lineno <= len(lines) else ""
        print(f"\nFailed to parse {path}: {e}")
        print(f"  Line {e.lineno}: {bad_line}")
        print(f"           {' ' * max(0, e.colno - 1)}^")
        raise


def get_seed() -> int:
    if len(sys.argv) > 1:
        try:
            return int(sys.argv[1])
        except ValueError:
            print(f"Invalid seed '{sys.argv[1]}'. Pass a whole number, e.g.: py generate_piles.py 829845863")
            sys.exit(1)
    return random.randint(1, 999_999_999)


def cleanup_old_files():
    """Remove obsolete shapes/patches so they don't collide or get packed into the zip."""
    old_files = [
        OUT_SHAPES_DIR / "mineral-pile.json",
        OUT_SHAPES_DIR / "clearquartz-pile.json",
        OUT_PATCHES_DIR / "quartz-pile.json",
    ]
    for old_file in old_files:
        if old_file.exists():
            old_file.unlink()
            print(f"Removed old file: {old_file}")

    old_dir = Path("assets/mineralpiles/shapes/block")
    if old_dir.exists():
        shutil.rmtree(old_dir)
        print(f"Removed old folder: {old_dir}")


def build_shared_layout(seed: int):
    """Generate 64 (layer, cx, cz, rot) placements once so every mineral uses identical positions."""
    rng = random.Random(seed)
    coords = [2.3, 6.1, 9.9, 13.7]
    layout = []

    for layer in range(4):
        slots = [(row, col) for row in range(4) for col in range(4)]
        rng.shuffle(slots)

        for row, col in slots:
            cx = round(max(2.1, min(13.9, coords[col] + rng.uniform(-POS_JITTER, POS_JITTER))), 2)
            cz = round(max(2.1, min(13.9, coords[row] + rng.uniform(-POS_JITTER, POS_JITTER))), 2)
            rot = rng.randint(0, 359) if FULL_360_ROT else rng.choice([0, 90, 180, 270]) + rng.randint(-20, 20)
            layout.append((layer, cx, cz, rot))

    return layout


def flatten_elements(elements, offset=(0.0, 0.0, 0.0)):
    """Flatten child boxes to top-level elements and skip invisible zero-size helper parents."""
    flat = []
    for el in elements:
        ox, oy, oz = offset
        ef = el.get("from", [0.0, 0.0, 0.0])
        et = el.get("to", [0.0, 0.0, 0.0])

        abs_from = [ef[0] + ox, ef[1] + oy, ef[2] + oz]
        abs_to = [et[0] + ox, et[1] + oy, et[2] + oz]

        has_size = any(abs(abs_to[i] - abs_from[i]) > 1e-6 for i in range(3))
        faces = el.get("faces", {})
        has_visible_face = any(f.get("enabled", True) for f in faces.values())

        if has_size and has_visible_face:
            box = copy.deepcopy(el)
            box.pop("children", None)
            box["from"] = abs_from
            box["to"] = abs_to
            flat.append(box)

        if "children" in el:
            flat.extend(flatten_elements(el["children"], tuple(abs_from)))
    return flat


def match_vs_pattern(code: str, pattern: str) -> bool:
    """Match Vintage Story wildcard or @regex keys."""
    if pattern.startswith("@"):
        return re.fullmatch(pattern[1:], code) is not None
    return fnmatch.fnmatchcase(code, pattern)


def resolve_by_type(data: dict, base_key: str, item_code: str):
    """Match Vintage Story *ByType wildcards top-to-bottom, falling back to base_key."""
    by_type = data.get(f"{base_key}ByType")
    if isinstance(by_type, dict):
        for pattern, val in by_type.items():
            if match_vs_pattern(item_code, pattern):
                return val
    return data.get(base_key)


def add_game_domain(asset_path: str) -> str:
    return asset_path if ":" in asset_path else f"game:{asset_path}"


def extract_textures_dict(tex_entry, placeholder_sub: tuple[str, str] | None = None) -> dict:
    """Normalize a VS textures block into {key: 'game:path'}."""
    out = {}
    if not isinstance(tex_entry, dict):
        return out
    for k, v in tex_entry.items():
        base = v.get("base") if isinstance(v, dict) else v
        if isinstance(base, str):
            if placeholder_sub:
                base = base.replace(placeholder_sub[0], placeholder_sub[1])
            out[k] = add_game_domain(base)
    return out


def find_standalone_item_path(item_name: str) -> Path:
    """Locate a standalone vanilla item JSON (e.g. clearquartz.json, amethyst.json)."""
    preferred = [
        VS_ASSETS_DIR / "itemtypes/resource/quartz" / f"{item_name}.json",
        VS_ASSETS_DIR / "itemtypes/resource" / f"{item_name}.json",
    ]
    for p in preferred:
        if p.exists():
            return p

    matches = list((VS_ASSETS_DIR / "itemtypes").rglob(f"{item_name}.json"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Could not find vanilla item JSON for '{item_name}' under {VS_ASSETS_DIR / 'itemtypes'}")


def resolve_ungraded_ore_assets(ore_ungraded_data: dict, ore_name: str):
    """Resolve the vanilla shape path and any item-level texture overrides for ore-{ore_name}."""
    item_code = f"ore-{ore_name}"

    shape_entry = resolve_by_type(ore_ungraded_data, "shape", item_code)
    if isinstance(shape_entry, dict) and "base" in shape_entry:
        shape_rel = shape_entry["base"].replace("{ore}", ore_name).removeprefix("game:")
    else:
        shape_rel = f"item/ore/ungraded/{ore_name}"

    shape_path = VS_ASSETS_DIR / "shapes" / f"{shape_rel}.json"
    if not shape_path.exists():
        raise FileNotFoundError(f"Could not find vanilla shape for '{ore_name}' at: {shape_path}")

    tex_entry = resolve_by_type(ore_ungraded_data, "textures", item_code)
    tex_overrides = extract_textures_dict(tex_entry, ("{ore}", ore_name))
    return shape_path, tex_overrides


def build_pile_shape(
    src_path: Path,
    out_shape_name: str,
    prefix: str,
    layout,
    forced_texture_key: str | None = None,
    texture_overrides: dict | None = None,
):
    """Generate a flattened pile shape from a vanilla item shape, auto-capping layers to fit 16 voxels."""
    src = load_vs_json(src_path)
    out_path = OUT_SHAPES_DIR / f"{out_shape_name}.json"

    flat_boxes = flatten_elements(src.get("elements", []))
    cuboids_per_piece = len(flat_boxes)

    has_pre_rot = any(
        b.get("rotationX", 0) or b.get("rotationY", 0) or b.get("rotationZ", 0)
        for b in flat_boxes
    )
    if has_pre_rot:
        print(f"  WARNING: {src_path.name} has pre-rotated sub-cuboids; inspect {out_shape_name} in-game.")

    min_x = min(min(b["from"][0], b["to"][0]) for b in flat_boxes)
    max_x = max(max(b["from"][0], b["to"][0]) for b in flat_boxes)
    min_y = min(min(b["from"][1], b["to"][1]) for b in flat_boxes)
    max_y = max(max(b["from"][1], b["to"][1]) for b in flat_boxes)
    min_z = min(min(b["from"][2], b["to"][2]) for b in flat_boxes)
    max_z = max(max(b["from"][2], b["to"][2]) for b in flat_boxes)

    center_x = (min_x + max_x) / 2.0
    center_z = (min_z + max_z) / 2.0
    base_y = min_y
    layer_height = round(max_y - min_y, 2)
    layers = max(1, min(4, int((16.0 + 1e-6) // layer_height))) if layer_height > 0 else 4
    capacity = layers * 16

    face_keys = sorted({
        face["texture"].lstrip("#")
        for box in flat_boxes
        for face in box.get("faces", {}).values()
        if face.get("enabled", True) and face.get("texture")
    })

    raw_textures = {
        k: add_game_domain(v)
        for k, v in src.get("textures", {}).items()
    }
    if texture_overrides:
        if "all" in texture_overrides:
            for k in set(raw_textures.keys()) | set(face_keys):
                raw_textures[k] = texture_overrides["all"]
        for k, v in texture_overrides.items():
            if k != "all":
                raw_textures[k] = v

    if forced_texture_key:
        first_tex = next(iter(raw_textures.values()))
        textures = {forced_texture_key: first_tex}
        key_map = None
    else:
        textures = {}
        key_map = {}
        for k, v in raw_textures.items():
            new_key = f"{prefix}_{k}"
            key_map[f"#{k}"] = f"#{new_key}"
            textures[new_key] = v

    elements = []
    for piece_idx, (layer, cx, cz, rot) in enumerate(layout[:capacity], start=1):
        y = round(layer * layer_height, 2)
        dx = cx - center_x
        dy = y - base_y
        dz = cz - center_z

        for i, box in enumerate(flat_boxes):
            clone = copy.deepcopy(box)
            clone["name"] = f"{prefix}{piece_idx}_{box.get('name', i)}"
            clone["from"] = [
                round(box["from"][0] + dx, 2),
                round(box["from"][1] + dy, 2),
                round(box["from"][2] + dz, 2),
            ]
            clone["to"] = [
                round(box["to"][0] + dx, 2),
                round(box["to"][1] + dy, 2),
                round(box["to"][2] + dz, 2),
            ]
            clone["rotationOrigin"] = [cx, y, cz]
            clone["rotationY"] = rot
            clone.pop("renderPass", None)

            for face in clone.get("faces", {}).values():
                if not face.get("enabled", True):
                    continue
                if forced_texture_key:
                    face["texture"] = f"#{forced_texture_key}"
                elif key_map and face.get("texture") in key_map:
                    face["texture"] = key_map[face["texture"]]

            elements.append(clone)

    shape_data = {
        "editor": {"allAngles": False, "singleTexture": False},
        "textureWidth": src.get("textureWidth", 16),
        "textureHeight": src.get("textureHeight", 16),
        "textures": textures,
        "elements": elements,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(shape_data, f, indent=2)

    print(
        f"Shape -> {out_path} "
        f"(src={src_path.name}, ratio={float(cuboids_per_piece)}, "
        f"layerHeight={layer_height}, layers={layers}, capacity={capacity}, textures={textures})"
    )
    return cuboids_per_piece, layer_height, capacity


def make_ground_storable_props(
    model_name: str,
    cuboids_per_piece: int,
    layer_height: float,
    stacking_textures: dict | None = None,
    stacking_capacity: int = 64,
):
    props = {
        "layout": "Stacking",
        "ctrlKey": False,
        "stackingModel": f"mineralpiles:item/{model_name}",
    }
    if stacking_textures:
        props["stackingTextures"] = stacking_textures

    props.update({
        "upSolid": False,
        "modelItemsToStackSizeRatio": float(cuboids_per_piece),
        "stackingCapacity": stacking_capacity,
        "transferQuantity": 1,
        "bulkTransferQuantity": 16,
        "collisionBox": {
            "x1": 0,
            "y1": 0,
            "z1": 0,
            "x2": 1,
            "y2": min(1.0, round(layer_height / 16.0, 4)),
            "z2": 1,
        },
        "cbScaleYByLayer": 0.0625,
    })
    return props


def merge_ground_storable_behaviors(existing_behaviors, props: dict):
    """Preserve any non-GroundStorable behaviors while replacing or adding GroundStorable."""
    merged = []
    replaced = False
    for b in existing_behaviors or []:
        if isinstance(b, dict) and b.get("name") == "GroundStorable":
            merged.append({"name": "GroundStorable", "properties": props})
            replaced = True
        else:
            merged.append(copy.deepcopy(b))
    if not replaced:
        merged.append({"name": "GroundStorable", "properties": props})
    return merged


def build_standalone_item_patch(vanilla: dict, item_rel_path: str, props: dict):
    """Emit the right JSON patch op depending on whether the item already has GroundStorable."""
    behaviors = vanilla.get("behaviors")
    file_ref = f"game:{item_rel_path}"

    if isinstance(behaviors, list):
        for idx, b in enumerate(behaviors):
            if isinstance(b, dict) and b.get("name") == "GroundStorable":
                return [{
                    "file": file_ref,
                    "op": "replace",
                    "path": f"/behaviors/{idx}/properties",
                    "value": props,
                }]
        return [{
            "file": file_ref,
            "op": "add",
            "path": "/behaviors/-",
            "value": {"name": "GroundStorable", "properties": props},
        }]

    return [{
        "file": file_ref,
        "op": "add",
        "path": "/behaviors",
        "value": [{"name": "GroundStorable", "properties": props}],
    }]


def main():
    seed = get_seed()
    print(f"Using seed: {seed}\n")

    cleanup_old_files()
    layout = build_shared_layout(seed)
    OUT_PATCHES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Shared quartz pile shape (reused by Clear, Normal, Rose, Smoky Quartz, and Amethyst if it uses the quartz shape)
    quartz_src = VS_ASSETS_DIR / "shapes/item/ore/ungraded/quartz.json"
    q_cuboids, q_height, q_cap = build_pile_shape(
        quartz_src, "quartz-pile", "q", layout, forced_texture_key="quartz"
    )

    # 2. Standalone crystal items (clearquartz, rosequartz, smokyquartz, amethyst)
    quartz_family_shapes = {"quartz", "clearquartz"}
    for item_name in STANDALONE_CRYSTALS:
        item_path = find_standalone_item_path(item_name)
        item_rel = item_path.relative_to(VS_ASSETS_DIR).as_posix()
        vanilla = load_vs_json(item_path)

        shape_entry = vanilla.get("shape")
        shape_base = (
            shape_entry.get("base", "").removeprefix("game:")
            if isinstance(shape_entry, dict)
            else ""
        )
        shape_path = VS_ASSETS_DIR / "shapes" / f"{shape_base}.json" if shape_base else quartz_src
        tex_overrides = extract_textures_dict(vanilla.get("textures"))

        if shape_path.stem in quartz_family_shapes:
            if tex_overrides:
                tex_path = next(iter(tex_overrides.values()))
            else:
                shape_json = load_vs_json(shape_path)
                tex_path = add_game_domain(next(iter(shape_json.get("textures", {}).values())))

            props = make_ground_storable_props(
                "quartz-pile",
                q_cuboids,
                q_height,
                stacking_textures={"quartz": tex_path},
                stacking_capacity=q_cap,
            )
            note = f"shared quartz-pile, tex={tex_path}"
        else:
            cuboids, height, cap = build_pile_shape(
                shape_path, f"{item_name}-pile", item_name, layout, texture_overrides=tex_overrides
            )
            props = make_ground_storable_props(
                f"{item_name}-pile", cuboids, height, stacking_capacity=cap
            )
            note = f"own shape {item_name}-pile"

        patch_data = build_standalone_item_patch(vanilla, item_rel, props)
        patch_path = OUT_PATCHES_DIR / f"{item_name}-pile.json"
        with patch_path.open("w", encoding="utf-8") as f:
            json.dump(patch_data, f, indent=2)
        print(f"Patch -> {patch_path} ({patch_data[0]['op']} {patch_data[0]['path']}; {note})")

    # 3. Consolidated ore-ungraded.json patch (Normal Quartz + all UNGRADED_ORES)
    ore_ungraded_file = "itemtypes/resource/ore-ungraded.json"
    ore_ungraded_data = load_vs_json(VS_ASSETS_DIR / ore_ungraded_file)
    behaviors_by_type = ore_ungraded_data.get("behaviorsByType", {})
    existing_keys = set(behaviors_by_type.keys())

    ore_patches = [
        {
            "file": f"game:{ore_ungraded_file}",
            "op": "move",
            "fromPath": "/behaviorsByType/*",
            "path": "/temp_mineralpiles_wildcard",
        },
        # Normal Quartz reuses quartz-pile.json with normal quartz texture
        {
            "file": f"game:{ore_ungraded_file}",
            "op": "replace" if "*-quartz" in existing_keys else "add",
            "path": "/behaviorsByType/*-quartz",
            "value": merge_ground_storable_behaviors(
                resolve_by_type(ore_ungraded_data, "behaviors", "ore-quartz"),
                make_ground_storable_props(
                    "quartz-pile",
                    q_cuboids,
                    q_height,
                    stacking_textures={"quartz": "game:item/resource/ungraded/quartz"},
                    stacking_capacity=q_cap,
                ),
            ),
        },
    ]

    for ore in UNGRADED_ORES:
        src_shape, tex_overrides = resolve_ungraded_ore_assets(ore_ungraded_data, ore)
        cuboids, height, cap = build_pile_shape(
            src_shape,
            f"{ore}-pile",
            ore,
            layout,
            texture_overrides=tex_overrides,
        )
        key = f"*-{ore}"
        existing_behaviors = resolve_by_type(ore_ungraded_data, "behaviors", f"ore-{ore}")
        ore_patches.append({
            "file": f"game:{ore_ungraded_file}",
            "op": "replace" if key in existing_keys else "add",
            "path": f"/behaviorsByType/{key}",
            "value": merge_ground_storable_behaviors(
                existing_behaviors,
                make_ground_storable_props(
                    f"{ore}-pile", cuboids, height, stacking_capacity=cap
                ),
            ),
        })

    ore_patches.append({
        "file": f"game:{ore_ungraded_file}",
        "op": "move",
        "fromPath": "/temp_mineralpiles_wildcard",
        "path": "/behaviorsByType/*",
    })

    ore_patch_path = OUT_PATCHES_DIR / "ore-ungraded-piles.json"
    with ore_patch_path.open("w", encoding="utf-8") as f:
        json.dump(ore_patches, f, indent=2)
    print(f"Patch -> {ore_patch_path} (quartz + {len(UNGRADED_ORES)} ores)")

    print(f"\nDone! Re-run anytime with: py {Path(sys.argv[0]).name} {seed}")


if __name__ == "__main__":
    main()