import numpy as np
import yaml

from nightcut.layout import (box_from_answer, combine_boxes, content_mask, content_rects, model_size,
                             save_layout, snap_16_9, to_yaml)


def canvas_frames(n=6, w=256, h=252, seed=1):
    """Stacked OBS canvas like 2560x2520: gameplay on top (16:9, full width), camera below-left
    (16:9, 75 % width) with a dark, static, slightly noisy corner; the rest is pure black."""
    rng = np.random.default_rng(seed)
    frames = np.zeros((n, h, w), dtype=np.float32)
    gh, cw = 144, 192
    frames[:, :gh, :] = rng.uniform(30, 220, (n, gh, w))
    frames[:, gh:, :cw] = 120 + rng.normal(0, 1.0, (n, h - gh, cw))
    frames[:, gh + 40:, :30] = 18 + rng.normal(0, 1.0, (n, h - gh - 40, 30))  # dark corner of the room
    return frames


def test_canvas_regions_found_and_dark_cam_corner_counts_as_content():
    rects = content_rects(content_mask(canvas_frames()))
    assert len(rects) == 2
    game, cam = rects
    assert (game["x"], game["y"], game["w"]) == (0.0, 0.0, 1.0)
    assert abs(game["h"] - 144 / 252) < 0.01
    assert cam["x"] == 0.0 and abs(cam["w"] - 0.75) < 0.01 and abs(cam["y"] + cam["h"] - 1.0) < 0.01


def test_snap_16_9_fixes_shared_edge():
    rects = [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5724}, {"x": 0.0, "y": 0.5724, "w": 0.751, "h": 0.4276}]
    game, cam = snap_16_9(rects, 2560, 2520)
    assert abs(game["h"] - 1440 / 2520) < 1e-6
    assert abs(cam["y"] - 1440 / 2520) < 1e-6 and abs(cam["y"] + cam["h"] - 1.0) < 1e-6
    assert abs(cam["w"] - 0.75) < 1e-6


def test_snap_16_9_leaves_other_ratios_alone():
    rects = [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.6}]
    assert snap_16_9(rects, 1920, 1080) == rects


def test_single_picture_is_one_region():
    frames = np.random.default_rng(2).uniform(30, 220, (6, 90, 160)).astype(np.float32)
    rects = content_rects(content_mask(frames))
    assert len(rects) == 1 and rects[0]["w"] * rects[0]["h"] > 0.95


def test_model_size_multiple_of_28_about_one_megapixel():
    w, h = model_size(2560, 1440)
    assert w % 28 == 0 and h % 28 == 0
    assert 860_000 <= w * h <= 1_100_000


def test_box_from_answer():
    box = box_from_answer({"webcam": True, "bbox_2d": [1092, 598, 1290, 756]}, 1344, 756)
    assert abs(box["x"] - 0.8125) < 0.001 and abs(box["y"] + box["h"] - 1.0) < 0.001
    assert box_from_answer({"webcam": False}, 1344, 756) is None
    assert box_from_answer({"webcam": True, "bbox_2d": [0, 0, 1344, 756]}, 1344, 756) is None  # whole screen
    assert box_from_answer({"webcam": True, "bbox_2d": "corner"}, 1344, 756) is None


def test_combine_boxes_snaps_to_corner():
    boxes = [{"x": 0.81, "y": 0.79, "w": 0.15, "h": 0.21}, {"x": 0.80, "y": 0.78, "w": 0.18, "h": 0.22},
             {"x": 0.82, "y": 0.80, "w": 0.16, "h": 0.20}]
    r = combine_boxes(boxes)
    assert r["x"] + r["w"] == 1.0 and r["y"] + r["h"] == 1.0
    assert 0.77 < r["x"] < 0.80 and 0.75 < r["y"] < 0.78


def test_yaml_block_is_valid_config():
    layout = {"gameplay": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
              "facecam": {"x": 0.79, "y": 0.76, "w": 0.21, "h": 0.24}, "facecam_burned_in": True}
    data = yaml.safe_load(to_yaml(layout, "a.mkv"))
    assert data["layout"]["facecam"] == {"x": 0.79, "y": 0.76, "w": 0.21, "h": 0.24}
    assert data["layout"]["facecam_burned_in"] is True
    no_cam = yaml.safe_load(to_yaml({**layout, "facecam": None, "facecam_burned_in": False}))
    assert no_cam["layout"]["facecam"] is None


def test_save_layout_replaces_only_layout(tmp_path):
    cfg = tmp_path / "local.yaml"
    cfg.write_text("# my settings\nkillfeed:\n  player_names: [Me]\n\n"
                   "layout:\n  gameplay: { x: 0, y: 0, w: 1, h: 1 }\n  facecam: null\n\n"
                   "editor:\n  target_minutes: 10\n", encoding="utf-8")
    layout = {"gameplay": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
              "facecam": {"x": 0.0, "y": 0.8, "w": 0.2, "h": 0.2}, "facecam_burned_in": True}
    save_layout(cfg, to_yaml(layout, "a.mkv"))
    save_layout(cfg, to_yaml(layout, "a.mkv"))  # running twice must not stack blocks
    text = cfg.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert text.startswith("# my settings")
    assert text.count("\nlayout:") == 1 and text.count("# found by nightcut layout") == 1
    assert data["killfeed"]["player_names"] == ["Me"] and data["editor"]["target_minutes"] == 10
    assert data["layout"]["facecam"]["y"] == 0.8


def test_save_layout_creates_file(tmp_path):
    cfg = tmp_path / "config" / "local.yaml"
    layout = {"gameplay": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, "facecam": None, "facecam_burned_in": False}
    save_layout(cfg, to_yaml(layout))
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["layout"]["facecam"] is None


# ---------------------------------------------------------------- side by side, twins, auto detection

def side_by_side_frames(n=6, seed=3):
    """2560x1080-like canvas at 256x108: gameplay 192x108 left, camera 64x36 top right, rest black."""
    rng = np.random.default_rng(seed)
    frames = np.zeros((n, 108, 256), dtype=np.float32)
    frames[:, :, :192] = rng.uniform(30, 220, (n, 108, 192))
    frames[:, :36, 192:] = 120 + rng.normal(0, 1.0, (n, 36, 64))
    return frames


def test_camera_next_to_gameplay_top_right():
    from nightcut.layout import gameplay_first
    frames = side_by_side_frames()
    game, cam = gameplay_first(content_rects(content_mask(frames)), frames)
    assert (game["x"], game["y"], round(game["w"], 3), game["h"]) == (0.0, 0.0, 0.75, 1.0)
    assert round(cam["x"], 3) == 0.75 and cam["y"] == 0.0 and round(cam["h"], 3) == 0.333


def test_camera_left_of_gameplay():
    frames = side_by_side_frames()[:, :, ::-1].copy()  # mirrored: camera top left
    game, cam = content_rects(content_mask(frames))
    assert round(game["x"], 3) == 0.25 and game["w"] == 0.75 and cam["x"] == 0.0


def test_two_16_9_pictures_without_gap_are_split_and_moving_one_is_gameplay():
    from nightcut.layout import gameplay_first
    rng = np.random.default_rng(4)
    frames = np.zeros((6, 90, 320), dtype=np.float32)
    frames[:, :, :160] = 100 + rng.normal(0, 2.0, (6, 90, 160))   # camera left: same room in every sample
    frames[:, :, 160:] = rng.uniform(30, 220, (6, 90, 160))       # gameplay right: changes all the time
    rects = content_rects(content_mask(frames))
    assert len(rects) == 2
    game, cam = gameplay_first(rects, frames)
    assert game["x"] == 0.5 and cam["x"] == 0.0


def test_stacked_twins_are_split():
    from nightcut.layout import split_twins
    assert split_twins([(0, 0, 160, 180)]) == [(0, 0, 160, 90), (0, 90, 160, 180)]
    assert split_twins([(0, 0, 160, 90)]) == [(0, 0, 160, 90)]


def test_describe():
    from nightcut.layout import describe
    game = {"x": 0.0, "y": 0.0, "w": 0.75, "h": 1.0}
    assert "next to" in describe({"kind": "canvas", "gameplay": game, "facecam": {"x": 0.75, "y": 0.0, "w": 0.25, "h": 0.33}})
    stacked = {"kind": "canvas", "gameplay": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.57},
               "facecam": {"x": 0.0, "y": 0.57, "w": 0.75, "h": 0.43}}
    assert "below" in describe(stacked)
    assert "inside" in describe({"kind": "burned_in", "gameplay": game, "facecam": {"x": 0.8, "y": 0.8, "w": 0.2, "h": 0.2}})
    assert "no facecam" in describe({"kind": "no_cam", "gameplay": game, "facecam": None})


def test_saved_layout_turns_detection_off():
    layout = {"gameplay": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, "facecam": None, "facecam_burned_in": False}
    assert yaml.safe_load(to_yaml(layout))["layout"]["detect"] is False  # "off"


def test_own_regions_in_config_turn_detection_off(tmp_path):
    from nightcut.config import load_config
    assert load_config()["layout"]["detect"] == "auto"
    own = tmp_path / "own.yaml"
    own.write_text("layout:\n  facecam: null\n", encoding="utf-8")
    assert load_config(own)["layout"]["detect"] == "off"
    keep = tmp_path / "keep.yaml"
    keep.write_text("layout:\n  detect: auto\n  facecam: null\n", encoding="utf-8")
    assert load_config(keep)["layout"]["detect"] == "auto"
    other = tmp_path / "other.yaml"
    other.write_text("editor:\n  target_minutes: 5\n", encoding="utf-8")
    assert load_config(other)["layout"]["detect"] == "auto"
