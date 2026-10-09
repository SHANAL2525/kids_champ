# Kids Champ Artwork Generator V5.0 — Original Source First

> Release candidate. Run the automated and clean-PC checks before office deployment.

## V5.0 highlights

- Enter or keypad Enter saves the current Compare artwork adjustment immediately.
- Rotate Left/Right rotates only the artwork inside its current template; it never switches the Auto Portrait/Landscape template.
- Clicking the artwork inside the left final/template preview opens Photoshop-style Free Transform handles for move, scale, side stretch, corner transform and rotation.
- Transform handle motion stays lightweight while expensive preview rendering is bounded and debounced for a smoother UI.

## V4.8 automation foundation

- Auto V4 detects/straightens paper, removes blank paper/photo margins using border-colour and faint-stroke analysis, then routes from cleaned artwork geometry.
- Dark table/floor backgrounds and light sheets that touch image edges are detected by a second page fallback.
- The light-sheet fallback now requires real paper colour and outside-background separation, preventing bright parts of a drawing from being destructively cropped.
- Smart placement maps the complete cleaned artwork to the exact active frame with independent width/height scaling, matching the measured Photoshop Free Transform workflow without letterbox gaps or a second crop.
- Cleaned Square artwork routes to the admin-selected active Square template; Portrait and Landscape remain independent.
- Batch analysis and final generation use bounded parallel workers for practical 250-image operation.
- PNG export preserves RGBA transparency. TIFF export is flattened onto solid white and saved as RGB so empty fitted-image space never appears black in TIFF viewers or print software.
- The right-side video opening remains transparent in PNG for later use in a video editor.
- Portrait, Landscape and Square use the supplied corrected production masters.
- Template radio selectors sit on the left; exactly one active template is stored for each layout.
- Active templates, saved template edits, theme and export format persist independently across restarts.
- ZIPs are validated before extraction to block traversal, oversized entries and compression bombs.
- Batch loading and generation use cooperative cancellation and safe shutdown.

## Main goal

V3.9 keeps the V3.8 performance architecture and adds the third locked Square production template across Generator, Compare, Template Settings, active-template routing, filters and batch rows.

### Performance changes

- Generator preview renders in a background thread pool instead of blocking the UI.
- Compare final preview renders in the background and ignores stale results from old images/drag states.
- Crop and Wrap handle dragging is UI-only while the mouse moves. Heavy OpenCV/Pillow work runs after mouse release.
- Move/Zoom and tone sliders use debounced background preview updates instead of full rendering every few milliseconds.
- Source image decoding and Crop/Wrap geometry are cached.
- Auto paper detection runs on a bounded working copy, then full-quality rendering is preserved for Download All.
- Batch ZIP loading uses fast EXIF-aware width/height orientation first; heavy auto paper work is delayed until preview/render.
- Compare Save refreshes one row instead of rebuilding the whole 250-row table when possible.
- Preview cache sizes are bounded to avoid memory growth.

## Locked production templates

The supplied production masters remain unchanged:

- Portrait: large artwork frame is **white**; small right-side box remains **black**.
- Landscape: exact latest supplied production master remains unchanged.
- Square: exact supplied `sq.png` is included as the third locked production master.
- Locked Defaults cannot be deleted.
- Crop / Wrap / Auto / Preset / Brightness / Contrast / Fit / Zoom / Move apply only to the child artwork, never to the locked template graphics.

## Compare workflow

- `Compare | Generator | Template Settings | Light/Dark`
- Auto-first workflow.
- If Auto misses a difficult image, enable **Edit**.
- **Crop** = 8-handle keep-box; dark outside region is removed.
- **Wrap** = 4 independent perspective corners.
- Crop/Wrap state is synchronized between left Final Preview and right Original/Edit Source.
- Mouse-release commits the expensive preview update, which keeps dragging smooth.
- `Ctrl+T` activates Wrap.
- Inspect mode: Left/Right arrows navigate Previous/Next.
- Move edit mode: arrow keys nudge the artwork.
- 8 ACV presets remain included.

## Generator workflow

- Upload up to 250 artworks.
- Auto / Portrait / Landscape / Square template routing.
- Smart / Full Artwork / Fill Frame batch controls.
- Batch row edit auto-saves when clicking outside and returns to normal text style.
- Double-click a row to open that exact image in Compare.
- Download keeps upload folder structure and the supported original image extension.

## Required libraries

```powershell
py -m pip install -r requirements.txt
```

Main dependencies:

- PySide6-Essentials
- Pillow
- NumPy
- OpenCV

## Run and test

```powershell
py main.py
```

or double-click `run_windows.bat`.

Run automated regression tests:

```powershell
py -3.12 -m unittest discover -s tests -v
```

Read `CODE_MAP.md` before modifying the project.


## V4.0 Safe Source Editor

Main production rule in V4.0:

- **RIGHT side = uploaded student-image source editor.** Crop and Wrap work only on the student artwork source.
- **LEFT side = final locked-template placement.** Move / Zoom / Fit work on placement only.
- Crop never crops a template frame. The blue box keeps the inside of the uploaded image and removes only the outside.
- After Crop, Wrap starts with four points on the four corners of the newly cropped artwork.
- Manual Wrap maps those four source corners to the active Portrait / Landscape / Square artwork-frame aspect ratio, which removes leftover white frame-space without editing the locked template graphic.
- Safe Auto is conservative: if page/background confidence is low, it keeps the complete original artwork instead of risking cutting the drawing.
- The three locked production masters remain Portrait, Landscape, and Square and are not recreated/recoloured by the editor.

Performance design from V3.8/V3.9 remains: bounded preview processing, background preview workers, caching, and full-quality final export.

## V4.1 Direct Source Editor Test
Compare is now direct-edit: no per-image Edit button is required. The right panel is the Editable Source with Org Img, Crop, Wrap, Stretch/Push-Pull, Undo and Redo. The original upload is never overwritten. Neutral color no longer applies hidden autocontrast. See `V4_1_UPDATE_NOTES.txt` for the complete change list.

## V4.2 Auto Template Routing
Auto template choice is now a stable per-image decision. The source image is analyzed to identify the real artwork orientation without cropping it. Manual template selection always wins. Batch Preview displays the resolved Auto route for easier checking.

## V4.3 Correct Text / Pin / Square
- Uses the newly supplied red location-pin artwork as the production pin asset.
- Name and location rendering now uses safe inset boxes and auto-fit text so labels do not touch/cut at box edges.
- Square label-strip geometry was retuned for `sq.png` (artwork + name + location areas) while keeping the locked square master graphics unchanged.
- Portrait / Landscape / Square routing and all V4.2 direct source-edit features are preserved.
