from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app.batch_service import BatchSession, _safe_zip_images
from app.config import MAX_BATCH_IMAGES
from app.filename_parser import parse_filename, safe_name
from app.image_processing import apply_color_stage, auto_detect_document, classify_layout, detect_routing_layout
from app.image_renderer import default_transform_quad, normalize_transform_quad, place_artwork, render_item
from app.models import BatchItem, Box
from app.settings_store import load_settings, save_settings
from app.template_store import (
    create_custom_template, get_active_template, get_active_template_keys,
    invalidate_cache, load_templates, save_record, set_active_template,
)
import app.settings_store as settings_store_module
import app.template_store as template_store_module
from app.ui.generator_page import GenerateWorker


def png_bytes(size=(320, 240), color=(240, 240, 240, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class FilenameTests(unittest.TestCase):
    def test_supported_filename_forms(self):
        self.assertEqual(parse_filename("KC-12__Nimal__Colombo.png")[:3], ("KC-12", "Nimal", "Colombo"))
        self.assertEqual(parse_filename("Nimal__Galle.tif")[:3], ("", "Nimal", "Galle"))

    def test_windows_safe_output_names(self):
        self.assertEqual(safe_name("CON"), "_CON")
        self.assertEqual(safe_name('A<B>:C?. '), "A_B_C_")
        self.assertLessEqual(len(safe_name("x" * 500)), 100)


class AutoV4Tests(unittest.TestCase):
    def test_basic_layout_thresholds(self):
        self.assertEqual(classify_layout(1000, 1000), "Square")
        self.assertEqual(classify_layout(700, 1000), "Portrait")
        self.assertEqual(classify_layout(1200, 700), "Landscape")

    def test_rotating_artwork_does_not_change_auto_template(self):
        item = BatchItem(
            source_path=Path("portrait.png"), filename="portrait.png",
            submission_id="1", child_name="Test", city="Colombo",
            detected_layout="Portrait", artwork_rotation=90,
        )
        self.assertEqual(item.selected_layout, "Portrait")
        item.artwork_rotation = 270
        self.assertEqual(item.selected_layout, "Portrait")

    def test_square_capture_with_portrait_page_routes_portrait(self):
        image = Image.new("RGB", (800, 800), (85, 100, 115))
        draw = ImageDraw.Draw(image)
        draw.rectangle((210, 90, 590, 710), fill=(248, 248, 244), outline=(20, 20, 20), width=5)
        layout, info = detect_routing_layout(image)
        self.assertEqual(layout, "Portrait")
        self.assertEqual(info["version"], "auto-v4")
        self.assertGreaterEqual(info["confidence"], 0.72)

    def test_plain_square_stays_square(self):
        layout, info = detect_routing_layout(Image.new("RGB", (500, 500), "white"))
        self.assertEqual(layout, "Square")
        self.assertIn("confidence", info)

    def test_blank_paper_margins_are_removed_without_stretching(self):
        image = Image.new("RGB", (900, 1200), (244, 241, 232))
        draw = ImageDraw.Draw(image)
        draw.rectangle((250, 330, 650, 800), fill=(235, 80, 45), outline=(25, 25, 25), width=12)
        cleaned, info = auto_detect_document(image)
        self.assertTrue(info.get("content_crop"))
        self.assertLess(cleaned.width * cleaned.height, image.width * image.height * 0.75)
        self.assertEqual(info.get("content_layout"), "Square")

    def test_dark_table_is_removed_around_light_sheet(self):
        image = Image.new("RGB", (1200, 900), (25, 30, 35))
        draw = ImageDraw.Draw(image)
        draw.rectangle((160, 120, 1040, 780), fill=(246, 243, 235))
        draw.ellipse((390, 260, 810, 650), fill=(40, 150, 210), outline=(15, 45, 65), width=10)
        cleaned, info = auto_detect_document(image)
        self.assertTrue(info.get("page_crop") or info.get("content_crop"))
        self.assertLess(cleaned.width * cleaned.height, image.width * image.height * 0.85)

    def test_colourful_full_artwork_is_not_mistaken_for_light_sheet(self):
        image = Image.new("RGB", (900, 1200), (230, 125, 55))
        draw = ImageDraw.Draw(image)
        draw.rectangle((90, 170, 810, 1130), fill=(55, 125, 205))
        draw.ellipse((260, 330, 640, 850), fill=(235, 205, 70), outline=(25, 25, 25), width=10)
        cleaned, info = auto_detect_document(image)
        self.assertFalse(info.get("page_source") == "light-sheet")


class ZipSecurityTests(unittest.TestCase):
    def test_path_traversal_is_rejected_before_extraction(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../escape.png", png_bytes())
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            with self.assertRaisesRegex(ValueError, "Unsafe path"):
                _safe_zip_images(archive, MAX_BATCH_IMAGES)

    def test_batch_limit_is_checked_before_extraction(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for index in range(MAX_BATCH_IMAGES + 1):
                archive.writestr(f"ID{index}__Child__City.png", b"")
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            with self.assertRaisesRegex(ValueError, "Maximum 250"):
                _safe_zip_images(archive, MAX_BATCH_IMAGES)

    def test_valid_zip_loads_transactionally(self):
        with tempfile.TemporaryDirectory() as folder:
            archive_path = Path(folder) / "batch.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("class-a/ID7__Ama__Kandy.png", png_bytes((420, 300)))
            session = BatchSession()
            try:
                items = session.add_zip(archive_path)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].source_relative, "class-a/ID7__Ama__Kandy.png")
                self.assertEqual(items[0].submission_id, "ID7")
            finally:
                session.close()


class TransparencyTests(unittest.TestCase):
    VIDEO_POINTS = {"Square": (1240, 610)}

    def test_square_master_keeps_transparent_video_opening(self):
        with tempfile.TemporaryDirectory() as folder:
            artwork = Path(folder) / "TEST1__CHILD__CITY.png"
            Image.new("RGB", (900, 700), (220, 50, 80)).save(artwork)
            for layout, point in self.VIDEO_POINTS.items():
                item = BatchItem(artwork, artwork.name, "TEST1", "CHILD", "CITY", layout, template_mode=layout, auto_paper_detect=False)
                output = render_item(item)
                record = get_active_template(layout)
                art_point = (record.artwork_box.x + record.artwork_box.width // 2, record.artwork_box.y + record.artwork_box.height // 2)
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getpixel(point)[3], 0, layout)
                self.assertEqual(output.getpixel(art_point)[3], 255, layout)

    def test_new_portrait_landscape_frames_cover_artwork_and_keep_text_card_opaque(self):
        with tempfile.TemporaryDirectory() as folder:
            artwork = Path(folder) / "TEST1__MADUSHA METHMINI__MEDAWACHCHIYA.png"
            Image.new("RGB", (900, 700), (220, 50, 80)).save(artwork)
            for layout in ("Portrait", "Landscape"):
                item = BatchItem(
                    artwork, artwork.name, "TEST1", "MADUSHA METHMINI", "MEDAWACHCHIYA",
                    layout, template_mode=layout, auto_paper_detect=False,
                )
                output = render_item(item)
                record = get_active_template(layout)
                art_center = (
                    record.artwork_box.x + record.artwork_box.width // 2,
                    record.artwork_box.y + record.artwork_box.height // 2,
                )
                self.assertEqual(output.getpixel(art_center)[3], 255, layout)
                self.assertEqual(output.getpixel((1340, 610))[3], 255, layout)

    def test_smart_fills_frame_without_outside_space(self):
        base = Image.new("RGBA", (260, 160), (0, 0, 0, 0))
        artwork = Image.new("RGBA", (80, 180), (220, 40, 70, 255))
        box = Box(30, 20, 200, 120)
        place_artwork(base, artwork, box, "Smart")
        for point in ((30, 20), (229, 20), (30, 139), (229, 139), (130, 80)):
            self.assertEqual(base.getpixel(point), (220, 40, 70, 255))

    def test_smart_clamps_zoom_out_and_pan_to_frame_edges(self):
        base = Image.new("RGBA", (220, 140), (0, 0, 0, 0))
        artwork = Image.new("RGBA", (90, 180), (25, 145, 210, 255))
        box = Box(10, 10, 200, 120)
        place_artwork(base, artwork, box, "Smart", zoom=0.25, offset_x=9999, offset_y=-9999)
        for point in ((10, 10), (209, 10), (10, 129), (209, 129)):
            self.assertEqual(base.getpixel(point), (25, 145, 210, 255))

    def test_default_free_transform_matches_smart_exact_frame_geometry(self):
        quad = default_transform_quad((100, 200), Box(0, 0, 300, 120), "Smart")
        self.assertAlmostEqual(quad[0][0], 0.0, places=4)
        self.assertAlmostEqual(quad[1][0], 1.0, places=4)
        self.assertAlmostEqual(quad[0][1], 0.0, places=4)
        self.assertAlmostEqual(quad[2][1], 1.0, places=4)

    def test_smart_preserves_all_four_source_corners_by_stretching(self):
        artwork = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
        draw = ImageDraw.Draw(artwork)
        draw.rectangle((0, 0, 49, 99), fill=(230, 30, 30, 255))
        draw.rectangle((50, 0, 99, 99), fill=(20, 180, 50, 255))
        draw.rectangle((0, 100, 49, 199), fill=(30, 70, 220, 255))
        draw.rectangle((50, 100, 99, 199), fill=(235, 190, 20, 255))
        base = Image.new("RGBA", (320, 160), (0, 0, 0, 0))
        place_artwork(base, artwork, Box(10, 10, 300, 120), "Smart")
        self.assertGreater(base.getpixel((15, 15))[0], 200)
        self.assertGreater(base.getpixel((305, 15))[1], 140)
        self.assertGreater(base.getpixel((15, 125))[2], 180)
        self.assertGreater(base.getpixel((305, 125))[0], 200)

    def test_free_transform_horizontal_stretch_and_frame_clip(self):
        base = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
        artwork = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(artwork)
        draw.rectangle((0, 0, 49, 99), fill=(230, 30, 30, 255))
        draw.rectangle((50, 0, 99, 99), fill=(20, 80, 230, 255))
        box = Box(20, 20, 200, 120)
        quad = ((-0.25, 0.0), (1.0, 0.0), (1.0, 1.0), (-0.25, 1.0))
        place_artwork(base, artwork, box, "Smart", transform_quad=quad)
        self.assertEqual(base.getpixel((20, 80))[3], 255)
        self.assertEqual(base.getpixel((219, 80))[3], 255)
        self.assertEqual(base.getpixel((19, 80))[3], 0)
        self.assertGreater(base.getpixel((45, 80))[0], base.getpixel((45, 80))[2])
        self.assertGreater(base.getpixel((190, 80))[2], base.getpixel((190, 80))[0])

    def test_free_transform_rejects_collapsed_or_unsafe_quad(self):
        self.assertIsNone(normalize_transform_quad(((0, 0), (0, 0), (0, 0), (0, 0))))
        self.assertIsNone(normalize_transform_quad(((99, 0), (1, 0), (1, 1), (0, 1))))

    def test_png_preserves_alpha_and_tiff_flattens_to_white(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = Image.new("RGBA", (40, 40), (10, 20, 30, 255))
            source.putpixel((20, 20), (0, 0, 0, 0))
            png = GenerateWorker.save_output(source, root / "out.any", "PNG")
            tif = GenerateWorker.save_output(source, root / "out.any", "TIFF")
            with Image.open(png) as result:
                self.assertEqual(result.convert("RGBA").getpixel((20, 20))[3], 0)
            with Image.open(tif) as result:
                self.assertEqual(result.mode, "RGB")
                self.assertEqual(result.getpixel((20, 20)), (255, 255, 255))

    def test_color_balance_and_hue_saturation_are_applied(self):
        source = Image.new("RGBA", (2, 2), (100, 80, 60, 128))
        balanced = apply_color_stage(source, color_balance_blue=50)
        self.assertEqual(balanced.getpixel((0, 0)), (100, 80, 110, 128))
        saturated = apply_color_stage(source, saturation=-100)
        r, g, b, a = saturated.getpixel((0, 0))
        self.assertEqual((r, g, b), (g, b, r))
        self.assertEqual(a, 128)


class IndividualImportTests(unittest.TestCase):
    def test_individual_images_can_be_added_with_plain_filenames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "my drawing.jpg"
            Image.new("RGB", (320, 240), (120, 180, 220)).save(source)
            session = BatchSession()
            try:
                added = session.add_images([source])
                self.assertEqual(len(added), 1)
                self.assertEqual(added[0].status, "Ready")
                self.assertEqual(added[0].child_name, "my drawing")
                self.assertTrue(added[0].source_path.exists())
            finally:
                session.close()


class PersistenceTests(unittest.TestCase):
    def test_atomic_settings_keep_active_templates_and_export_format(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / "settings.json"
            with patch("app.settings_store.SETTINGS_FILE", settings_path):
                data = load_settings()
                data.update({
                    "active_portrait_template": "p-new",
                    "active_landscape_template": "l-new",
                    "active_square_template": "s-new",
                    "export_format": "TIFF",
                })
                save_settings(data)
                loaded = load_settings()
                self.assertEqual(loaded["export_format"], "TIFF")
                self.assertEqual(loaded["active_portrait_template"], "p-new")
                self.assertFalse(settings_path.with_name("settings.json.tmp").exists())

    def test_exactly_one_active_default_per_layout(self):
        active = get_active_template_keys()
        self.assertEqual(set(active), {"Portrait", "Landscape", "Square"})
        for layout, key in active.items():
            self.assertEqual(get_active_template(layout).key, key)

    def test_custom_radio_selection_and_locked_edits_survive_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            templates = root / "templates"
            defaults = templates / "defaults"
            custom = templates / "custom"
            custom.mkdir(parents=True)
            for layout in ("Portrait", "Landscape", "Square"):
                target = defaults / layout.lower()
                target.mkdir(parents=True)
                Image.new("RGBA", (800, 500), (255, 255, 255, 255)).save(target / "template.png")
                (target / "template.json").write_text(json.dumps({
                    "id": layout.lower(), "name": f"{layout} Master", "type": layout,
                    "group": "Defaults", "template_image": "template.png",
                    "artwork_box": {"x": 20, "y": 20, "width": 500, "height": 300},
                    "name_box": {"x": 20, "y": 350, "width": 500, "height": 50},
                    "city_box": {"x": 20, "y": 410, "width": 500, "height": 40},
                    "is_default": True, "locked": True,
                }), encoding="utf-8")
            groups = templates / "groups.json"
            groups.write_text('{"groups":[{"name":"Defaults","color":"Blue"}]}', encoding="utf-8")
            settings = root / "app_data" / "settings.json"
            source = root / "new_portrait.tif"
            Image.new("RGBA", (800, 500), (20, 80, 160, 0)).save(source, "TIFF")

            patches = (
                patch.object(template_store_module, "TEMPLATE_DIR", templates),
                patch.object(template_store_module, "DEFAULT_TEMPLATE_DIR", defaults, create=True),
                patch.object(template_store_module, "CUSTOM_TEMPLATE_DIR", custom),
                patch.object(template_store_module, "GROUPS_FILE", groups),
                patch.object(settings_store_module, "SETTINGS_FILE", settings),
            )
            try:
                for active_patch in patches:
                    active_patch.start()
                invalidate_cache()
                created = create_custom_template("Admin Portrait", "Portrait", source)
                set_active_template(created)
                invalidate_cache()
                self.assertEqual(get_active_template("Portrait").key, created.key)

                locked = next(record for record in load_templates() if record.key == "portrait")
                locked.name_font_size = 61
                save_record(locked)
                invalidate_cache()
                reloaded = next(record for record in load_templates() if record.key == "portrait")
                self.assertTrue(reloaded.locked)
                self.assertEqual(reloaded.name_font_size, 61)
            finally:
                invalidate_cache()
                for active_patch in reversed(patches):
                    active_patch.stop()


if __name__ == "__main__":
    unittest.main()
