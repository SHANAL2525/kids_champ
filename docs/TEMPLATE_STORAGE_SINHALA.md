# Kids Champ Template Storage — Sinhala Guide

## Default production templates

```text
templates/defaults/portrait/
templates/defaults/landscape/
```

එක් folder එකේ:

```text
template.json      coordinates + settings
template.png       actual clean production template
overlay.png        tape/plaster/frame layer, artwork hole transparent
thumbnail.png      Saved Templates card image
```

## Renderer order

```text
1. template.png
2. child artwork
3. overlay.png
4. child name
5. location pin + city
```

ඒ නිසා artwork එක tape/plaster/frame **යටින්** පේනවා.

## Current Portrait default

`templates/defaults/portrait/template.png`

Main artwork box:

```json
"artwork_box": {"x":339,"y":125,"width":430,"height":563}
```

Black frame එක overlay එකේ තියාගෙන artwork එක black frame එක ඇතුළට යනවා.

## Current Landscape default

`templates/defaults/landscape/template.png`

Main artwork box:

```json
"artwork_box": {"x":150,"y":136,"width":839,"height":544}
```

Original supplied TIFF එකේ old sample name/location clean කරලා production template එක save කරලා තියෙනවා.

## Locked/default template manually replace කරන්න

Safest method: **Template Settings → select template → Edit → Choose Image → adjust boxes → Save Changes**.

File side manually replace කරනවා නම්:

1. original folder backup කරන්න.
2. `template.png` replace කරන්න.
3. `template.json` artwork/name/location box values check කරන්න.
4. `overlay.png` එකත් new artwork box එකට match වෙන්න update කරන්න. UI එකෙන් Save කළොත් overlay automatically rebuild වෙනවා.
5. `thumbnail.png` update කරන්න.
6. app restart කරන්න.

Important fields:

```json
"type": "Portrait",
"group": "Defaults",
"locked": true,
"is_default": true,
"artwork_fit_mode": "Fill Frame"
```

Portrait + Landscape + Square defaults Generator Auto mode එකේ routing anchors. ඒවා delete කරන්න බැහැ.

## Custom templates

Custom templates automatically:

```text
templates/custom/<template-name>/
```

Groups metadata:

```text
templates/groups.json
```
