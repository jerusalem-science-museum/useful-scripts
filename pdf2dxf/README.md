# pdf2dxf: convert a PDF to a DXF that shows only what's visible (What you see is what you get WYSIWYG)

Typical PDF-to-DXF converters export everything in the file, including content you never see on the page. **This tool exports only what is actually visible:**

| Problem with typical converters | What pdf2dxf does |
|---|---|
| Ignores **clip paths**, so patterns and hatching spill outside their shapes | Trims every shape and line to its clip region |
| Exports **hidden objects** (fully clipped or off the page) | Leaves them out |
| Ignores **dash patterns**, so dashed and dash-dot lines become one long solid line | Breaks lines into real dash segments |
| Drops or substitutes **live text** | Converts it to outlines from the font embedded in the PDF |

## Requirements

Python 3.8 or newer, then:

```
pip install pikepdf fonttools
```

## Usage

Convert:

```
python pdf2dxf.py input.pdf output.dxf
```

Example output:

```
stats {'hidden_fills': 42, 'hidden_strokes': 27, 'clipped_pieces': 14} entities per layer {'CUT': 9, 'FOLD': 59, 'PATTERN': 116, 'TEXT': 6}
```

- `hidden_fills` / `hidden_strokes`: invisible shapes and lines that were left out.
- `clipped_pieces`: shapes that were trimmed to their clip region.

## Output

- **Format:** DXF R12 (AC1009). Almost every CAD and cutting program can open it.
- **Units:** millimetres, at true scale (1 PDF point = 25.4/72 mm).
- **Entities:** `LINE` and `POLYLINE`. Curves are approximated with short straight segments (about 0.5 pt / 0.18 mm long).
- **Layers:**

| Layer | Contents | How it's chosen |
|---|---|---|
| `CUT` | Outline / cut lines | Lines 1.5 pt or thicker |
| `FOLD` | Fold and crease lines, including dashed ones | Thinner dark lines |
| `PATTERN` | Hatching, dot patterns | Light gray (under 50% black) |
| `TEXT` | Text and other dark filled shapes | Dark filled shapes |

Filled areas are written as closed outlines, because DXF R12 cannot store solid fills.

## Limitations

- Converts the **first page** only.
- Assumes clip shapes have **no inward corners** (rectangles, circles and similar). An L-shaped or star-shaped clip will give wrong results.
- Sorts objects into layers by line thickness and gray level, assuming CMYK colors. For other PDFs, adjust `layer_for()` in `pdf2dxf.py`.
- Text is converted only when its font is embedded as TrueType (`FontFile2`). Other text is skipped.
- Images, shadings and transparency are ignored.
