# Kids Champ V4.9 Auto V4 Code Map

## Start / main window
- `main.py`
- `app/ui/main_window.py`
  - top navigation
  - Compare / Generator / Template Settings
  - Light/Dark
  - footer

## Generator
- `app/ui/generator_page.py`
  - ZIP upload
  - selected output preview
  - async/background preview jobs
  - preview cache
  - Smart / Full / Fill controls
  - Clear All
  - Download All
- `app/preview_service.py`
  - shared two-thread preview pool
  - stale preview result protection
  - converts worker results to QImage for safe UI handoff

## Batch rows
- `app/widgets/batch_table.py`
  - row selection
  - edit/delete icons
  - click-outside auto-save
  - Enter save / Escape cancel
  - double-click -> Compare
  - single-row refresh to avoid rebuilding 250 rows

## Compare — per-image editing
- `app/ui/compare_page.py`
  - final/output + original/source views
  - Auto-first workflow
  - Move / Crop / Wrap / Fit / Rotate / Reset
  - brightness / contrast / ACV presets
  - async/debounced final preview
  - stale preview cancellation-by-request-id
  - Save / Apply All
- `app/widgets/crop_warp_view.py`
  - dedicated tool modes
  - Crop: 8 handles + outside dim region
  - Wrap: 4 independent corners
  - live UI-only handle movement
  - commit signals on mouse release

## Image processing / performance cache
- `app/image_processing.py`
  - EXIF-aware source loading
  - bounded auto paper detection
  - auto crop / perspective correction
  - manual Crop then manual Wrap
  - geometry cache keyed by image + edit state
  - ACV preset processing
  - brightness / contrast
  - fast initial layout detection
  - full-quality final path + bounded preview path

## Rendering
- `app/image_renderer.py`
  - locked template compositing
  - active template selection
  - auto template selection from processed-artwork orientation
  - Smart Fit / Fit Full / Fill Frame
  - artwork clip / zoom / pan / rotation
  - frame/plaster overlay above artwork
  - `render_item()` = full-quality Download
  - `render_item_preview()` = bounded/cached UI preview

## Batch ZIP / source paths
- `app/batch_service.py`
  - max 250 images
  - filename parsing
  - relative folder path preservation

## Template system
- `app/template_store.py`
- `app/ui/template_page.py`
- `app/widgets/template_asset_view.py`
- `app/widgets/template_editor.py`
  - locked defaults
  - persistent active Portrait/Landscape/Square selections
  - custom template selection/deletion

## Locked production assets
- `templates/defaults/portrait/master/`
- `templates/defaults/landscape/master/`
- `templates/defaults/square/master/`
- `assets/location_pin.png`

Do not redraw/recolor/rewrite the locked production master files.

## Models
- `app/models.py`
  - per-image Crop/Wrap/placement/color/preset overrides

## UI support
- `app/ui/theme.py`
- `app/ui/dialogs.py`
- `assets/icons/`


## V4.0 source-geometry split

- `app/ui/compare_page.py` — separates LEFT placement controls from RIGHT source Crop/Wrap controls.
- `app/widgets/crop_warp_view.py` — dedicated crop keep-box and four-corner source-wrap interaction.
- `app/image_processing.py` — conservative Safe Auto page/background detection; manual Crop -> manual Wrap source pipeline; target-aspect perspective mapping.
- `app/image_renderer.py` — chooses active template frame before manual Wrap so Wrap output can match the exact frame aspect ratio.

Processing order: `Original -> Safe Auto OR Manual Crop -> Manual Wrap -> ACV/Light -> Placement -> Locked Template`.

## V4.1 additions
- `app/widgets/crop_warp_view.py`: dedicated Crop, 4-corner Wrap, persistent editor-view zoom, and Stretch/Push-Pull stroke interaction.
- `app/image_processing.py`: conservative auto geometry, source rotation before manual geometry, neutral color stage, local Stretch/Push-Pull remap processing.
- `app/ui/compare_page.py`: direct-edit Compare workflow, Org Img toggle, Undo/Redo history, source-tool routing, synchronized rotation and source preview.
- `app/models.py`: per-image `stretch_strokes` state.

## V4.2 routing changes
- `app/image_processing.py`: `detect_routing_layout()` and `detect_routing_layout_path()` decide Portrait/Landscape/Square from source content without modifying it.
- `app/filename_parser.py`: batch import calls the new safe routing detector.
- `app/models.py`: `BatchItem.selected_layout` is the stable route; source rotation edits only the artwork and never changes the selected template.
- `app/image_renderer.py`: preprocessing never silently changes the selected template.
- `app/widgets/batch_table.py`: normal rows show `Auto -> <resolved type>`.
- `app/ui/compare_page.py`: displays the resolved Auto route next to template controls.

## V4.3 text / location pin changes
- `app/image_renderer.py`: safe-padded name/location text rendering; explicit alpha compositing for the supplied location-pin asset.
- `assets/location_pin.png`: transparent production asset derived from the user's supplied red-pin image.
- `templates/defaults/square/template.json`: tuned Square artwork/name/location geometry only; `template.png` remains the locked `sq.png` master.

## V4.4 Auto V2 / transparent export

- `app/image_processing.py`: confidence-scored raw/page/foreground routing and cached file analysis.
- `app/image_renderer.py`: foreground-aware Smart Fit and explicit render-cache invalidation.
- `app/ui/generator_page.py`: PNG/TIFF export selector, RGBA output and cooperative worker shutdown.
- `app/batch_service.py`: pre-extraction ZIP validation and transactional batch commit.
- `app/settings_store.py`: atomic settings writes; UI pages reload settings before updating one preference.
- `app/widgets/template_asset_view.py`: left-side active-template radio controls.
- `tests/test_core.py`: routing, ZIP, persistence, template and transparency regression coverage.

Final layer order: `Student Artwork -> Artwork Box -> Master Graphics -> Text/Pin`.
The right-side video opening is preserved as transparent alpha in PNG and TIFF output.

## V4.8 Auto V4 frame-aware automation

- `app/image_processing.py`: Square-aware content routing remains active; the light-sheet fallback now verifies light/low-chroma paper and visible separation from the outside background before cropping.
- `app/image_renderer.py`: Smart maps the complete cleaned source to the exact saved artwork box using independent X/Y scaling. Explicit Full Artwork and proportional Fill Frame remain available.
- `app/config.py`: application label is V4.9 Auto V4.
- `tests/test_core.py`: covers exact-frame Smart stretching, four-corner preservation, colourful-artwork crop safety, Square routing and transparent exports.

## V4.9 smooth in-frame editing

- `app/widgets/crop_warp_view.py`: a click inside the left artwork region selects it for Free Transform; handle movement remains UI-local.
- `app/ui/compare_page.py`: Enter saves, left/right transform surfaces share one artwork quad, preview work is debounced and bounded to screen resolution.
- `app/models.py`: artwork rotation no longer changes the active Auto template route.
- `tests/test_core.py`: locks template routing across 90/270-degree artwork rotations.
