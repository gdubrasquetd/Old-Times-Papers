"""Tests de l'export DB d'annotation -> dataset YOLO. Hermétique (DB temp +
images minuscules générées, sorties dans tmp_path)."""
from __future__ import annotations
import pytest
import export_dataset as ed


# ─────────────────────────── _split (pur) ───────────────────────────────

def test_split_is_deterministic():
    assert ed._split("le_figaro_1930", 0.2, 42) == ed._split("le_figaro_1930", 0.2, 42)


def test_split_all_train_when_ratio_zero():
    assert all(ed._split(s, 0.0, 1) == "train" for s in ("a", "b", "c", "d", "e"))


def test_split_all_val_when_ratio_one():
    assert all(ed._split(s, 1.0, 1) == "val" for s in ("a", "b", "c", "d", "e"))


# ─────────────────────────── _label_map ─────────────────────────────────

def test_label_map_contiguous_indices(export_env):
    conn = export_env.db.get_conn()
    id_to_idx, names = ed._label_map(conn)
    conn.close()
    assert names == [l["name"] for l in export_env.db.list_labels()]
    assert sorted(id_to_idx.values()) == list(range(len(names)))


def test_label_map_exclude_keeps_indices_contiguous(export_env):
    conn = export_env.db.get_conn()
    id_to_idx, names = ed._label_map(conn, exclude={"autres"})
    conn.close()
    assert "autres" not in names
    assert sorted(id_to_idx.values()) == list(range(len(names)))


# ─────────────────────────── export() ───────────────────────────────────

def _read_label(out_dir, slug):
    for split in ("train", "val"):
        p = out_dir / "labels" / split / f"{slug}.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip().splitlines()
    return None


def test_export_normalizes_box_to_yolo(export_env):
    # image 1000x800, boîte titre (100,200)-(300,400)
    export_env.add("img1", [("titre", 100, 200, 300, 400)])
    r = export_env.ed.export(val_ratio=0.2, seed=1)
    lines = _read_label(export_env.tmp / "dataset", "img1")
    assert lines is not None and len(lines) == 1
    cls, cx, cy, bw, bh = lines[0].split()
    assert int(cls) == r["names"].index("titre")
    assert float(cx) == pytest.approx(0.2)      # (100+300)/2/1000
    assert float(cy) == pytest.approx(0.375)     # (200+400)/2/800
    assert float(bw) == pytest.approx(0.2)       # 200/1000
    assert float(bh) == pytest.approx(0.25)      # 200/800


def test_export_single_class_collapses_to_bloc(export_env):
    export_env.add("img1", [("titre", 10, 10, 50, 50), ("bloc de texte", 60, 60, 90, 90)])
    r = export_env.ed.export(single_class=True)
    assert r["names"] == ["bloc"]
    lines = _read_label(export_env.tmp / "dataset_blocs", "img1")
    assert all(l.split()[0] == "0" for l in lines)     # tout en classe 0


def test_export_exclude_drops_those_boxes(export_env):
    export_env.add("img1", [("titre", 10, 10, 50, 50), ("autres", 60, 60, 90, 90)])
    r = export_env.ed.export(exclude={"autres"})
    assert "autres" not in r["names"]
    lines = _read_label(export_env.tmp / "dataset", "img1")
    assert len(lines) == 1                              # la boîte 'autres' a été retirée


def test_export_skips_image_without_boxes(export_env):
    export_env.add("empty", [])                         # 'done' mais sans annotation
    r = export_env.ed.export()
    assert r["skipped_no_box"] == 1
    assert r["train"] + r["val"] == 0


def test_export_ignores_non_done_images(export_env):
    export_env.add("draft", [("titre", 10, 10, 50, 50)], status="in_progress")
    r = export_env.ed.export()
    assert r["train"] + r["val"] == 0


def test_export_clips_box_to_image_bounds(export_env):
    # boîte qui déborde en largeur (x1=1200 > 1000) -> clippée à 1000
    export_env.add("img1", [("titre", 900, 100, 1200, 300)], w=1000, h=800)
    lines = _read_label((export_env.ed.export()) and export_env.tmp / "dataset", "img1")
    cls, cx, cy, bw, bh = lines[0].split()
    # après clip : x de 900 à 1000 -> largeur 100/1000 = 0.1, centre (900+1000)/2/1000=0.95
    assert float(bw) == pytest.approx(0.1)
    assert float(cx) == pytest.approx(0.95)


def test_export_writes_data_yaml(export_env):
    export_env.add("img1", [("titre", 10, 10, 50, 50)])
    r = export_env.ed.export()
    yaml = (export_env.tmp / "dataset" / "data.yaml").read_text(encoding="utf-8")
    assert "names:" in yaml and "titre" in yaml
    assert r["data_yaml"].name == "data.yaml"
