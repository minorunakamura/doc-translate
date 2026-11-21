"""Reconcile owned Docling reading-order ranges with validated local proposals."""

import ctypes
import itertools
import re
import unicodedata
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO
from math import ceil, floor, isclose
from threading import Lock
from typing import Any

from doc_translate.pdf_correction import (
    CalloutBlock,
    CodeBlock,
    CorrectionEvidence,
    CorrectionUncertainty,
    FigureMember,
    HeadingBlock,
    ListBlock,
    PictureBlock,
    TableBlock,
    TextBlock,
    _DraftBox,
    _overlaps,
    _valid_span,
)


def _reading_order(raw, *, nested=False):
    pending = [
        (entry, None) for entry in reversed(raw.get("body", {}).get("children", []))
    ]
    seen = set()
    while pending:
        entry, container = pending.pop()
        ref = entry["$ref"]
        if ref in seen:
            raise ValueError("Non-unique extraction reading order")
        seen.add(ref)
        _, collection, index = ref.split("/")
        item = raw[collection][int(index)]
        if item["self_ref"] != ref:
            raise ValueError("Invalid extraction child ref")
        yield ref, item, container
        if nested or collection == "groups":
            parent = ref if collection in ("pictures", "tables") else container
            pending.extend(
                (child, parent) for child in reversed(item.get("children", []))
            )


def preserve_owned_text(answer, raw):
    blocks = list(answer.blocks)
    issues = list(answer.uncertainties)
    by_ref = defaultdict(list)
    for block in blocks:
        for key in {(e.page, e.ref) for e in block.evidence}:
            by_ref[key].append(block)
    used = {b.block_id for b in blocks}
    rank = {}
    for order, (ref, node, _) in enumerate(_reading_order(raw)):
        if node.get("label") != "text" or node.get("content_layer", "body") != "body":
            continue
        text = node["text"]
        for prov in node["prov"]:
            page, span = prov["page_no"], prov.get("charspan")
            if page not in answer.owned_pages or not _valid_span(span, text):
                continue
            start, end = span
            original = text[start:end]
            if not original.strip():
                continue
            ev = CorrectionEvidence(
                page=page,
                ref=ref,
                charspan=(start, end),
                position=f"characters {start}:{end}",
                reason="Owned extraction range",
            )
            matches = by_ref[page, ref]
            if matches:
                for b in matches:
                    rank.setdefault(b.block_id, (page, order, start))
                if len(matches) != 1 or matches[0].kind != "body":
                    continue
                block = matches[0]
                if block.text == original:
                    continue
                reason = (
                    f"Extraction {original!r} differs from correction {block.text!r}"
                )
                blocks[blocks.index(block)] = block.model_copy(
                    update={"text": original}
                )
            else:
                key = f"extraction-{order}-{start}"
                while key in used:
                    key += "-"
                used.add(key)
                block = TextBlock(
                    kind="body", block_id=key, text=original, evidence=[ev]
                )
                blocks.append(block)
                rank[key] = (page, order, start)
                reason = "Readable owned range omitted by correction"
            issues.append(
                CorrectionUncertainty(
                    reason=reason,
                    target_ids=[block.block_id],
                    evidence=[ev],
                    origin="processing",
                )
            )
    blocks.sort(
        key=lambda b: rank.get(
            b.block_id, (min(e.page for e in b.evidence), float("inf"), 0)
        )
    )
    return answer.model_copy(update={"blocks": blocks, "uncertainties": issues})


# Only PDFium operations need serialization. The verified bytes and page scopes
# belong to this correction; they must never be shared between jobs.
_pdfium_lock = Lock()


def _mixed_phrase_matches(original, proposal):
    from doc_translate.units import _LITERAL, _NUMBER

    literals = []
    offset = 0
    for match in _LITERAL.finditer(original):
        if match.lastgroup == "number":
            literals.extend(
                (re.escape(original[offset : match.start()]), f"({_NUMBER})")
            )
            offset = match.end()
    literals.append(re.escape(original[offset:]))
    numbers = {m.span() for m in _LITERAL.finditer(proposal) if m.lastgroup == "number"}
    return [
        m
        for m in re.finditer(r"(?<!\w)" + "".join(literals) + r"(?!\w)", proposal)
        if all(span in numbers for span in m.regs[1:])
    ]


def _word_pair(original, proposed, printed_lines):
    if len(proposed) != len(original) + 1:
        return None
    index = next(
        (i for i, (a, b) in enumerate(zip(original, proposed)) if a != b), None
    )
    if (
        index is None
        or proposed[index] != " "
        or proposed[:index] + proposed[index + 1 :] != original
    ):
        return None
    # Isolate a plain prose token, not a component of an identifier or URL.
    left = re.search(
        r"(?:^|\s)([A-Za-z]+)$" if printed_lines else r"(?:^|\s)([a-z]+)$",
        proposed[:index],
    )
    right = re.match(
        r"([A-Za-z]+)(?:[,.!?;:]?(?:\s|$))"
        if printed_lines
        else r"([a-z]+)(?:[,.!?;:]?(?:\s|$))",
        proposed[index + 1 :],
    )
    if left and right:
        fused = left[1] + right[1]
        if len(re.findall(rf"(?<!\w){fused}(?!\w)", original)) == 1:
            return (left[1], right[1]), index
    return None


def _local_text(
    provenances, page, read_original, inspect, *, strict_pdf, verify_page=False
):
    import pypdfium2 as pdfium

    try:
        boxes = [_DraftBox.model_validate(prov.get("bbox")) for prov in provenances]
    except ValueError:
        return None
    # Verification errors are not missing text. Open exactly the buffer verified.
    data = read_original()
    with _pdfium_lock:
        try:
            with pdfium.PdfDocument(data) as pdf:
                if page.page > len(pdf):
                    if verify_page:
                        raise ValueError("Original PDF has no such page")
                    return None
                p = pdf[page.page - 1]
                width, height = p.get_size()
                if p.get_rotation() or p.get_bbox()[:2] != (0, 0):
                    return None
                if not isclose(width, page.width_points, abs_tol=0.01) or not isclose(
                    height, page.height_points, abs_tol=0.01
                ):
                    if verify_page:
                        raise ValueError("Inconsistent original page geometry")
                    return None
                tp = p.get_textpage()
                result = []
                for box in boxes:
                    b, t = (
                        (height - box.b, height - box.t)
                        if box.coord_origin == "TOPLEFT"
                        else (box.b, box.t)
                    )
                    if not (0 <= box.l < box.r <= width and 0 <= b < t <= height):
                        return None
                    bounds = box.l, b, box.r, t
                    value = inspect(tp, tp.get_text_bounded(*bounds), bounds)
                    if value is None:
                        return None
                    result.append(value)
                return result
        except pdfium.PdfiumError as error:
            if strict_pdf:
                raise ValueError("Unable to read original PDF") from error
            return None


def _paints_text(obj):
    import pypdfium2.raw as native

    # Fill/stroke (possibly also clipping) paint; invisible/clip-only do not.
    return bool(obj) and native.FPDFTextObj_GetTextRenderMode(obj) in (
        native.FPDF_TEXTRENDERMODE_FILL,
        native.FPDF_TEXTRENDERMODE_STROKE,
        native.FPDF_TEXTRENDERMODE_FILL_STROKE,
        native.FPDF_TEXTRENDERMODE_FILL_CLIP,
        native.FPDF_TEXTRENDERMODE_STROKE_CLIP,
        native.FPDF_TEXTRENDERMODE_FILL_STROKE_CLIP,
    )


def _word_draws(tp, phrase, bounds):
    """Read native geometry for the unique bounded occurrence, in page points."""
    import pypdfium2.raw as native

    left, bottom, right, top = bounds
    hits = []
    with closing(tp.search(phrase, match_case=True)) as search:
        while hit := search.get_next():
            index, length = hit
            if length != len(phrase) or tp.get_text_range(index, length) != phrase:
                continue
            rows = []
            for i in range(index, index + length):
                mat = native.FS_MATRIX()
                x, y = ctypes.c_double(), ctypes.c_double()
                generated = native.FPDFText_IsGenerated(tp, i)
                if generated == 1 and phrase[i - index] == " ":
                    rows.append(None)
                    continue
                l, b, r, t = tp.get_charbox(i, loose=True)
                if not (left < r and l < right and bottom < t and b < top):
                    break
                obj = native.FPDFText_GetTextObject(tp, i)
                if (
                    not _paints_text(obj)
                    or generated != 0
                    or not native.FPDFText_GetCharOrigin(tp, i, x, y)
                    or not native.FPDFText_GetMatrix(tp, i, mat)
                    or mat.b
                    or mat.c
                    or mat.a <= 0
                    or mat.d <= 0
                ):
                    break
                rows.append(
                    (
                        x.value,
                        y.value,
                        r,
                        ctypes.cast(obj, ctypes.c_void_p).value,
                        (
                            ctypes.cast(
                                native.FPDFTextObj_GetFont(obj), ctypes.c_void_p
                            ).value,
                            native.FPDFText_GetFontSize(tp, i),
                            mat.a,
                            mat.d,
                        ),
                    )
                )
            else:
                hits.append(rows)
    return hits[0] if len(hits) == 1 else None


def _printed_space(rows, offset):
    if not rows:
        return False
    drawn = [row for row in rows if row is not None]
    if not all(isclose(row[1], drawn[0][1], abs_tol=0.01) for row in drawn):
        return False
    if rows[offset] is not None:
        return True
    left, right = rows[offset - 1], rows[offset + 1]
    if left[3] == right[3] or not all(row[4] == left[4] for row in drawn):
        return False
    # Loose glyph boxes include the font's advance transformed to page space.
    # Compare residual tracking inside both words to the disputed boundary.
    jitter = [abs(b[0] - a[2]) for a, b in itertools.pairwise(rows) if a and b]
    em = left[4][1] * left[4][2]
    tolerance = max(0.02, 0.05 * em)
    return (
        offset > 1
        and offset + 2 < len(rows)
        and max(jitter) <= tolerance
        and right[0] - left[2] > max(jitter) + tolerance
    )


def _supported_space(
    original, proposed, prov, page, read_original, verify_context, printed_lines
):
    boundary = _word_pair(original, proposed, printed_lines)
    if boundary is None:
        return False
    pair, offset = boundary

    def inspect(tp, text, bounds):
        if printed_lines:
            return text, _word_draws(tp, " ".join(pair), bounds)
        if not verify_context:
            return text
        import pypdfium2.raw as native

        left, bottom, right, top = bounds
        phrase = " ".join(pair)
        with closing(tp.search(phrase, match_case=True)) as search:
            while hit := search.get_next():
                index, length = hit
                l, b, r, t = tp.get_charbox(index)
                if not (left < r and l < right and bottom < t and b < top):
                    continue
                if length != len(phrase) or tp.get_text_range(index, length) != phrase:
                    continue
                baselines = []
                for i, c in enumerate(phrase, index):
                    if c != " ":
                        x, y = ctypes.c_double(), ctypes.c_double()
                        if not native.FPDFText_GetCharOrigin(tp, i, x, y):
                            return None
                        baselines.append(y.value)
                if max(baselines) - min(baselines) < 0.01:
                    return text
        return None

    values = _local_text([prov], page, read_original, inspect, strict_pdf=printed_lines)
    if values is None:
        return False
    (text,) = values
    if printed_lines:
        text, rows = text
        if not _printed_space(rows, len(pair[0])):
            return False
    left, right = pair
    # A competing fused or line-broken occurrence is not a unique location.
    matches = re.findall(rf"(?<!\w){left}([ \r\n]*){right}(?!\w)", text)
    if verify_context:
        # PDFium marks a discretionary line-end hyphen with U+0002. Normalize
        # for context alignment only, never to produce adopted characters.
        def context(s):
            return " ".join(s.splitlines()).replace("\x02", "").replace("’", "'")

        if context(text) != context(proposed):
            return False
    if printed_lines:
        from doc_translate.units import _LITERAL

        if any(m.start() < offset < m.end() for m in _LITERAL.finditer(original)):
            return False
    return matches == [" "]


@dataclass
class _Portion:
    order: int
    node: dict
    prov: dict
    start: int
    end: int

    @property
    def rank(self):
        return self.prov["page_no"], self.order, self.start

    @property
    def text(self):
        return self.node["text"][self.start : self.end]

    def evidence(self):
        return CorrectionEvidence(
            page=self.prov["page_no"],
            ref=self.node["self_ref"],
            charspan=(self.start, self.end),
            position=f"characters {self.start}:{self.end}",
            reason="Owned extraction range",
        )


def _missing_block(part, key):
    fields: dict[str, Any] = {
        "block_id": key,
        "text": part.text,
        "evidence": [part.evidence()],
    }
    node = part.node
    match node["label"]:
        case "section_header" | "title":
            return HeadingBlock(kind="heading", level=node.get("level", 1), **fields)
        case "code":
            return CodeBlock(kind="code", **fields)
        case "list_item":
            return ListBlock(
                kind="list", depth=1, marker=node.get("marker", ""), **fields
            )
        case "text" | "paragraph":
            return TextBlock(kind="body", **fields)
        case kind:
            return TextBlock(kind=kind, **fields)


def _owned_separator(prev, part, *, code=True):
    if prev.rank[0] != part.rank[0] and (code or prev.node is not part.node):
        return None
    if prev.node is not part.node:
        return " "
    if prev.end > part.start:
        return None
    if any(
        prev.end < end and start < part.start
        for prov in part.node["prov"]
        for start, end in [prov["charspan"]]
        if prov is not part.prov and prov is not prev.prov
    ):
        return None
    gap = part.node["text"][prev.end : part.start]
    return gap if not gap.strip() else None


def _joined(parts):
    result = parts[0].text
    for prev, part in itertools.pairwise(parts):
        gap = " "
        if prev.node is part.node and prev.end <= part.start:
            between = part.node["text"][prev.end : part.start]
            if not between or between.isspace():
                gap = between
        result += gap + part.text
    return result


def _line_formatting(original, proposal):
    # A proposal may lay out existing tokens on lines. Existing newlines,
    # indentation, quoted literal spaces and every non-separator are immutable.
    source = re.split(r"(\s+)", original)
    target = re.split(r"(\s+)", proposal)
    if len(source) != len(target):
        return False
    quote = None
    for i, (a, b) in enumerate(zip(source, target, strict=True)):
        if a != b and not (
            i % 2
            and not quote
            and a.startswith(" ")
            and (re.fullmatch(r"\n+ *", b) or (a == " " and re.fullmatch(r" +", b)))
            and (a == " " or b.endswith(a[1:]))
        ):
            return False
        if not i % 2:
            for char in re.findall(r'\\.|["\'`]', a):
                if char == quote:
                    quote = None
                elif quote is None and len(char) == 1:
                    quote = char
    return True


def _layout_candidate(original, proposal):
    # Count blank lines separately after the nonempty lines have been located.
    return _line_formatting(
        re.sub(r"\n+", "\n", original), re.sub(r"\n+", "\n", proposal)
    )


def _printed_candidate(original, proposal, *, relative_margin=False):
    # This comparison only nominates candidates. The local glyph runs must
    # separately prove every proposed nonempty line and its indentation.
    source = list(re.finditer(r"(\s*)(\S|$)", original))
    target = re.findall(r"(\s*)(\S|$)", proposal)
    if len(source) != len(target):
        return False
    word_apostrophes = {m.start() for m in re.finditer(r"(?<=\w)'(?=\w)", original)}
    quotes: set[str | None] = {None}
    escaped = False
    for token, (b, other) in zip(source, target, strict=True):
        a, char = token.groups()
        if char != other:
            return False
        if (
            relative_margin
            and token.start() == 0
            and re.fullmatch(" *", a)
            and re.fullmatch(" *", b)
            and len(b) >= len(a)
        ):
            # The first printed line can itself start to the right of the fragment origin.
            pass
        elif a != b:
            if (a and quotes != {None}) or not char:
                return False
            if not (a == " " and re.fullmatch(" +", b)):
                if not re.fullmatch(r"\n+ *", b) or not re.fullmatch(r"\n* *", a):
                    return False
                # One separator space may represent the printed boundary;
                # never discard existing indentation or existing newlines.
                indent = a.lstrip("\n") if "\n" in a else a[1:]
                if not b.endswith(indent) or b.count("\n") < a.count("\n"):
                    return False
        if not escaped and char in ('"', "'", "`"):
            toggled = {None if q == char else q or char for q in quotes}
            # A word-internal apostrophe need not delimit a literal; keep both readings.
            quotes = quotes | toggled if token.start(2) in word_apostrophes else toggled
        escaped = char == "\\" and not escaped
    return True


def _line_origins(
    textpage, local, bounds, *, printed_lines, row_fonts=None, glyphs=None
):
    """Find the bounded text's unique native run, not an identical line elsewhere."""
    import pypdfium2.raw as native

    left, bottom, right, top = bounds
    lines = local.splitlines(keepends=True)
    if not lines or not all(line.rstrip("\r\n") for line in lines):
        return None
    first = lines[0].rstrip("\r\n")
    hits = []
    with closing(textpage.search(first, match_case=True)) as search:
        while hit := search.get_next():
            index, _ = hit
            l, b, r, t = textpage.get_charbox(index)
            if not (left < r and l < right and bottom < t and b < top):
                continue
            if index + len(local) <= textpage.count_chars() and all(
                native.FPDFText_GetUnicode(textpage, index + j) == ord(c)
                for j, c in enumerate(local)
            ):
                hits.append(index)
    if len(hits) != 1:
        return None
    index = hits[0]
    result = []
    for line in lines:
        content = line.rstrip("\r\n")
        positions = []
        generated_spaces = set()
        fonts = []
        for i in range(index, index + len(content)):
            x, y = ctypes.c_double(), ctypes.c_double()
            if (
                not native.FPDFText_GetCharOrigin(textpage, i, x, y)
                or native.FPDFText_GetCharAngle(textpage, i) != 0
            ):
                return None
            if printed_lines:
                generated = native.FPDFText_IsGenerated(textpage, i)
                if generated == 1:
                    # Generated separators only connect real painted text in this row.
                    offset = i - index
                    if not (
                        content[offset] == " "
                        and 0 < offset < len(content) - 1
                        and not content[offset - 1].isspace()
                        and not content[offset + 1].isspace()
                    ):
                        return None
                    if glyphs is not None:
                        # A generated separator alone is not evidence of a printed SPACE.
                        generated_spaces.add(offset)
                elif generated != 0 or not _paints_text(
                    native.FPDFText_GetTextObject(textpage, i)
                ):
                    return None
            if row_fonts is not None:
                obj = native.FPDFText_GetTextObject(textpage, i)
                fonts.append(
                    ctypes.cast(native.FPDFTextObj_GetFont(obj), ctypes.c_void_p).value
                    if obj
                    else None
                )
            if glyphs is not None and not content[i - index].isspace():
                glyphs.append(
                    (
                        i,
                        native.FPDFText_GetTextObject(textpage, i),
                        textpage.get_charbox(i),
                    )
                )
            positions.append((x.value, y.value))
        row = 0
        for end in range(1, len(positions) + 1):
            if end < len(positions) and isclose(
                positions[row][1], positions[end][1], abs_tol=0.01
            ):
                continue
            if not printed_lines and end < len(positions):
                return None
            if glyphs is not None:
                # A bounded substring cannot establish a row if its native row continues.
                for neighbor in (index + row - 1, index + end):
                    if (
                        not 0 <= neighbor < textpage.count_chars()
                        or chr(native.FPDFText_GetUnicode(textpage, neighbor)) in "\r\n"
                    ):
                        continue
                    nx, ny = ctypes.c_double(), ctypes.c_double()
                    if not native.FPDFText_GetCharOrigin(
                        textpage, neighbor, nx, ny
                    ) or isclose(ny.value, positions[row][1], abs_tol=0.01):
                        return None
            x, y = positions[row]
            steps = [b[0] - a[0] for a, b in itertools.pairwise(positions[row:end])]
            if generated_spaces.intersection(range(row, end)):
                pitch = next(
                    (
                        d
                        for j, d in enumerate(steps, row)
                        if j not in generated_spaces and j + 1 not in generated_spaces
                    ),
                    None,
                )
                if pitch is None or not all(
                    isclose(d, pitch, abs_tol=0.02) for d in steps
                ):
                    return None
            if printed_lines and any(step <= 0 for step in steps):
                return None
            result.append((content[row:end], x, y, steps))
            if row_fonts is not None:
                row_fonts.append(set(fonts[row:end]) - {None})
            row = end
        index += len(line)
    return result


def _page_ink(page, read_image):
    from PIL import Image

    sx, sy = page.scale_x, page.scale_y
    with Image.open(BytesIO(read_image(page))) as im:
        scale = page.rendering.get("render_scale", sx if sx == sy else None)
        if (
            im.size != (page.width_pixels, page.height_pixels)
            or not isinstance(scale, (int, float))
            or scale <= 0
            or any(
                not isclose(px / pt, axis)
                or not floor(pt * scale) <= px <= ceil(pt * scale)
                for px, pt, axis in (
                    (im.width, page.width_points, sx),
                    (im.height, page.height_points, sy),
                )
            )
        ):
            return None
        image = im.convert("L")

    return image


def _visible_glyphs(tp, bounds, glyphs, page, image):
    import pypdfium2 as pdfium
    import pypdfium2.raw as native
    from PIL import Image, ImageChops, ImageStat

    sx, sy = page.scale_x, page.scale_y

    # Rendering the verified buffer with and without the selected drawing
    # distinguishes visible glyphs from hidden, overpainted or clipped text.
    def paint():
        # Render straight onto the recorded integer canvas; do not resample the PNG.
        with closing(
            pdfium.PdfBitmap.new_native(*image.size, native.FPDFBitmap_BGR)
        ) as bitmap:
            bitmap.fill_rect((255, 255, 255, 255), 0, 0, *image.size)
            native.FPDF_RenderPageBitmap(bitmap, tp.page, 0, 0, *image.size, 0, 0)
            return bitmap.to_pil().convert("L")

    painted = paint()
    objects = {ctypes.cast(o, ctypes.c_void_p).value: o for _, o, _ in glyphs}

    def pixels(bounds):
        l, b, r, t = bounds
        return (
            floor(l * sx),
            floor((page.height_points - t) * sy),
            ceil(r * sx),
            ceil((page.height_points - b) * sy),
        )

    # Suppressing a text object also suppresses its other glyphs. Shared
    # pixels cannot witness which glyph supplied the missing ink.
    boxes = defaultdict(dict)
    for i in range(tp.count_chars()):
        obj = ctypes.cast(native.FPDFText_GetTextObject(tp, i), ctypes.c_void_p).value
        if obj in objects and not chr(native.FPDFText_GetUnicode(tp, i)).isspace():
            boxes[obj][i] = pixels(tp.get_charbox(i))

    active = None
    hidden = None
    for i, obj, (l, b, r, t) in glyphs:
        key = ctypes.cast(obj, ctypes.c_void_p).value
        if key != active:
            if not native.FPDFPageObj_SetIsActive(obj, False):
                return None
            hidden = paint()
            if not native.FPDFPageObj_SetIsActive(obj, True):
                return None
            active = key
        if not (
            bounds[0] <= (l + r) / 2 <= bounds[2]
            and bounds[1] <= (b + t) / 2 <= bounds[3]
        ):
            return False
        box = boxes[key][i]
        if box[0] >= box[2] or box[1] >= box[3]:
            return False
        # The immutable page image must also carry ink at this glyph.
        ink = image.crop(box)

        exclusive = Image.new("L", ink.size, 255)
        for j, (left, top, right, bottom) in boxes[key].items():
            if (
                i != j
                and left < box[2]
                and box[0] < right
                and top < box[3]
                and box[1] < bottom
            ):
                exclusive.paste(
                    0,
                    (left - box[0], top - box[1], right - box[0], bottom - box[1]),
                )

        def distance(render, ink=ink, box=box, mask=exclusive):
            return ImageStat.Stat(
                ImageChops.difference(ink, render.crop(box)), mask=mask
            ).sum[0]

        if distance(painted) >= distance(hidden):
            return False
    return True


def _page_box(prov, page):
    box = _DraftBox.model_validate(prov.get("bbox"))
    b, t = (
        (box.b, box.t)
        if box.coord_origin == "BOTTOMLEFT"
        else (page.height_points - box.b, page.height_points - box.t)
    )
    return box.l, b, box.r, t


def _body_punctuation(
    part, item, page, read_original, read_image, independent, items, issue
):
    """Resolve one-character punctuation edits against the entire owned occurrence."""
    import pypdfium2.raw as native

    from doc_translate.units import _literal_spans

    original = part.text
    changes = [
        op[1:]
        for op in SequenceMatcher(
            None, original, item.text, autojunk=False
        ).get_opcodes()
        if op[0] != "equal"
    ]
    edits = [(i, j, item.text[k:l]) for i, j, k, l in changes]
    punctuation = {
        i
        for i, j, new in edits
        if j - i <= 1
        and len(new) == 1
        and all(unicodedata.category(c).startswith("P") for c in original[i:j] + new)
    }
    if not punctuation:
        return None
    eligible = {
        i
        for i, j, k, l in changes
        if i in punctuation
        and 0 < i <= j < len(original)
        and original[i - 1] != item.text[k:l] != original[j]
        and not (original[i - 1].isdigit() and original[j].isdigit())
        and not any(
            a < end and start < b
            for text, start, end in ((original, i, j), (item.text, k, l))
            for a, b in _literal_spans(text)
        )
    }

    def inspect(tp, local, bounds):
        # Generated line wraps / discretionary hyphens establish context only.
        # Prefer the full provenance, never search for an isolated proposed glyph.
        local = local.replace("\x02", "").replace("\r\n", "\n")

        def pattern(text):
            return r"\n?".join("[ \\n]" if c == " " else re.escape(c) for c in text)

        pieces = []
        cursor = 0
        for i, j, new in edits:
            pieces.extend(
                [
                    pattern(original[cursor:i]),
                    "("
                    + (re.escape(new) + "|" if i in punctuation else "")
                    + re.escape(original[i:j])
                    + ")",
                ]
            )
            cursor = j
        pieces.append(pattern(original[cursor:]))
        match = re.fullmatch(r"\n?".join(pieces), local)
        try:
            for node in items.values():
                for other in node["prov"]:
                    if other is part.prov or other["page_no"] != page.page:
                        continue
                    l, b, r, t = _page_box(other, page)
                    if (
                        l < bounds[2]
                        and bounds[0] < r
                        and b < bounds[3]
                        and bounds[1] < t
                    ):
                        return {}
        except ValueError:
            return {}
        glyphs = []
        for n in range(tp.count_chars()):
            c = chr(native.FPDFText_GetUnicode(tp, n))
            if c.isspace() or c == "\x02":
                continue
            l, b, r, t = box = tp.get_charbox(n)
            if (
                bounds[0] <= (l + r) / 2 <= bounds[2]
                and bounds[1] <= (b + t) / 2 <= bounds[3]
            ):
                obj = native.FPDFText_GetTextObject(tp, n)
                if native.FPDFText_IsGenerated(tp, n) != 0 or not _paints_text(obj):
                    return {}
                glyphs.append((c, (n, obj, box)))
        if "".join(c for c, _ in glyphs) != "".join(local.split()):
            return {}
        image = _page_ink(page, read_image)
        if image is None:
            raise ValueError("Inconsistent original page image geometry")
        located = {}
        for g, (i, j, new) in enumerate(edits, 1):
            if i not in eligible:
                continue
            if match:
                if match[g] != new:
                    continue
                offset = match.start(g)
            else:
                # Bind two unique flanking words at the same provenance edge.
                # A hit elsewhere in the box (even of the whole phrase) is not proof.
                left = re.search(r"(\w+)([^\w]*)$", original[:i])
                right = re.match(r"([^\w]*)(\w+)", original[j:])
                if (
                    not left
                    or not right
                    or any(
                        len(re.findall(r"\b" + re.escape(word) + r"\b", text)) != 1
                        for word in (left[1], right[2])
                        for text in (original, item.text, local)
                    )
                ):
                    continue
                # Whole words here (no wrapping inside an anchor) so uniqueness
                # and the position lookup count the same occurrences.
                anchor = r"\b" + re.escape(left[0] + new + right[0]) + r"\b"
                proposed = re.search(anchor, item.text)
                found = re.search(anchor, local)
                if (
                    not proposed
                    or proposed.start() + len(left[0]) != changes[g - 1][2]
                    or not found
                ):
                    continue
                if (
                    local[: found.start()] != original[: left.start()]
                    and local[found.end() :] != original[j + right.end() :]
                ):
                    continue
                offset = found.start() + len(left[0])
            pos = len("".join(local[:offset].split()))
            if not 0 < pos < len(glyphs) - 1:
                continue
            _, glyph = glyphs[pos]
            context = [row for _, row in glyphs[pos - 1 : pos + 2]]
            visible = _visible_glyphs(tp, bounds, context, page, image)
            if visible is None:
                raise ValueError("Unable to verify original text drawing")
            if visible:
                located[i] = ", ".join(f"{x:.3f}" for x in glyph[2])
        return located

    located = {}
    if eligible and independent:
        result = _local_text(
            [part.prov], page, read_original, inspect, strict_pdf=True, verify_page=True
        )
        if result:
            (located,) = result
    adopted = []
    evidence = list(item.evidence)
    cursor = 0
    for i, j, new in edits:
        before = original[i:j]
        spans = (
            [(i, j)]
            if before
            else [(x, x + 1) for x in (i - 1, i) if 0 <= x < len(original)]
        )
        reason = (
            f"{before!r} → {new!r}"
            if before
            else f"Insertion of {new!r} between extraction characters"
        )
        selected = []
        for a, b in spans:
            span = (part.start + a, part.start + b)
            position = f"characters {span[0]}:{span[1]}"
            if i in located:
                position += f"; glyph bbox ({located[i]}) pt, bottom-left"
            selected.append(
                part.evidence().model_copy(
                    update={"charspan": span, "position": position, "reason": reason}
                )
            )
        adopted.extend([original[cursor:i], new if i in located else before])
        cursor = j
        if i in located:
            evidence.extend(selected)
        else:
            issue(
                f"{reason} not corroborated at this location; retained extraction",
                [item.block_id],
                selected,
            )
    return item.model_copy(
        update={"text": "".join(adopted) + original[cursor:], "evidence": evidence}
    )


def _drawn_listing(item, nodes, valid, page, read_original, read_image):
    """Corroborate complete, adjacent item boxes, never an offset into invalid text."""
    provs = [n["prov"][0] for n in nodes]
    image = _page_ink(page, read_image)
    if image is None:
        return None

    def inspect(tp, local, bounds):
        glyphs = []
        rows = _line_origins(tp, local, bounds, printed_lines=True, glyphs=glyphs)
        if not rows or not glyphs:
            return None

        if not _visible_glyphs(tp, bounds, glyphs, page, image):
            return None
        drawn_x = min(b[0] for _, _, b in glyphs), max(b[2] for _, _, b in glyphs)
        return rows, bounds, drawn_x

    groups = _local_text(provs, page, read_original, inspect, strict_pdf=True)
    if not groups:
        return None
    rows = []
    for i, ((group, box, drawn_x), ev) in enumerate(
        zip(groups, item.evidence, strict=True)
    ):
        if i:
            _, above, prior_x = groups[i - 1]
            if not (
                max(prior_x[0], drawn_x[0]) < min(prior_x[1], drawn_x[1])
                and 0 <= above[1] - box[3] <= max(above[3] - above[1], box[3] - box[1])
            ):
                return None
        anchor = valid.get(ev.ref)
        cursor = 0
        for line, x, y, steps in group:
            if rows and rows[-1][2] <= y:
                return None
            span = None
            gap = ""
            if anchor:
                match = re.match(r"(\s*)" + re.escape(line), anchor.text[cursor:])
                if not match:
                    return None
                gap = match[1]
                span = (
                    anchor.start + cursor + len(gap),
                    anchor.start + cursor + match.end(),
                )
                cursor += match.end()
            location = ev.model_copy(
                update={
                    "charspan": span,
                    "position": f"line origin ({x:.3f}, {y:.3f}) pt, bottom-left",
                    "reason": "Visible drawing in owned listing",
                }
            )
            rows.append((line, x, y, steps, location, gap))
        if anchor and cursor != len(anchor.text):
            return None
    # Check the space between refs too: omitted text in the gap is a barrier.
    boxes = [box for _, box, _ in groups]
    envelope = {
        "l": min(b[0] for b in boxes),
        "b": min(b[1] for b in boxes),
        "r": max(b[2] for b in boxes),
        "t": max(b[3] for b in boxes),
        "coord_origin": "BOTTOMLEFT",
    }
    band = _local_text(
        [{"bbox": envelope}],
        page,
        read_original,
        lambda tp, text, bounds: _line_origins(tp, text, bounds, printed_lines=True),
        strict_pdf=True,
    )
    if band != [[row[:4] for row in rows]]:
        return None
    proposed = [m for m in re.finditer(r"[^\r\n]+", item.text) if m[0].strip(" \t")]
    wanted = [m[0] for m in proposed]
    if len(wanted) != len(rows) or any(
        line.lstrip(" ") != r[0].lstrip(" ")
        for line, r in zip(wanted, rows, strict=True)
    ):
        return None
    pitch = next((d for r in rows for d in r[3]), None)
    margin = min(r[1] for r in rows)
    columns = [(r[1] - margin) / pitch if pitch else 0 for r in rows]
    measured = (
        pitch
        and all(isclose(d, pitch, abs_tol=0.02) for r in rows for d in r[3])
        and all(
            isclose(round(c) * pitch, r[1] - margin, abs_tol=0.02)
            for c, r in zip(columns, rows, strict=True)
        )
    )
    display = []
    differences = []
    for i, (r, column) in enumerate(zip(rows, columns, strict=True)):
        line = " " * (round(column) if measured else 0) + r[0]
        if i:
            separator = "".join(re.findall(r"\r\n|[\r\n]", r[5])) or "\n"
            display.append(separator)
            proposed_gap = item.text[proposed[i - 1].end() : proposed[i].start()]
            if proposed_gap != separator:
                differences.append(
                    (
                        "Line separator differs between drawn rows"
                        if re.sub(r"\r\n?", "\n", proposed_gap)
                        == re.sub(r"\r\n?", "\n", separator)
                        else "Additional blank-line count not established between drawn rows",
                        [rows[i - 1][4], r[4]],
                    )
                )
        display.append(line)
    # Keep protected whitespace in already-owned ranges, not just their letters.
    for ref, anchor in valid.items():
        selected = [i for i, r in enumerate(rows) if r[4].ref == ref]
        printed = "".join(display[2 * selected[0] : 2 * selected[-1] + 1])
        # Compare indentation on logical lines; display and source offsets keep their EOLs.
        if not _printed_candidate(
            re.sub(r"\r\n?", "\n", anchor.text),
            re.sub(r"\r\n?", "\n", printed),
            relative_margin=True,
        ):
            return None
    locations = [r[4] for r in rows]
    if proposed[0].start():
        differences.append(("Leading blank-line count not established", locations[:1]))
    if proposed[-1].end() != len(item.text):
        differences.append(
            ("Trailing blank-line count not established", locations[-1:])
        )
    differences.append(
        ("Absolute indentation not established; see each drawn line origin", locations)
    )
    return "".join(display), locations, differences


def _caption_listing_parts(
    caption, code, selectors, raw, items, answer, page, read_original, read_image
):
    """Place the caption and *all* listing refs against one complete painted band."""
    if caption.target_id != code.block_id or len(caption.evidence) != 1:
        return None
    note = caption.evidence[0]
    refs = {e.ref for e in code.evidence}
    if (
        len(refs) < 2
        or len(refs) != len(code.evidence)
        or note.ref not in refs
        or any(
            e.page != page.page or e.cell_index is not None or e.ref is None
            for e in [note, *code.evidence]
        )
        or any(
            len(selectors[page.page, ref]) != (2 if ref == note.ref else 1)
            for ref in refs
        )
        or any(
            e.page == page.page and e.ref in {*refs, None}
            for ex in answer.exclusions
            for e in ex.evidence
        )
        or any(
            not isinstance(b, PictureBlock)
            and any(e.page == page.page and e.ref is None for e in b.evidence)
            for b in answer.blocks
        )
    ):
        return None
    parts = {}
    for order, (ref, node, container) in enumerate(_reading_order(raw, nested=True)):
        if ref not in refs:
            continue
        if (
            container
            or node.get("label") != "code"
            or node.get("content_layer", "body") != "body"
            or len(node["prov"]) != 1
        ):
            return None
        (prov,) = node["prov"]
        span = prov.get("charspan")
        if (
            prov["page_no"] != page.page
            or not isinstance(node.get("text"), str)
            or not _valid_span(span, node["text"])
        ):
            return None
        parts[ref] = _Portion(order, node, prov, *span)
    if parts.keys() != refs or max(p.order for p in parts.values()) - min(
        p.order for p in parts.values()
    ) + 1 != len(parts):
        return None
    shared = parts[note.ref]
    end = shared.start + len(caption.text)
    if (
        not caption.text
        or not shared.text.startswith(caption.text)
        or shared.text.find(caption.text, 1) != -1
        or note.charspan not in (None, (shared.start, end))
    ):
        return None
    try:
        boxes = {r: _page_box(p.prov, page) for r, p in parts.items()}
        left, bottom, right, top = (
            min(b[0] for b in boxes.values()),
            min(b[1] for b in boxes.values()),
            max(b[2] for b in boxes.values()),
            max(b[3] for b in boxes.values()),
        )
        if any(
            not (0 <= l < r <= page.width_points and 0 <= b < t <= page.height_points)
            for l, b, r, t in boxes.values()
        ):
            return None
        if any(
            l < right and left < r and b < top and bottom < t
            for ref, node in items.items()
            if ref not in refs
            for prov in node["prov"]
            if prov["page_no"] == page.page
            for l, b, r, t in [_page_box(prov, page)]
        ):
            return None
    except ValueError:
        return None
    if any(
        isinstance(fig, PictureBlock)
        and fig.region.page == page.page
        and (
            any(
                not _outside_region(p, {page.page: page}, fig.region)
                for p in parts.values()
            )
            or any(
                m.target_id in (caption.block_id, code.block_id)
                or (m.ref and m.ref.ref in refs)
                for m in fig.members
            )
        )
        for fig in answer.blocks
    ):
        return None
    image = _page_ink(page, read_image)
    if image is None:
        return None

    def inspect(tp, local, bounds):
        glyphs = []
        rows = _line_origins(tp, local, bounds, printed_lines=True, glyphs=glyphs)
        if not rows or not _visible_glyphs(tp, bounds, glyphs, page, image):
            return None
        painted = iter(glyphs)
        result = []
        overlap = None
        for index, row in enumerate(rows):
            ink = [next(painted)[2] for c in row[0] if not c.isspace()]
            if not ink:
                return None
            if index:
                # PDFium can collapse a pair of printed spaces into one extracted SPACE.
                if " " in row[0] and not all(
                    isclose(d, row[3][0], abs_tol=0.02) for d in row[3]
                ):
                    return None
                left, right = min(g[0] for g in ink), max(g[2] for g in ink)
                overlap = (
                    (left, right)
                    if overlap is None
                    else (max(left, overlap[0]), min(right, overlap[1]))
                )
                if overlap[0] >= overlap[1]:
                    return None
            centers = [((l + r) / 2, (b + t) / 2) for l, b, r, t in ink]
            result.append(
                (
                    row,
                    {
                        ref
                        for ref, (l, b, r, t) in boxes.items()
                        if all(l <= x <= r and b <= y <= t for x, y in centers)
                    },
                )
            )
        return result

    band = {"l": left, "r": right, "t": top, "b": bottom, "coord_origin": "BOTTOMLEFT"}
    result = _local_text(
        [{"bbox": band}], page, read_original, inspect, strict_pdf=True
    )
    if not result:
        return None
    (rows,) = result
    proposed = [
        line.lstrip(" \t") for line in code.text.splitlines() if line.strip(" \t")
    ]
    if (
        rows[0][0][0] != caption.text
        or note.ref not in rows[0][1]
        or [row[0].lstrip(" ") for row, _ in rows[1:]] != proposed
        or any(a[0][2] <= b[0][2] for a, b in itertools.pairwise(rows))
        or caption.text in proposed
    ):
        return None
    cap = _Portion(shared.order, shared.node, shared.prov, shared.start, end)
    # Remove one role boundary, not indentation or blank lines belonging to the code.
    boundary = re.match(r" *\r?\n| ", shared.node["text"][end : shared.end])
    shared.start = end + (boundary.end() if boundary else 0)
    cursors = {r: p.start for r, p in parts.items()}
    by_ref = defaultdict(list)
    extracted = []
    for row, candidates in rows[1:]:
        line = row[0].lstrip(" ")
        matches = [
            (r, m)
            for r in candidates
            if (
                m := re.match(
                    r"([ \t\r\n]*)" + re.escape(line),
                    parts[r].node["text"][cursors[r] : parts[r].end],
                )
            )
        ]
        if len(matches) != 1:
            return None
        r, m = matches[0]
        if r in by_ref and r != next(reversed(by_ref)):
            return None
        previous = next(reversed(by_ref), None)
        by_ref[r].append(row)
        gap = m[1]
        if previous is not None and r != previous:
            tail = parts[previous].node["text"][cursors[previous] : parts[previous].end]
            if tail.strip(" \t\r\n"):
                return None
            cursors[previous] = parts[previous].end
            if tail:
                gap = tail + ("" if re.search(r"[\r\n]", tail + gap) else "\n") + gap
        loc = (
            parts[r]
            .evidence()
            .model_copy(
                update={
                    "charspan": (cursors[r] + len(m[1]), cursors[r] + m.end()),
                    "position": f"line origin ({row[1]:.3f}, {row[2]:.3f}) pt, bottom-left",
                }
            )
        )
        cursors[r] += m.end()
        if extracted and "\n" not in gap and "\r" not in gap:
            gap = "\n" + gap
        extracted.append((gap + line, loc))
    if (
        by_ref.keys() != refs
        or next(reversed(by_ref)) != note.ref
        or any(cursors[r] != p.end for r, p in parts.items())
        or any(
            e.charspan not in (None, (parts[e.ref].start, parts[e.ref].end))
            for e in code.evidence
        )
    ):
        return None
    return cap, [(parts[r], rows) for r, rows in by_ref.items()], extracted


# Only the glyph form of a quotation mark; every other character must match literally.
_QUOTE_FORMS = str.maketrans("\u201c\u201d\u2018\u2019", "\"\"''")


def _quote_pattern(text):
    return "".join(
        '["\u201c\u201d]'
        if c == '"'
        else "['\u2018\u2019]"
        if c == "'"
        else re.escape(c)
        for c in text.translate(_QUOTE_FORMS)
    )


def _same_text(a, b):
    return " ".join(a.translate(_QUOTE_FORMS).split()) == " ".join(
        b.translate(_QUOTE_FORMS).split()
    )


def _drawn_rows(tp, local, bounds, page, image):
    """Visible native rows in the band, each with its font set and glyph centers."""
    import pypdfium2.raw as native

    fonts = []
    rows = _line_origins(tp, local, bounds, printed_lines=True, row_fonts=fonts)
    if not rows:
        return None
    glyphs = []
    for n in range(tp.count_chars()):
        c = chr(native.FPDFText_GetUnicode(tp, n))
        if c.isspace():
            continue
        l, b, r, t = box = tp.get_charbox(n)
        if (
            bounds[0] <= (l + r) / 2 <= bounds[2]
            and bounds[1] <= (b + t) / 2 <= bounds[3]
        ):
            obj = native.FPDFText_GetTextObject(tp, n)
            if native.FPDFText_IsGenerated(tp, n) != 0 or not _paints_text(obj):
                return None
            glyphs.append((c, (n, obj, box)))
    # Every painted glyph in the band is one of the rows' characters, in order.
    if "".join(c for c, _ in glyphs) != "".join("".join(r[0] for r in rows).split()):
        return None
    if not _visible_glyphs(tp, bounds, [g for _, g in glyphs], page, image):
        return None
    painted = iter(glyphs)
    result = []
    for row, font in zip(rows, fonts, strict=True):
        centers = [
            ((l + r) / 2, (b + t) / 2)
            for _, (_, _, (l, b, r, t)) in (
                next(painted) for c in row[0] if not c.isspace()
            )
        ]
        if not centers:
            return None
        result.append((row, font, centers))
    return result


def _role_evidence(blocks, page, selectors, answer, refs):
    """Bare same-page evidence only, from refs no block outside the claimants cites."""
    keys = {b.block_id for b in blocks}
    for b in blocks:
        cited = [e.ref for e in b.evidence]
        if len(set(cited)) != len(cited) or any(
            e.page != page.page
            or e.ref is None
            or e.charspan is not None
            or e.cell_index is not None
            for e in b.evidence
        ):
            return False
        refs.extend(r for r in cited if r not in refs)
    return not (
        any({k for k, _ in selectors[page.page, r]} - keys for r in refs)
        or any(
            e.page == page.page and e.ref in {*refs, None}
            for ex in answer.exclusions
            for e in ex.evidence
        )
        or any(
            not isinstance(b, PictureBlock)
            and any(e.page == page.page and e.ref is None for e in b.evidence)
            for b in answer.blocks
        )
    )


def _role_nodes(raw, refs, page):
    nodes = {}
    for order, (ref, node, container) in enumerate(_reading_order(raw, nested=True)):
        if ref not in refs:
            continue
        if (
            container
            or node.get("content_layer", "body") != "body"
            or node.get("label") not in ("text", "paragraph", "code")
            or len(node["prov"]) != 1
        ):
            return None
        (prov,) = node["prov"]
        span = prov.get("charspan")
        if (
            prov["page_no"] != page.page
            or not isinstance(node.get("text"), str)
            or not _valid_span(span, node["text"])
        ):
            return None
        nodes[ref] = _Portion(order, node, prov, *span)
    orders = sorted(p.order for p in nodes.values())
    if nodes.keys() != set(refs) or orders[-1] - orders[0] + 1 != len(orders):
        return None
    return nodes


def _role_band(boxes, page, items, answer, allowed):
    """One band holding only the given refs' items, away from any figure."""
    left, bottom, right, top = (
        min(b[0] for b in boxes.values()),
        min(b[1] for b in boxes.values()),
        max(b[2] for b in boxes.values()),
        max(b[3] for b in boxes.values()),
    )
    if any(
        not (0 <= l < r <= page.width_points and 0 <= b < t <= page.height_points)
        for l, b, r, t in boxes.values()
    ):
        return None
    if any(
        l < right and left < r and b < top and bottom < t
        for ref, node in items.items()
        if ref not in allowed
        for prov in node["prov"]
        if prov["page_no"] == page.page
        for l, b, r, t in [_page_box(prov, page)]
    ):
        return None
    sx, sy = page.scale_x, page.scale_y
    if any(
        isinstance(fig, PictureBlock)
        and fig.region.page == page.page
        and fig.region.left < right * sx
        and left * sx < fig.region.right
        and fig.region.top < (page.height_points - bottom) * sy
        and (page.height_points - top) * sy < fig.region.bottom
        for fig in answer.blocks
    ):
        return None
    return {"l": left, "r": right, "t": top, "b": bottom, "coord_origin": "BOTTOMLEFT"}


def _code_groups(run, nodes):
    """Drawn code rows as per-ref portions in printed order, plus extraction lines."""
    groups = []
    extracted = []
    for r, row, _, a, b, gap in run:
        part = nodes[r]
        groups.append((_Portion(part.order, part.node, part.prov, a, b), [row]))
        loc = part.evidence().model_copy(
            update={
                "charspan": (a, b),
                "position": f"line origin ({row[1]:.3f}, {row[2]:.3f}) pt, bottom-left",
            }
        )
        separator = "".join(re.findall(r"\r\n|[\r\n]", gap)) or "\n"
        extracted.append(
            ((separator if extracted else "") + part.node["text"][a:b], loc)
        )
    return groups, extracted


def _shared_item_roles(
    part,
    entries,
    targets,
    selectors,
    raw,
    items,
    answer,
    page,
    read_original,
    read_image,
):
    """Partition one jointly claimed item and its claimants' refs by drawn row and role.

    Every drawn row maps to exactly one extraction span. Each claimant, in correction
    order, takes one contiguous run of rows; text roles match their full proposal,
    code rows match its proposed lines, and code/text boundaries change typeface.
    """
    position = {b.block_id: i for i, b in enumerate(answer.blocks)}
    keys = [k for k, _ in entries]
    if (
        len(set(keys)) != len(keys)
        or len(keys) < 2
        or any(k not in position for k in keys)
    ):
        return None
    keys.sort(key=position.__getitem__)
    blocks = [targets[k] for k in keys]
    kinds = [getattr(b, "kind", None) for b in blocks]
    if not set(kinds) <= {"body", "caption", "code"}:
        return None
    if "caption" in kinds:
        # A caption names the listing directly after it, or is that listing's only note.
        if kinds != ["caption", "code"] or blocks[0].target_id not in (None, keys[1]):
            return None
    elif "code" in kinds and "body" not in kinds:
        return None
    refs = []
    if not _role_evidence(blocks, page, selectors, answer, refs):
        return None
    nodes = _role_nodes(raw, refs, page)
    if nodes is None or nodes[part.node["self_ref"]].order != part.order:
        return None
    try:
        boxes = {r: _page_box(p.prov, page) for r, p in nodes.items()}
        band = _role_band(boxes, page, items, answer, refs)
    except ValueError:
        return None
    image = _page_ink(page, read_image)
    if band is None or image is None:
        return None
    result = _local_text(
        [{"bbox": band}],
        page,
        read_original,
        lambda tp, local, bounds: _drawn_rows(tp, local, bounds, page, image),
        strict_pdf=True,
    )
    if not result:
        return None
    (drawn,) = result
    cursors = {r: p.start for r, p in nodes.items()}
    rows = []
    for row, font, centers in drawn:
        line = row[0].lstrip(" ")
        matches = [
            (r, m)
            for r, (l, b, rt, t) in boxes.items()
            if all(l <= x <= rt and b <= y <= t for x, y in centers)
            if (
                m := re.match(
                    r"([ \t\r\n]*)" + _quote_pattern(line),
                    nodes[r].node["text"][cursors[r] : nodes[r].end],
                )
            )
        ]
        if len(matches) != 1:
            return None
        r, m = matches[0]
        a = cursors[r] + len(m[1])
        cursors[r] += m.end()
        rows.append((r, row, font, a, cursors[r], m[1]))
    if any(
        p.node["text"][cursors[r] : p.end].strip() or cursors[r] == p.start
        for r, p in nodes.items()
    ):
        return None

    def fits(block, run):
        cited = {e.ref for e in block.evidence}
        if {row[0] for row in run} != cited:
            return False
        drawn_lines = [row[1][0] for row in run]
        if block.kind != "code":
            return _same_text(" ".join(drawn_lines), block.text)
        proposed = [line for line in block.text.splitlines() if line.strip()]
        if len(proposed) != len(run):
            return False
        if kinds[0] == "caption":
            # Character corrections only: the proposal keeps each drawn row's spacing.
            return all(
                re.split(r"\S+", p.strip()) == re.split(r"\S+", d.strip())
                for p, d in zip(proposed, drawn_lines, strict=True)
            )
        return all(_same_text(p, d) for p, d in zip(proposed, drawn_lines, strict=True))

    def faces(run):
        return set().union(*(row[2] for row in run))

    solutions = []

    def place(index, start, runs):
        if len(solutions) > 1:
            return
        if index == len(blocks):
            if start == len(rows):
                solutions.append(runs)
            return
        for end in range(start + 1, len(rows) - (len(blocks) - index - 1) + 1):
            run = rows[start:end]
            if not fits(blocks[index], run):
                continue
            if (
                runs
                and (kinds[index] == "code") != (kinds[index - 1] == "code")
                and faces(run) & faces(runs[-1])
            ):
                continue
            place(index + 1, end, [*runs, run])

    place(0, 0, [])
    if len(solutions) != 1:
        return None
    shared = part.node["self_ref"]
    spans = {}
    listings = {}
    ordered = []
    for key, block, run in zip(keys, blocks, solutions[0], strict=True):
        own = [row for row in run if row[0] == shared]
        spans[key] = (own[0][3], own[-1][4])
        if block.kind == "code":
            listings[key] = _code_groups(run, nodes)
            ordered.extend((p, key) for p, _ in listings[key][0])
            continue
        for r in dict.fromkeys(row[0] for row in run):
            n = nodes[r]
            a, b = spans[key] if r == shared else (n.start, n.end)
            ordered.append((_Portion(n.order, n.node, n.prov, a, b), key))
    return spans, listings, ordered


def _shared_listing_edge(
    part,
    entries,
    targets,
    selectors,
    raw,
    items,
    answer,
    page,
    read_original,
    read_image,
):
    """A listing's rows drawn in its own refs' band include one edge of a shared item.

    The code's exclusive refs are read as complete visible rows; the remaining rows
    must be one contiguous run at the edge of the shared item that faces the listing.
    The single text claimant keeps the rest of that item.
    """
    position = {b.block_id: i for i, b in enumerate(answer.blocks)}
    keys = [k for k, _ in entries]
    if len(set(keys)) != 2 or len(keys) != 2 or any(k not in position for k in keys):
        return None
    keys.sort(key=position.__getitem__)
    blocks = [targets[k] for k in keys]
    kinds = [getattr(b, "kind", "") for b in blocks]
    if sorted(kinds) != ["body", "code"]:
        return None
    refs = []
    if not _role_evidence(blocks, page, selectors, answer, refs):
        return None
    nodes = _role_nodes(raw, refs, page)
    shared = part.node["self_ref"]
    if nodes is None or nodes[shared].order != part.order:
        return None
    code = blocks[kinds.index("code")]
    text = blocks[kinds.index("body")]
    tail = kinds[0] == "body"
    own = [e.ref for e in code.evidence if e.ref != shared]
    others = [nodes[e.ref].order for e in text.evidence if e.ref != shared]
    # The text runs into the shared item from the side away from the listing.
    if (
        not own
        or any((o > part.order) if tail else (o < part.order) for o in others)
        or any(
            (nodes[r].order < part.order) if tail else (nodes[r].order > part.order)
            for r in own
        )
    ):
        return None
    try:
        boxes = {r: _page_box(nodes[r].prov, page) for r in own}
        shared_box = _page_box(part.prov, page)
        # The listing's own rows set the height; the shared item may be wider.
        lower = min(b[1] for b in boxes.values())
        upper = max(b[3] for b in boxes.values())
        across = (shared_box[0], lower, shared_box[2], upper)
        band = _role_band(
            {**boxes, shared: across}, page, items, answer, [*own, shared]
        )
    except ValueError:
        return None
    image = _page_ink(page, read_image)
    if band is None or image is None:
        return None
    result = _local_text(
        [{"bbox": band}],
        page,
        read_original,
        lambda tp, local, bounds: _drawn_rows(tp, local, bounds, page, image),
        strict_pdf=True,
    )
    if not result:
        return None
    (drawn,) = result
    cursors = {r: nodes[r].start for r in own}
    rows: list[list[Any]] = []
    edge = []
    for row, font, centers in drawn:
        line = row[0].lstrip(" ")
        hits = [
            (r, m)
            for r, (l, b, rt, t) in boxes.items()
            if all(l <= x <= rt and b <= y <= t for x, y in centers)
            if (
                m := re.match(
                    r"([ \t\r\n]*)" + re.escape(line),
                    nodes[r].node["text"][cursors[r] : nodes[r].end],
                )
            )
        ]
        l, b, rt, t = shared_box
        if len(hits) > 1:
            return None
        if hits:
            r, m = hits[0]
            a = cursors[r] + len(m[1])
            cursors[r] += m.end()
            rows.append([r, row, font, a, cursors[r], m[1]])
        elif all(l <= x <= rt and b <= y <= t for x, y in centers):
            if edge and edge[-1] != len(rows) - 1:
                return None
            edge.append(len(rows))
            rows.append([shared, row, font, None, None, ""])
        else:
            return None
    if not edge or any(
        nodes[r].node["text"][cursors[r] : nodes[r].end].strip()
        or cursors[r] == nodes[r].start
        for r in own
    ):
        return None
    whole = part.node["text"]
    pattern = r"[ \t\r\n]+".join(re.escape(rows[i][1][0].lstrip(" ")) for i in edge)
    found = list(re.finditer(pattern, whole[part.start : part.end]))
    if len(found) != 1:
        return None
    (m,) = found
    a, b = part.start + m.start(), part.start + m.end()
    if whole[b if tail else part.start : part.end if tail else a].strip():
        return None
    cursor = a
    for i in edge:
        n = re.match(
            r"([ \t\r\n]*)" + re.escape(rows[i][1][0].lstrip(" ")), whole[cursor:b]
        )
        assert n
        rows[i][3:] = [cursor + len(n[1]), cursor + n.end(), n[1]]
        cursor += n.end()
    proposed = [line for line in code.text.splitlines() if line.strip()]
    drawn_lines = [row[1][0] for row in rows]
    if len(proposed) != len(rows) or not all(
        _same_text(p, d) for p, d in zip(proposed, drawn_lines, strict=True)
    ):
        return None
    rest = whole[part.start : a] if tail else whole[b : part.end]
    if not rest.strip():
        return None
    if tail:
        text_span = (part.start, part.start + len(rest.rstrip()))
    else:
        text_span = (part.end - len(rest.lstrip()), part.end)
    listing = _code_groups(rows, nodes)
    prose = [
        (
            _Portion(
                n.order,
                n.node,
                n.prov,
                *(text_span if r == shared else (n.start, n.end)),
            ),
            text.block_id,
        )
        for r, n in sorted(nodes.items(), key=lambda kv: kv[1].order)
        if r in {e.ref for e in text.evidence}
    ]
    code_parts = [(p, code.block_id) for p, _ in listing[0]]
    return (
        {text.block_id: text_span, code.block_id: (a, b)},
        {code.block_id: listing},
        prose + code_parts if tail else code_parts + prose,
    )


def _occurrences(phrase, text):
    return len(re.findall(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))


def _cell_repeats(parts, item, cells):
    """Fragments also printed in the block's own table cells, each occurrence unchanged.

    The proposal must repeat each whole fragment exactly as often as the cited cells
    and the equal fragments together hold it. Equal occurrences place identical text.
    """
    for part in parts:
        if tuple(part.prov["charspan"]) != (part.start, part.end) or _occurrences(
            part.text, item.text
        ) != sum(_occurrences(part.text, c) for c in cells) + sum(
            p.text == part.text for p in parts
        ):
            return None
    return [(p.start, p.end) for p in parts]


def _fragment_places(parts, item, pages, read_original):
    """Each separately extracted fragment at one ordered place in the proposal.

    A list marker split from its item text is the block's marker. A fragment whose
    extraction differs from its drawing is located by its drawn row only.
    """
    located = []
    cursor = 0
    for index, part in enumerate(parts):
        text = part.text
        marker = (
            re.fullmatch(re.escape(item.marker) + r"\s+(\S.*)", text, re.DOTALL)
            if index == 0 and isinstance(item, ListBlock) and item.marker
            else None
        )
        if marker:
            text = marker[1]

        def literal(phrase):
            return list(
                re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", item.text)
            )

        matches = literal(text)
        if not matches and not marker:
            drawn = _local_text(
                [part.prov],
                pages[part.rank[0]],
                read_original,
                lambda tp, local, bounds: _line_origins(
                    tp, local, bounds, printed_lines=True
                ),
                strict_pdf=True,
            )
            if drawn and len(drawn[0]) == 1 and drawn[0][0][0] != text:
                matches = literal(drawn[0][0][0])
        if len(matches) != 1 or matches[0].start() < cursor:
            return None
        if marker and matches[0].start():
            return None
        located.append((part, text, matches[0]))
        cursor = matches[0].end()
    return located


def _printed_role_split(part, entries, targets, page, read_original):
    """Bind a complete edge note and its listing to the same ordered painted rows."""
    codes = [(targets[k], e) for k, e in entries if isinstance(targets[k], CodeBlock)]
    notes = [(targets[k], e) for k, e in entries if isinstance(targets[k], TextBlock)]
    if len(codes) != 1 or len(notes) != 1:
        return None
    (code, ce), (note, ne) = codes[0], notes[0]
    # This local proof cannot apportion another extraction item to either role.
    if any(
        e.ref and (e.page, e.ref) != (ce.page, ce.ref)
        for block in (code, note)
        for e in block.evidence
    ):
        return None
    raw = part.node["text"]

    def unique_start(text):
        pos = raw.find(text, part.start, part.end)
        return pos if pos >= 0 and raw.find(text, pos + 1, part.end) < 0 else None

    literal = unique_start(code.text)
    if note.kind == "caption" and note.target_id == code.block_id:
        first = True
    elif note.kind in ("footnote", "body"):
        first = False
    else:
        return None
    a = part.start if first else part.end - len(note.text)
    if (
        a < part.start
        or a + len(note.text) > part.end
        or raw[a : a + len(note.text)] != note.text
    ):
        return None
    # Narrowing a bare ref must not silently drop another provenance on this page.
    if (ne.charspan is None or ce.charspan is None) and sum(
        p["page_no"] == page.page for p in part.node["prov"]
    ) != 1:
        return None
    if ne.charspan is None and unique_start(note.text) != a:
        return None
    # Only narrow an already disjoint, unique literal body/code pair, not a new body claim.
    if note.kind == "body" and (
        literal != part.start
        or part.start + len(code.text) > a
        or raw[part.start + len(code.text) : a].strip()
    ):
        return None
    b = a + len(note.text)
    if ne.charspan is not None and ne.charspan != (a, b):
        return None
    fonts = [] if note.kind == "footnote" else None

    def inspect(tp, local, bounds):
        if note.kind == "footnote":
            # A different face alone could just be the listing's final code row.
            marker = re.match(r"(\d+[.)]) +(?=\S)", note.text)
            if not marker:
                return None
            glyphs = _word_draws(tp, note.text, bounds)
            if (
                not glyphs
                or not all(glyphs[: marker.end(1)])
                or not glyphs[marker.end()]
            ):
                return None
            mark_styles = {g[4] for g in glyphs[: marker.end(1)]}
            if len(mark_styles) != 1 or glyphs[marker.end()][4] in mark_styles:
                return None
        return _line_origins(tp, local, bounds, printed_lines=True, row_fonts=fonts)

    drawn = _local_text([part.prov], page, read_original, inspect, strict_pdf=True)
    if not drawn:
        return None
    rows = drawn[0]
    if (
        len(rows) < 2
        or rows[0 if first else -1][0] != note.text
        or any(left[2] <= right[2] for left, right in itertools.pairwise(rows))
    ):
        return None
    # Match the *entire* provenance, skipping separators only between drawn rows.
    cursor = 0
    for line, *_ in rows:
        content = line.lstrip(" ")
        m = re.match(r"\s*" + re.escape(content), part.text[cursor:])
        if not content or not m:
            return None
        cursor += m.end()
    if part.text[cursor:].strip():
        return None
    if fonts is not None and (
        not fonts[-1] or not fonts[-1].isdisjoint(set().union(*fonts[:-1]))
    ):
        return None
    code_rows = rows[1:] if first else rows[:-1]
    proposed = [s.lstrip(" ") for s in code.text.splitlines() if s.strip()]
    contents = [r[0].lstrip(" ") for r in code_rows]
    lo, hi = (b, part.end) if first else (part.start, a)
    if first and (boundary := re.match(r" *\r?\n", raw[lo:hi])):
        # Exactly one caption boundary, not part of the listing or its internal blank lines.
        owned = (lo + boundary.end(), hi)
    elif (
        literal is not None
        and lo <= literal
        and literal + len(code.text) <= hi
        and not raw[lo:literal].strip(" ")
        and not raw[literal + len(code.text) : hi].strip(" ")
    ):
        literal_span = (literal, literal + len(code.text))
        # A trailing note alone cannot exclude the listing's leading SPACE.
        owned = (
            literal_span
            if first or ce.charspan == literal_span
            else (lo, literal_span[1])
        )
    elif first:
        owned = (lo + int(raw.startswith(" ", lo, hi)), hi)
    elif note.kind == "body":
        owned = (lo, lo + len(code.text))
    else:
        gap = re.search(r"(?:\r?\n| )$", raw[lo:hi])
        owned = (lo, hi - (len(gap[0]) if gap else 0))
    if ce.charspan is not None:
        if ce.charspan != owned:
            return None
    elif contents != proposed and not (
        ne.charspan == (a, b) and " ".join(contents) == " ".join(proposed)
    ):
        return None
    return {note.block_id: (a, b), code.block_id: owned}, code_rows


def _listing_layout(
    parts,
    proposal,
    page,
    read_original,
    *,
    printed_lines,
    relative_margin,
    layout_version,
    caption=None,
    shared_rows=None,
):
    """Validate proposed lines individually against the complete local text runs."""
    original = _joined(parts)

    def candidate(text):
        if printed_lines:
            return _printed_candidate(original, text, relative_margin=relative_margin)
        return _layout_candidate(original, text)

    if (layout_version < 8 and not candidate(proposal)) or any(
        p.rank[0] != page.page
        or (
            caption is None
            and (shared_rows is None or p.rank not in shared_rows)
            and [p.start, p.end] != p.prov["charspan"]
        )
        or "bbox" not in p.prov
        for p in parts
    ):
        return None
    if shared_rows and all(p.rank in shared_rows for p in parts):
        printed = [shared_rows[p.rank] for p in parts]
    else:
        printed = _local_text(
            [p.prov for p in parts],
            page,
            read_original,
            lambda tp, local, bounds: _line_origins(
                tp, local, bounds, printed_lines=printed_lines
            ),
            strict_pdf=printed_lines,
        )
    if printed is None:
        return None
    rows = []
    for part_index, (part, origins) in enumerate(zip(parts, printed, strict=True)):
        if caption is not None:
            # The bounding box is shared: locate both roles in one complete native run.
            if not (
                len(parts) == 1
                and len(origins) >= 2
                and origins[0][0] == caption
                and origins[0][2] > origins[1][2]
            ):
                return None
            origins = origins[1:]
        if shared_rows:
            origins = shared_rows.get(part.rank, origins)
        cursor = 0
        for line, x, y, steps in origins:
            content = line.lstrip(" ")
            m = re.match(r"(\s*)" + re.escape(content), part.text[cursor:])
            if not content or not m:
                return None
            start = cursor + len(m[1])
            cursor += m.end()
            ev = part.evidence().model_copy(
                update={
                    "charspan": (part.start + start, part.start + cursor),
                    "position": f"line origin ({x:.3f}, {y:.3f}) pt, bottom-left",
                }
            )
            gap = m[1]
            if rows and start == len(gap):
                previous = parts[part_index - 1]
                separator = _owned_separator(previous, part) if printed_lines else " "
                if separator is None:
                    return None
                gap = separator + gap
            rows.append((line, x, y, steps, ev, gap))
        if cursor != len(part.text):
            return None
    proposed = re.findall(r"([^\n]+)(\n*)", proposal)
    if len(rows) != len(proposed) or "".join(a + b for a, b in proposed) != proposal:
        return None
    pitch = next((d for _, _, _, steps, _, _ in rows for d in steps), None)
    # No advance estimate is needed when the proposed line starts are literal.
    measure_indent = not printed_lines or any(
        wanted != row[0] for (wanted, _), row in zip(proposed, rows, strict=True)
    )
    if measure_indent and (pitch is None or pitch <= 0):
        return None
    # The leftmost *drawn run* defines the display origin; literal spaces in
    # the run are not part of the proposed shift. Check every origin below.
    common = min(
        len(wanted) - len(row[0])
        for (wanted, _), row in zip(proposed, rows, strict=True)
    )
    if common < 0 or (common and (not relative_margin or len(rows) < 2)):
        return None
    # Check protected separators in the displayed columns: a shared proposal
    # prefix is not a change inside an already extracted multiline literal.
    if layout_version == 8 and not candidate(
        "".join(line[common:] + after for line, after in proposed)
    ):
        return None
    margin = None
    previous_y = None
    adopted = []
    unknown = []
    for i, ((line, x, y, steps, ev, space), (wanted, after)) in enumerate(
        zip(rows, proposed, strict=True)
    ):
        indent = len(wanted) - len(line)
        if (
            indent < 0
            or wanted != " " * indent + line
            or (
                measure_indent
                and not all(isclose(d, pitch or 0, abs_tol=0.02) for d in steps)
            )
        ):
            return None
        if previous_y is not None and y >= previous_y:
            return None
        if margin is None:
            margin = x - indent * (pitch or 0)
        if not isclose(x - indent * (pitch or 0), margin, abs_tol=0.02):
            return None
        if i:
            supplied = proposed[i - 1][1]
            supported = max(1, space.count("\n"))
            if len(supplied) < supported:
                return None
            adopted.append("\n" * supported)
            if len(supplied) > supported:
                unknown.append(
                    (
                        f"Additional blank-line count not evidenced by the original; extraction separator {space!r}, proposed {supplied!r}, adopted {adopted[-1]!r} between verified printed lines",
                        [rows[i - 1][4], ev],
                    )
                )
        adopted.append(wanted[common:])
        previous_y = y
    display = "".join(adopted)
    if proposed[-1][1] or ((common or layout_version >= 9) and not candidate(display)):
        return None
    if common:
        unknown.append(
            (
                f"Unverified common margin: measured line starts at pitch {pitch:.3f} pt; displayed relative to the leftmost drawn run, {common} spaces shallower than proposed on every line. The page does not establish the source file's absolute indentation.",
                [row[4] for row in rows],
            )
        )
    return display, unknown, common


def _disputed_lines(parts, proposal):
    fallback = [p.evidence() for p in parts]
    if len(parts) != 1:
        return fallback
    part = parts[0]
    cursor = 0
    evidence = []
    for line in proposal.splitlines():
        if not line.strip():
            continue
        m = re.match(r"\s*(" + re.escape(line.strip()) + ")", part.text[cursor:])
        if not m:
            return fallback
        start, end = part.start + cursor + m.start(1), part.start + cursor + m.end(1)
        evidence.append(
            fallback[0].model_copy(
                update={
                    "charspan": (start, end),
                    "position": f"characters {start}:{end}",
                }
            )
        )
        cursor += m.end()
    return evidence if evidence and not part.text[cursor:].strip() else fallback


def _outside_region(part, pages, region, *, partial=False):
    if part.prov["page_no"] != region.page or "bbox" not in part.prov:
        return False
    try:
        box = _DraftBox.model_validate(part.prov["bbox"])
    except ValueError:
        return False
    page = pages[region.page]
    top, bottom = (
        (page.height_points - box.t, page.height_points - box.b)
        if box.coord_origin == "BOTTOMLEFT"
        else (box.t, box.b)
    )
    if not (
        0 <= box.l < box.r <= page.width_points
        and 0 <= top < bottom <= page.height_points
    ):
        return False
    if partial:
        return not (
            region.left <= box.l * page.scale_x
            and box.r * page.scale_x <= region.right
            and region.top <= top * page.scale_y
            and bottom * page.scale_y <= region.bottom
        )
    return (
        box.r * page.scale_x <= region.left
        or box.l * page.scale_x >= region.right
        or bottom * page.scale_y <= region.top
        or top * page.scale_y >= region.bottom
    )


def _single_printed_occurrence(parts, page, read_original, read_image, figures):
    """Three adjacent extracted rows, with just the claimed drawing between anchors."""
    from PIL import Image, ImageChops

    rows = _local_text(
        [p.prov for p in parts],
        page,
        read_original,
        lambda tp, text, box: (text, box),
        strict_pdf=True,
    )
    if rows is None or [text for text, _ in rows] != [p.text for p in parts]:
        return False
    (_, above), (_, box), (_, below) = rows
    if not (above[1] > box[3] and box[1] > below[3]):
        return False
    sx, sy = page.scale_x, page.scale_y
    if not (
        isclose(page.width_points * sx, page.width_pixels, abs_tol=0.01)
        and isclose(page.height_points * sy, page.height_pixels, abs_tol=0.01)
    ):
        return False
    height = page.height_points

    def pixels(bounds):
        l, b, r, t = bounds
        return (
            floor(l * sx),
            floor((height - t) * sy),
            ceil(r * sx),
            ceil((height - b) * sy),
        )

    band = (0, below[1], page.width_points, above[3])
    left, top, right, bottom = pixels(band)
    if any(
        region.page == page.page and region.top < bottom and top < region.bottom
        for region in figures
    ):
        return False
    # Check the strip including the anchors, not just the gap between coarse boxes.
    with Image.open(BytesIO(read_image(page))) as image:
        if image.size != (page.width_pixels, page.height_pixels):
            return False
        ink = ImageChops.invert(image.crop((left, top, right, bottom)).convert("RGB"))
    probes = []
    for text, box in rows:
        l, t, r, b = pixels(box)
        row = ink.crop((l, t - top, r, b - top))
        extent = row.getbbox()
        if extent is None:
            return False
        x = extent[0]
        column = row.crop((x, 0, x + 1, row.height)).getbbox()
        if column is None:
            return False
        probes.append(((l + x + 0.5) / sx, height - (t + column[1] + 0.5) / sy))

    def drawings(tp, text, bounds):
        if text.splitlines() != [p.text for p in parts]:
            return None
        boxes = []
        for (wanted, box), (x, y) in zip(rows, probes, strict=True):
            index = tp.get_index(x, y, 1 / sx, 1 / sy)
            obj = tp.get_textobj(index) if index is not None else None
            if obj is None or not _paints_text(obj) or obj.extract() != wanted:
                return None
            l, b, r, t = obj.get_bounds()
            if not (box[0] <= l < r <= box[2] and box[1] <= b < t <= box[3]):
                return None
            boxes.append((l, b, r, t))
        return boxes

    l, b, r, t = band
    result = _local_text(
        [{"bbox": {"l": l, "b": b, "r": r, "t": t, "coord_origin": "BOTTOMLEFT"}}],
        page,
        read_original,
        drawings,
        strict_pdf=True,
    )
    if result is None:
        return False
    # Any extra visible mark (including raster-only content) makes ownership ambiguous.
    for bounds in result[0]:
        l, t, r, b = pixels(bounds)
        ink.paste((0, 0, 0), (l - 1, t - top - 1, r + 1, b - top + 1))
    return ink.getbbox() is None


def _bind_neighbors(
    blocks,
    targets,
    ranges,
    ranks,
    layout=None,
    image_owner=None,
    *,
    display_windows=False,
    report_conflict=None,
):
    extra_evidence = defaultdict(list)
    # Page-image evidence can describe a complete extraction item without a ref.
    # Bind only a one-to-one full-text match on that page.
    image_blocks = defaultdict(list)
    free_ranges = defaultdict(list)
    for b in blocks:
        if len(b.evidence) == 1 and b.evidence[0].ref is None:
            image_blocks[b.evidence[0].page, b.text].append(b)
    for i, (part, owner) in enumerate(ranges):
        if owner is None:
            free_ranges[part.rank[0], part.text].append(i)
    for signature, candidates in image_blocks.items():
        indices = free_ranges[signature]
        if len(candidates) == len(indices) == 1:
            key = candidates[0].block_id
            i = indices[0]
            part = ranges[i][0]
            if image_owner is not None and not image_owner(part, candidates[0]):
                continue
            ranges[i] = (part, key)
            ranks[key] = part.rank
            extra_evidence[key].append(part.evidence())

    def bind(key, left, right):
        for i in range(left, right + 1):
            part, owner = ranges[i]
            if owner is None:
                extra_evidence[key].append(part.evidence())
                ranges[i] = (part, key)
        ranks[key] = ranges[left][0].rank

    pending = {}
    claims = defaultdict(set)
    # Extend a listing anchor only when a unique contiguous same-page
    # window accounts for the entire listing, including token boundaries.
    for key, block in targets.items():
        if not isinstance(block, CodeBlock):
            continue
        anchors = [i for i, (_, owner) in enumerate(ranges) if owner == key]
        if not anchors:
            continue
        first, last = min(anchors), max(anchors)
        page = ranges[first][0].rank[0]
        if any(k != key or p.rank[0] != page for p, k in ranges[first : last + 1]):
            continue
        windows = []
        # Only nominate a window; its complete printed layout must prove the extension.
        content = "".join(block.text.split()) if display_windows else None
        left = first
        while True:
            right = last
            while True:
                original = _joined([p for p, _ in ranges[left : right + 1]])
                if len(original) > len(block.text):
                    break
                candidate = (
                    _printed_candidate
                    if image_owner
                    else _layout_candidate
                    if layout
                    else _line_formatting
                )
                matches = (
                    "".join(original.split()) == content
                    if display_windows
                    else original == block.text or candidate(original, block.text)
                )
                if matches:
                    parts = [p for p, _ in ranges[left : right + 1]]
                    if (
                        layout is None
                        or (left == first and right == last)
                        or layout(parts, block.text) is not None
                    ):
                        windows.append((left, right))
                right += 1
                if (
                    right == len(ranges)
                    or ranges[right][1]
                    or ranges[right][0].rank[0] != page
                ):
                    break
            if len(original) > len(block.text) and right == last:
                break
            left -= 1
            if left < 0 or ranges[left][1] or ranges[left][0].rank[0] != page:
                break
        if report_conflict is None:
            if len(windows) == 1:
                bind(key, *windows[0])
            continue
        # Compare all verified windows against the same ownership state before binding.
        for left, right in windows:
            for i in range(left, right + 1):
                if ranges[i][1] is None:
                    claims[i].add(key)
        if len(windows) == 1:
            pending[key] = windows[0]
    if report_conflict is None:
        return extra_evidence
    disputed = set()
    for i in sorted(claims):
        keys = claims[i]
        if len(keys) > 1:
            disputed.update(keys)
            report_conflict(
                "Competing code listings claim the same extraction range; retained original ownership",
                sorted(keys),
                [
                    part.evidence()
                    for j, (part, owner) in enumerate(ranges)
                    if j == i or owner in keys
                ],
            )
    for key, window in pending.items():
        if key not in disputed:
            bind(key, *window)
    return extra_evidence


def _place_word_label_lists(ranges, blocks, ranks, pages, containers):
    # Only an uninterrupted run of already assigned label/body pairs. The
    # correction order and the disjoint vertical bands must independently agree.
    labels = {
        b.block_id: b for b in blocks if isinstance(b, ListBlock) and b.marker.isalpha()
    }
    for listed, group in itertools.groupby(
        enumerate(ranges), lambda row: row[1][1] in labels
    ):
        if not listed:
            continue
        rows = list(group)
        pairs = defaultdict(list)
        for _, (part, key) in rows:
            pairs[key].append(part)
        if any(len(parts) != 2 for parts in pairs.values()):
            continue
        ordered = []
        for key, block in labels.items():
            if key not in pairs:
                continue
            parts = pairs[key]
            label = [p for p in parts if p.text == block.marker]
            if len(label) != 1:
                break
            ordered.extend(
                (p, key)
                for p in [label[0], next(p for p in parts if p is not label[0])]
            )
        if len(ordered) != len(rows) or ordered == [r for _, r in rows]:
            continue
        page = ordered[0][0].rank[0]
        orders = {p.order for p, _ in ordered}
        if len(orders) != max(orders) - min(orders) + 1 or any(
            containers[o] for o in orders
        ):
            continue
        bottom = 0
        lefts = {}
        for i, (p, key) in enumerate(ordered):
            if p.rank[0] != page or tuple(p.prov["charspan"]) != (p.start, p.end):
                break
            try:
                box = _DraftBox.model_validate(p.prov.get("bbox"))
            except ValueError:
                break
            height = pages[page].height_points
            t, b = (
                (height - box.t, height - box.b)
                if box.coord_origin == "BOTTOMLEFT"
                else (box.t, box.b)
            )
            if not (
                bottom <= t < b <= height
                and 0 <= box.l < box.r <= pages[page].width_points
                and isclose(lefts.setdefault(i % 2, box.l), box.l, abs_tol=0.02)
            ):
                break
            bottom = b
        else:
            positions = sorted(p.rank for _, (p, _) in rows)
            for index, ((_, key), rank) in enumerate(
                zip(ordered, positions, strict=True)
            ):
                if index % 2 == 0:
                    ranks[key] = rank
            ranges[rows[0][0] : rows[-1][0] + 1] = ordered


def adopt_owned_ranges(
    answer,
    raw,
    pages,
    read_original,
    read_image,
    *,
    items,
    verify_layout,
    printed_lines,
    printed_ranges,
    relative_margin,
    layout_version,
):
    by_page = {p.page: p for p in pages}
    issues = list(answer.uncertainties)
    blocks = list(answer.blocks)
    targets = {b.block_id: b for b in blocks}
    cells = {c.cell_id: c for b in blocks if isinstance(b, TableBlock) for c in b.cells}
    targets.update(cells)
    proposals = {b.block_id: b.text for b in blocks if isinstance(b, CodeBlock)}
    used = set(targets)
    selectors = defaultdict(list)
    detached = defaultdict(list)
    ranks = {}
    portions = defaultdict(list)
    body_refs = set()
    for key, item in targets.items():
        for e in item.evidence:
            selectors[e.page, e.ref].append((key, e))

    shared_captions = {}
    shared_rows = {}
    caption_listings = {}
    drawn_listings = {}
    drawn_groups = []

    def issue(reason, keys, evidence):
        issues.append(
            CorrectionUncertainty(
                reason=reason, target_ids=keys, evidence=evidence, origin="processing"
            )
        )

    invalid_listing_refs = {
        (e.page, e.ref)
        for b in answer.blocks
        if layout_version >= 13 and isinstance(b, CodeBlock)
        for e in b.evidence
        if e.ref and isinstance((node := items[e.ref]).get("text"), str)
        if any(
            not _valid_span(p.get("charspan"), node["text"])
            for p in node["prov"]
            if p["page_no"] == e.page
        )
    }
    ranges: list[tuple[_Portion, str | None]] = []
    barriers = set()
    containers = {}
    nested_members = defaultdict(list)
    for order, (ref, node, container) in enumerate(
        _reading_order(raw, nested=verify_layout)
    ):
        containers[order] = container
        if verify_layout and (
            ref.startswith(("#/pictures/", "#/tables/"))
            or node.get("content_layer", "body") != "body"
        ):
            barriers.add(order)
        for prov in node.get("prov", []):
            page = prov["page_no"]
            span = prov.get("charspan")
            text = node.get("text")
            if page not in answer.owned_pages:
                continue
            entries = selectors[page, ref]
            for key, e in entries:
                rank = (page, order, e.charspan[0] if e.charspan else 0)
                ranks[key] = min(ranks.get(key, rank), rank)
            if (
                node.get("content_layer", "body") != "body"
                or node.get("label")
                not in {
                    "text",
                    "paragraph",
                    "section_header",
                    "title",
                    "footnote",
                    "code",
                    "list_item",
                    "caption",
                    "formula",
                }
                or not isinstance(text, str)
            ):
                continue
            if not _valid_span(span, text) or not all(
                _valid_span(p.get("charspan"), text)
                and not (span[0] < p["charspan"][1] and p["charspan"][0] < span[1])
                for p in node["prov"]
                if p is not prov
            ):
                if layout_version >= 13:
                    invalid_listing_refs.add((page, ref))
                issue(
                    "Ambiguous extraction provenance; no range assigned",
                    [],
                    [
                        CorrectionEvidence(
                            page=page,
                            ref=ref,
                            position="extraction provenance",
                            reason="Invalid or overlapping spans",
                        )
                    ],
                )
                continue
            if container and node["label"] not in ("caption", "footnote"):
                part = _Portion(order, node, prov, *span)
                figures = [
                    targets[k]
                    for k, _ in selectors[page, container]
                    if isinstance(targets[k], PictureBlock)
                ]
                if len(figures) == 1:
                    figure = figures[0]
                    if not _outside_region(
                        part, by_page, figure.region, partial=True
                    ) and not any(
                        m.target_id in {k for k, _ in entries}
                        or (m.ref and _overlaps(m.ref, part.evidence()))
                        for m in figure.members
                    ):
                        nested_members[figure.block_id].append(
                            FigureMember(
                                ref=part.evidence(),
                                status="confirmed",
                                reason="Extraction child of the evidenced figure",
                            )
                        )
                elif not entries:
                    issue(
                        "Nested extraction has no independent text ownership",
                        [],
                        [part.evidence()],
                    )
                    continue
            body_refs.add((page, ref))
            start, end = span
            split = {}
            if (
                printed_ranges
                and not container
                and node["label"] == "code"
                and len(entries) == 2
                and len(node["prov"]) == 1
            ):
                (cap_key, cap_ev), (code_key, code_ev) = entries
                if (
                    layout_version >= 11
                    and getattr(targets[code_key], "target_id", None) == cap_key
                ):
                    (code_key, code_ev), (cap_key, cap_ev) = entries
                caption, code = targets[cap_key], targets[code_key]
                if (
                    getattr(caption, "kind", None) == "caption"
                    and isinstance(code, CodeBlock)
                    and (layout_version < 11 or caption.target_id in (None, code_key))
                    and (
                        layout_version < 11
                        or text.find(caption.text, start + 1, end) == -1
                    )
                    and all(
                        len(block.evidence) == 1 and ev.charspan is None
                        for block, ev in ((caption, cap_ev), (code, code_ev))
                    )
                    and caption.text
                    and text.startswith(caption.text, start, end)
                    and text.count(caption.text, start, end) == 1
                ):
                    boundary = start + len(caption.text)
                    tail = text[boundary:end]
                    flat_gap = (
                        re.match(r" +(?=\S)", tail) if layout_version >= 10 else None
                    )
                    if flat_gap:
                        tail = tail[flat_gap.end() :]
                    elif layout_version >= 9:
                        # Only the caption-to-code separator is outside code ownership.
                        tail = re.sub(r"^(?: *\r?\n| )", "", tail, count=1)
                    else:
                        tail = tail.lstrip(" ")
                    part = _Portion(order, node, prov, end - len(tail), end)
                    checked = (
                        _listing_layout(
                            [part],
                            code.text,
                            by_page[page],
                            read_original,
                            printed_lines=True,
                            relative_margin=relative_margin,
                            layout_version=layout_version,
                            caption=caption.text,
                        )
                        if tail
                        else None
                    )
                    if checked is not None:
                        if flat_gap:
                            # One space separates the roles. Keep as code only the
                            # remaining suffix supported by the measured first column.
                            display = checked[0]
                            indent = len(display) - len(display.lstrip(" "))
                            part.start -= min(flat_gap.end() - 1, indent)
                        split = {
                            cap_key: (start, boundary),
                            code_key: (part.start, end),
                        }
                        shared_captions[code_key] = caption.text
                        for key, ev in entries:
                            block = targets[key]
                            a, b = split[key]
                            selected = ev.model_copy(update={"charspan": (a, b)})
                            blocks[blocks.index(block)] = targets[key] = (
                                block.model_copy(update={"evidence": [selected]})
                            )
            if (
                not split
                and layout_version >= 11
                and not container
                and node["label"] == "code"
                and len(entries) == 2
            ):
                found = _printed_role_split(
                    _Portion(order, node, prov, start, end),
                    entries,
                    targets,
                    by_page[page],
                    read_original,
                )
                if found:
                    split, rows = found
                    for key, ev in entries:
                        block = targets[key]
                        a, b = split[key]
                        if isinstance(block, CodeBlock):
                            shared_rows[page, order, a] = rows
                        blocks[blocks.index(block)] = targets[key] = block.model_copy(
                            update={
                                "evidence": [
                                    e.model_copy(update={"charspan": (a, b)})
                                    if e == ev
                                    else e
                                    for e in block.evidence
                                ]
                            }
                        )
            if (
                not split
                and layout_version >= 15
                and len(entries) == 2
                and node.get("label") == "code"
            ):
                for cap_key, _ in entries:
                    caption = targets[cap_key]
                    if not isinstance(caption, TextBlock) or caption.kind != "caption":
                        continue
                    code = targets.get(caption.target_id)
                    if (
                        not isinstance(code, CodeBlock)
                        or len(code.evidence) < 2
                        or not any(k == code.block_id for k, _ in entries)
                    ):
                        continue
                    found = _caption_listing_parts(
                        caption,
                        code,
                        selectors,
                        raw,
                        items,
                        answer,
                        by_page[page],
                        read_original,
                        read_image,
                    )
                    if found is None:
                        continue
                    cap, groups, extracted = found
                    code_key = code.block_id
                    spans = {p.node["self_ref"]: (p.start, p.end) for p, _ in groups}
                    split = {cap_key: (cap.start, cap.end), code_key: spans[ref]}
                    caption_listings[code_key] = (cap_key, cap, groups, extracted)
                    for p, rows in groups:
                        shared_rows[p.rank] = rows
                    for block, evidence in (
                        (
                            caption,
                            [
                                caption.evidence[0].model_copy(
                                    update={"charspan": split[cap_key]}
                                )
                            ],
                        ),
                        (
                            code,
                            [
                                next(e for e in code.evidence if e.ref == r).model_copy(
                                    update={"charspan": span}
                                )
                                for r, span in spans.items()
                            ],
                        ),
                    ):
                        blocks[blocks.index(block)] = targets[block.block_id] = (
                            block.model_copy(update={"evidence": evidence})
                        )
            claims = []
            for key, e in entries:
                if (
                    isinstance(targets[key], (PictureBlock, TableBlock))
                    or e.cell_index is not None
                ):
                    continue
                if (
                    printed_lines
                    and isinstance(targets[key], CodeBlock)
                    and node["label"] in ("caption", "footnote")
                ):
                    detached[key].append(e)
                    continue
                a, b = e.charspan or span
                if key in split:
                    a, b = split[key]
                    # Other evidence can anchor this block earlier in Docling order.
                    rank = (page, order, a)
                    if layout_version < 11 or ranks[key][:2] >= rank[:2]:
                        ranks[key] = rank
                elif e.charspan is None and len({k for k, _ in entries}) > 1:
                    item = targets[key]
                    if (
                        printed_ranges
                        and isinstance(item, ListBlock)
                        and item.marker.isalpha()
                        and len(item.evidence) == 2
                        and item.evidence[0].ref == ref
                        and item.evidence[1].ref != ref
                    ):
                        matches = [
                            p
                            for p in node["prov"]
                            if p["page_no"] == page
                            and text[slice(*p["charspan"])] == item.marker
                        ]
                        if len(matches) == 1:
                            if matches[0] is not prov:
                                continue
                            narrow = e.model_copy(update={"charspan": (start, end)})
                            blocks[blocks.index(item)] = targets[key] = item.model_copy(
                                update={"evidence": [narrow, item.evidence[1]]}
                            )
                    proposal = targets[key].text
                    pos = text.find(proposal, start, end)
                    if (
                        proposal
                        and pos != -1
                        and text.find(proposal, pos + 1, end) == -1
                    ):
                        a, b = pos, pos + len(proposal)
                        ranks[key] = (page, order, a)
                if start <= a < b <= end and (a, b, key) not in claims:
                    claims.append((a, b, key))
            if (
                layout_version >= 15
                and not split
                and not container
                and any(c[0] < p[1] for p, c in itertools.pairwise(sorted(claims)))
            ):
                found = None
                for rule in (_shared_item_roles, _shared_listing_edge):
                    found = rule(
                        _Portion(order, node, prov, start, end),
                        entries,
                        targets,
                        selectors,
                        raw,
                        items,
                        answer,
                        by_page[page],
                        read_original,
                        read_image,
                    )
                    if found:
                        break
                if found:
                    split, listings, ordered = found
                    drawn_groups.append(ordered)
                    claims = sorted((a, b, k) for k, (a, b) in split.items())
                    for key, (a, b) in split.items():
                        if ranks[key][:2] >= (page, order):
                            ranks[key] = (page, order, a)
                    for key, (groups, extracted) in listings.items():
                        drawn_listings[key] = (groups, extracted)
                        for p, rows in groups:
                            shared_rows[p.rank] = rows
                    for key in split:
                        block = targets[key]
                        bounds = defaultdict(list)
                        for p, _ in listings.get(key, ([], None))[0]:
                            bounds[p.node["self_ref"]] += [p.start, p.end]
                        bounds[ref] = list(split[key])
                        blocks[blocks.index(block)] = targets[key] = block.model_copy(
                            update={
                                "evidence": [
                                    e.model_copy(
                                        update={
                                            "charspan": (
                                                min(bounds[e.ref]),
                                                max(bounds[e.ref]),
                                            )
                                        }
                                    )
                                    if e.ref in bounds
                                    else e
                                    for e in block.evidence
                                ]
                            }
                        )
            cursor = start
            for a, b, key in sorted(claims) + [(end, end, None)]:
                if a < cursor:
                    raise ValueError("Overlapping ownership of extraction text")
                if cursor < a and text[cursor:a].strip():
                    part = _Portion(order, node, prov, cursor, a)
                    ranges.append((part, None))
                if key is not None:
                    ranges.append((_Portion(order, node, prov, a, b), key))
                cursor = b

    for code_key, (cap_key, cap, groups, _) in caption_listings.items():
        # Only permute this complete, contiguous group. All ordinary claims, including
        # explicit spans and the double-ownership check, must have survived unchanged.
        ordered = [(cap, cap_key), *((p, code_key) for p, _ in groups)]
        indices = [i for i, (_, k) in enumerate(ranges) if k in (cap_key, code_key)]
        if (
            not indices
            or indices != list(range(indices[0], indices[0] + len(ordered)))
            or sorted(
                (ranges[i][0].rank, ranges[i][0].end, ranges[i][1]) for i in indices
            )
            != sorted((p.rank, p.end, k) for p, k in ordered)
        ):
            raise ValueError("Cannot place evidenced caption and listing")
        ranges[indices[0] : indices[-1] + 1] = ordered
        ranks[cap_key] = (cap.rank[0], min(p.order for p, _ in ordered), -1)

    def covered(owned):
        return {
            (p.node["self_ref"], i, key)
            for p, key in owned
            for i in range(p.start, p.end)
            if not p.node["text"][i].isspace()
        }

    for ordered in drawn_groups:
        # Reorder only the proven items, as drawn; every character keeps its owner.
        orders = {p.order for p, _ in ordered}
        indices = [i for i, (p, _) in enumerate(ranges) if p.order in orders]
        if (
            not indices
            or indices != list(range(indices[0], indices[0] + len(indices)))
            or covered(ranges[i] for i in indices) != covered(ordered)
        ):
            raise ValueError("Cannot place evidenced caption and listing")
        ranges[indices[0] : indices[-1] + 1] = ordered

    if printed_ranges:
        _place_word_label_lists(ranges, blocks, ranks, by_page, containers)

    unsupported = set()
    for key, notes in detached.items():
        block = targets[key]
        remaining = [e for e in block.evidence if e not in notes]
        if remaining:
            blocks[blocks.index(block)] = targets[key] = block.model_copy(
                update={"evidence": remaining}
            )
            anchors = [part.rank for part, owner in ranges if owner == key]
            if anchors:
                ranks[key] = min(anchors)
            issue(
                "Independent caption or footnote not adopted as part of the code example",
                [key],
                notes,
            )
        else:
            # No independent listing was evidenced. Preserve the actual note's
            # role, source and ID rather than inventing an image-only listing.
            for i, (part, owner) in enumerate(ranges):
                if owner is None and any(_overlaps(e, part.evidence()) for e in notes):
                    restored = _missing_block(part, key)
                    blocks[blocks.index(block)] = targets[key] = restored
                    ranges[i] = (part, key)
                    ranks[key] = part.rank
                    issue(
                        f"Only note evidence for proposed code {block.text!r}; retained {restored.kind} {part.text!r}",
                        [key],
                        notes,
                    )
                    break
            else:
                blocks.remove(block)
                targets.pop(key)
                unsupported.add(key)
                issue(
                    f"No independent code ownership for proposal {block.text!r}; kept separately evidenced notes",
                    [],
                    notes,
                )

    def layout(parts, proposal, caption=None):
        if separated(parts) or (
            printed_lines
            and any(p.node["label"] in ("footnote", "caption") for p in parts)
        ):
            return None
        return _listing_layout(
            parts,
            proposal,
            by_page[parts[0].rank[0]],
            read_original,
            printed_lines=printed_lines,
            relative_margin=relative_margin,
            layout_version=layout_version,
            caption=caption,
            shared_rows=shared_rows,
        )

    def separated(parts, *, code=True):
        return (
            (
                printed_lines
                and any(
                    _owned_separator(a, b, code=code) is None
                    for a, b in itertools.pairwise(parts)
                )
            )
            or verify_layout
            and (
                len({containers[p.order] for p in parts}) != 1
                or any(parts[0].order < o < parts[-1].order for o in barriers)
            )
        )

    def image_owner(part, block):
        def same_occurrence():
            i = blocks.index(block)
            j = next(n for n, (p, _) in enumerate(ranges) if p is part)
            if i == 0 or j == 0 or i + 1 == len(blocks) or j + 1 == len(ranges):
                return False
            neighbors = blocks[i - 1 : i + 2]
            window = ranges[j - 1 : j + 2]
            parts = [p for p, _ in window]
            if any(
                b.kind != "body"
                or p.node["label"] not in ("text", "paragraph")
                or containers[p.order]
                or p.rank[0] != part.rank[0]
                or [p.start, p.end] != p.prov["charspan"]
                for b, p in zip(neighbors, parts, strict=True)
            ):
                return False
            for anchor, (other, owner) in (
                (neighbors[0], window[0]),
                (neighbors[2], window[2]),
            ):
                if (
                    owner != anchor.block_id
                    or len(anchor.evidence) != 1
                    or anchor.evidence[0].ref != other.node["self_ref"]
                    or anchor.text != other.text
                ):
                    return False
            # No intervening extraction node (including a figure, cell or exclusion).
            if [p.order for p in parts] != [part.order - 1, part.order, part.order + 1]:
                return False
            if any(
                isinstance(b, PictureBlock)
                and b.region.page == part.rank[0]
                and any(
                    m.target_id in {a.block_id for a in neighbors} for m in b.members
                )
                for b in blocks
            ):
                return False
            return _single_printed_occurrence(
                parts,
                by_page[part.rank[0]],
                read_original,
                read_image,
                (b.region for b in blocks if isinstance(b, PictureBlock)),
            )

        if same_occurrence():
            return True
        issue(
            "Image and extraction not proven to be the same printed occurrence; retained both",
            [block.block_id],
            [*block.evidence, part.evidence()],
        )
        return False

    extra_evidence = _bind_neighbors(
        blocks,
        targets,
        ranges,
        ranks,
        layout if verify_layout else None,
        image_owner if printed_lines else None,
        display_windows=layout_version >= 9,
        report_conflict=issue if layout_version >= 10 else None,
    )
    # Joining non-adjacent body refs would move intervening material. Keep each
    # evidenced fragment at its own rank, without inventing a continuation.
    indices = defaultdict(list)
    for i, (_, owner) in enumerate(ranges):
        if owner is not None:
            indices[owner].append(i)
    excluded_refs = {(e.page, e.ref) for x in answer.exclusions for e in x.evidence}

    def omitted_items(key, positions):
        """Unclaimed items between evidenced ranges, each at one place in the proposal."""
        block = targets[key]
        filled = [positions[0]]
        for i, j in itertools.pairwise(positions):
            middle = range(i + 1, j)
            # Page, container and owner are checked by separated() and selectors.
            if middle and any(
                selectors[ranges[k][0].rank[0], ranges[k][0].node["self_ref"]]
                or (ranges[k][0].rank[0], ranges[k][0].node["self_ref"])
                in excluded_refs
                for k in middle
            ):
                return None
            if middle:
                pattern = r"\s+".join(
                    re.escape(token)
                    for k in (i, *middle, j)
                    for token in ranges[k][0].text.split()
                )
                if len(list(re.finditer(pattern, block.text))) != 1:
                    return None
            filled.extend([*middle, j])
        return filled if len(filled) > len(positions) else None

    for key, positions in indices.items():
        parts = [ranges[i][0] for i in positions]
        if positions[-1] - positions[0] + 1 == len(positions) and not separated(
            parts, code=isinstance(targets[key], CodeBlock)
        ):
            continue
        filled = omitted_items(key, positions) if layout_version >= 15 else None
        if filled and not separated(
            [ranges[i][0] for i in filled], code=isinstance(targets[key], CodeBlock)
        ):
            block = targets[key]
            evidence = list(block.evidence)
            for i in sorted(set(filled) - set(positions)):
                part = ranges[i][0]
                ranges[i] = (part, key)
                previous = ranges[i - 1][0].node["self_ref"]
                at = max(n for n, e in enumerate(evidence) if e.ref == previous)
                evidence.insert(at + 1, part.evidence())
                issue(
                    "Evidence omitted an intervening extraction item; included at its unique place in the correction",
                    [key],
                    [part.evidence()],
                )
            blocks[blocks.index(block)] = targets[key] = block.model_copy(
                update={"evidence": evidence}
            )
            continue
        block = targets[key]
        split_code = printed_lines and isinstance(block, CodeBlock)
        image_evidence = [e for e in block.evidence if e.ref is None]
        if (
            not ((isinstance(block, TextBlock) and block.kind == "body") or split_code)
            or block.incoming
            or block.outgoing
            or (image_evidence and not split_code)
        ):
            raise ValueError(
                "Cannot place non-adjacent evidence without changing membership"
            )
        if any(
            (e.page, e.ref) not in {(p.rank[0], p.node["self_ref"]) for p in parts}
            for e in block.evidence
            if e.ref is not None
        ):
            raise ValueError("Non-adjacent block contains additional evidence")
        if image_evidence:
            issue(
                "Image-only evidence cannot be assigned to a separated code fragment",
                [key],
                image_evidence,
            )
        issue(
            f"Separated non-adjacent extraction ranges; correction {block.text!r}",
            [key],
            [p.evidence() for p in parts],
        )
        for number, (i, part) in enumerate(zip(positions, parts, strict=True)):
            if number and isinstance(block, CodeBlock):
                new = f"{key}-{part.order}-{part.start}"
                while new in used:
                    new += "-"
                used.add(new)
                fragment = block.model_copy(
                    update={
                        "block_id": new,
                        "text": part.text,
                        "evidence": [part.evidence()],
                    }
                )
                blocks.append(fragment)
                targets[new] = fragment
                ranges[i] = (part, new)
                ranks[new] = part.rank
            elif number:
                ranges[i] = (part, None)
            else:
                replacement = block.model_copy(
                    update={"text": part.text, "evidence": [part.evidence()]}
                )
                blocks[blocks.index(block)] = targets[key] = replacement
                ranks[key] = part.rank
    owners = []
    prev = None
    for part, owner in ranges:
        if owner is None:
            if (
                prev is not None
                and prev.node is part.node
                and (
                    not printed_lines
                    or _owned_separator(prev, part, code=part.node["label"] == "code")
                    is not None
                )
                and prev.end <= part.start
                and (
                    not (gap := part.node["text"][prev.end : part.start])
                    or gap.isspace()
                )
            ):
                b = blocks[-1]
                blocks[-1] = b.model_copy(
                    update={
                        "text": b.text + gap + part.text,
                        "evidence": [*b.evidence, part.evidence()],
                    }
                )
            else:
                new = f"extraction-{part.order}-{part.start}"
                while new in used:
                    new += "-"
                used.add(new)
                blocks.append(_missing_block(part, new))
                ranks[new] = part.rank
            issue(
                "Readable owned range omitted by correction",
                [blocks[-1].block_id],
                [part.evidence()],
            )
            owners.append((part, blocks[-1].block_id))
            prev = part
        else:
            owners.append((part, owner))
            portions[owner].append(part)
            prev = None

    def recover_listing(item, parts):
        page = by_page[item.evidence[0].page]
        refs = [e.ref for e in item.evidence]
        valid = {p.node["self_ref"]: p for p in parts}
        if len(set(refs)) != len(refs) or any(
            e.page != page.page
            or e.ref is None
            or (
                e.charspan is not None
                and (
                    e.ref not in valid
                    or e.charspan != (valid[e.ref].start, valid[e.ref].end)
                )
            )
            or e.cell_index is not None
            or len(selectors[e.page, e.ref]) != 1
            for e in item.evidence
        ):
            return None
        # A second role, image-only claim or unlocated exclusion is not a free row.
        if any(
            e.page == page.page and e.ref in {*refs, None}
            for b in answer.exclusions
            for e in b.evidence
        ) or any(
            not isinstance(figure := targets.get(k), PictureBlock)
            or figure.region.page != page.page
            for k, _ in selectors[page.page, None]
        ):
            return None
        selected = []
        for order, (ref, node, container) in enumerate(
            _reading_order(raw, nested=True)
        ):
            if ref not in refs:
                continue
            if (
                container
                or node.get("label") not in ("code", "list_item")
                or node.get("content_layer", "body") != "body"
                or len(node.get("prov", [])) != 1
                or node["prov"][0]["page_no"] != page.page
                or (selected and order != selected[-1][0] + 1)
            ):
                return None
            selected.append((order, node))
        if (
            [n["self_ref"] for _, n in selected] != refs
            or set(valid)
            != {ref for ref in refs if (page.page, ref) not in invalid_listing_refs}
            or any([p.start, p.end] != p.prov["charspan"] for p in parts)
        ):
            return None

        try:
            boxes = [_page_box(n["prov"][0], page) for _, n in selected]
            l, b, r, t = (
                min(v[0] for v in boxes),
                min(v[1] for v in boxes),
                max(v[2] for v in boxes),
                max(v[3] for v in boxes),
            )
            for figure in blocks:
                if (
                    not isinstance(figure, PictureBlock)
                    or figure.region.page != page.page
                ):
                    continue
                region = figure.region
                if (
                    region.left < r * page.scale_x
                    and l * page.scale_x < region.right
                    and region.top < (page.height_points - b) * page.scale_y
                    and (page.height_points - t) * page.scale_y < region.bottom
                ) or any(
                    m.target_id == item.block_id or (m.ref and m.ref.ref in refs)
                    for m in figure.members
                ):
                    return None
            neighbors = (
                p
                for ref, node in items.items()
                if ref not in refs
                for p in node["prov"]
                if p["page_no"] == page.page
            )
            if any(
                x < r and l < right and bottom < t and b < top
                for x, bottom, right, top in (_page_box(p, page) for p in neighbors)
            ):
                return None
        except ValueError:
            return None
        return _drawn_listing(
            item, [n for _, n in selected], valid, page, read_original, read_image
        )

    replacements = {}
    verified_margins = {}
    drawn_codes = set()
    for b in blocks:
        if isinstance(b, CodeBlock) and any(
            (e.page, e.ref) in invalid_listing_refs for e in b.evidence
        ):
            portions.setdefault(b.block_id, [])
    for key, parts in portions.items():
        item = targets[key]
        original = _joined(parts) if parts else ""
        if original == item.text and not (
            printed_lines and isinstance(item, CodeBlock)
        ):
            continue
        if verify_layout and isinstance(item, CodeBlock):
            if any((e.page, e.ref) in invalid_listing_refs for e in item.evidence):
                checked_rows = recover_listing(item, parts)
                if checked_rows is not None:
                    display, locations, differences = checked_rows
                    drawn_codes.add(key)
                    replacements[key] = item.model_copy(
                        update={
                            "text": display,
                            "evidence": [*item.evidence, *locations],
                        }
                    )
                    for reason, evidence in differences:
                        issue(reason, [key], evidence)
                    continue
                issue(
                    "Proposed listing rows not verified in the owned drawing",
                    [key] if parts else [],
                    item.evidence,
                )
                if not parts:
                    blocks.remove(item)
                    targets.pop(key)
                    unsupported.add(key)
                    continue
                # Declined boxes neither position the fallback nor become adopted targets.
                ranks[key] = min(p.rank for p in parts)
                item = item.model_copy(
                    update={"evidence": [p.evidence() for p in parts]}
                )
            checked = layout(parts, item.text, shared_captions.get(key))
            if checked is not None:
                text, differences, common = checked
                verified_margins[key] = common
                replacements[key] = item.model_copy(update={"text": text})
                for reason, evidence in differences:
                    issue(reason, [key], evidence)
                continue
            if key in caption_listings or key in drawn_listings:
                extracted = (
                    caption_listings[key][3]
                    if key in caption_listings
                    else drawn_listings[key][1]
                )
                original = "".join(text for text, _ in extracted)
                issue(
                    f"Unverified listing formatting; proposed {item.text!r}, retained drawn boundaries and extraction {original!r}",
                    [key],
                    [loc for _, loc in extracted],
                )
                replacements[key] = item.model_copy(update={"text": original})
                continue
            replacements[key] = item.model_copy(update={"text": original})
            issue(
                f"Line boundary or indentation not corroborated; adopted extraction {original!r} rather than correction {item.text!r}",
                [key],
                _disputed_lines(parts, item.text),
            )
            continue
        if (
            not verify_layout
            and getattr(item, "kind", None) == "code"
            and _line_formatting(original, item.text)
        ):
            continue
        if (
            len(parts) == 1
            and isinstance(item, TextBlock)
            and item.kind == "body"
            and (
                parts[0].node["label"] == "text"
                or (printed_lines and parts[0].node["label"] == "paragraph")
            )
            and tuple(parts[0].prov["charspan"]) == (parts[0].start, parts[0].end)
            and _supported_space(
                original,
                item.text,
                parts[0].prov,
                by_page[parts[0].rank[0]],
                read_original,
                verify_layout,
                printed_lines,
            )
        ):
            continue
        if (
            layout_version >= 14
            and len(parts) == 1
            and isinstance(item, TextBlock)
            and item.kind == "body"
            and len(item.evidence) == 1
            and item.evidence[0].ref == parts[0].node["self_ref"]
        ):
            part = parts[0]
            independent = (
                part.node["label"] in ("text", "paragraph")
                and not containers[part.order]
                and tuple(part.prov["charspan"]) == (part.start, part.end)
                and len(selectors[part.rank[0], part.node["self_ref"]]) == 1
                and not any(
                    isinstance(fig, PictureBlock)
                    and fig.region.page == part.rank[0]
                    and (
                        not _outside_region(part, by_page, fig.region)
                        or any(
                            m.target_id == key
                            or (m.ref and _overlaps(m.ref, part.evidence()))
                            for m in fig.members
                        )
                    )
                    for fig in blocks
                )
            )
            punctuation = _body_punctuation(
                part,
                item,
                by_page[part.rank[0]],
                read_original,
                read_image,
                independent,
                items,
                issue,
            )
            if punctuation is not None:
                replacements[key] = punctuation
                continue
        if any((e.page, e.ref) not in body_refs for e in item.evidence):
            tables = [e for e in item.evidence if (e.ref or "").startswith("#/tables/")]
            cells = [
                cell.get("text", "")
                for e in tables
                for cell in items[e.ref].get("data", {}).get("table_cells", [])
            ]
            if layout_version >= 15 and any(
                _occurrences(p.text, cell) for p in parts for cell in cells
            ):
                sole = all(
                    {k for k, _ in selectors[e.page, e.ref]} == {key} for e in tables
                )
                if not sole or _cell_repeats(parts, item, cells) is None:
                    raise ValueError(
                        "Cannot isolate extraction range from other block evidence"
                    )
                continue
            if verify_layout:
                # Anchor the complete extracted phrase, allowing a disputed
                # decimal token, never a prefix of a longer number or word.
                if printed_lines:
                    matches = _mixed_phrase_matches(original, item.text)
                else:
                    terms = re.split(r"(\d+)", original)
                    pattern = "".join(
                        r"\d+" if i % 2 else re.escape(t) for i, t in enumerate(terms)
                    )
                    matches = list(
                        re.finditer(r"(?<!\w)" + pattern + r"(?!\w)", item.text)
                    )
                if len(matches) == 1:
                    match = matches[0]
                    if match[0] != original:
                        replacements[key] = item.model_copy(
                            update={
                                "text": item.text[: match.start()]
                                + original
                                + item.text[match.end() :]
                            }
                        )
                        issue(
                            f"Adopted extraction {original!r} rather than correction {match[0]!r}",
                            [key],
                            [p.evidence() for p in parts],
                        )
                    continue
            elif original in item.text:
                continue
            located = (
                _fragment_places(parts, item, by_page, read_original)
                if layout_version >= 15 and printed_lines
                else None
            )
            if located is not None:
                text = item.text
                for part, extracted, match in reversed(located):
                    if match[0] == extracted:
                        continue
                    text = text[: match.start()] + extracted + text[match.end() :]
                    issue(
                        f"Adopted extraction {extracted!r} rather than correction {match[0]!r}",
                        [key],
                        [part.evidence()],
                    )
                if text != item.text:
                    replacements[key] = item.model_copy(update={"text": text})
                continue
            raise ValueError(
                "Cannot isolate extraction range from other block evidence"
            )
        replacements[key] = item.model_copy(update={"text": original})
        issue(
            f"Extraction {original!r} differs from correction {item.text!r}",
            [key],
            [p.evidence() for p in parts],
        )
    if unsupported:
        # Keep original uncertainty locations, not references to a nonexistent example.
        issues = [
            q.model_copy(
                update={"target_ids": [k for k in q.target_ids if k not in unsupported]}
            )
            for q in issues
        ]

    exclusions = []
    for excluded in answer.exclusions:
        disputed = [e for e in excluded.evidence if (e.page, e.ref) in body_refs]
        if not disputed:
            exclusions.append(excluded)
            continue
        issue("Exclusion conflicts with owned extraction", [], disputed)
        rest = [e for e in excluded.evidence if e not in disputed]
        if rest:
            exclusions.append(excluded.model_copy(update={"evidence": rest}))
    result = []
    for b in blocks:
        if (
            printed_lines
            and isinstance(b, CodeBlock)
            and b.block_id in targets
            and b.block_id not in portions
        ):
            issue(
                "No unique owned extraction range for the proposed display lines; retained the evidenced code without confirming its layout",
                [b.block_id],
                b.evidence,
            )
        if isinstance(b, PictureBlock):
            members = []
            for member in [*b.members, *nested_members[b.block_id]]:
                if member.target_id in unsupported:
                    issue(
                        "Figure membership referred to an unsupported listing",
                        [b.block_id],
                        b.evidence,
                    )
                    continue
                if member.status == "confirmed" and any(
                    (
                        key == member.target_id
                        or (member.ref and _overlaps(member.ref, part.evidence()))
                    )
                    and _outside_region(part, by_page, b.region, partial=verify_layout)
                    for part, key in owners
                ):
                    member = member.model_copy(
                        update={
                            "status": "uncertain",
                            "reason": "Claimed figure member has owned provenance outside figure region",
                        }
                    )
                members.append(member)
            b = b.model_copy(update={"members": members})
        if isinstance(b, TableBlock):
            b = b.model_copy(
                update={"cells": [replacements.get(c.cell_id, c) for c in b.cells]}
            )
        b = replacements.get(b.block_id, b)
        if isinstance(b, TextBlock) and b.target_id in unsupported:
            issue(
                "Annotation target is not an evidenced code example; retained unlinked",
                [b.block_id],
                b.evidence,
            )
            b = b.model_copy(update={"target_id": None})
        if (
            printed_lines
            and isinstance(b, CalloutBlock)
            and (
                b.code_id in replacements
                or not isinstance(targets.get(b.code_id), CodeBlock)
            )
        ):
            proposed = proposals[b.code_id].splitlines()
            adopted = (
                replacements[b.code_id].text.splitlines()
                if isinstance(targets.get(b.code_id), CodeBlock)
                else []
            )
            if proposed != adopted:
                before = [i for i, line in enumerate(proposed, 1) if line.strip()]
                after = [i for i, line in enumerate(adopted, 1) if line.strip()]
                # Never infer a shift by stripping each line of a fallback extraction.
                shift = verified_margins.get(b.code_id) if layout_version >= 9 else 0
                if all(n in before for n in b.lines) and (
                    b.code_id in drawn_codes
                    or (
                        shift is not None
                        and [proposed[n - 1] for n in before]
                        == [" " * shift + adopted[n - 1] for n in after]
                    )
                ):
                    b = b.model_copy(
                        update={"lines": [after[before.index(n)] for n in b.lines]}
                    )
                else:
                    issue(
                        f"Callout {b.marker!r} proposed for lines {b.lines} of {b.code_id}; no unique adopted display line. Retained as unlinked text.",
                        [k for k in (b.block_id, b.code_id) if k in targets],
                        b.evidence,
                    )
                    b = TextBlock(
                        **b.model_dump(
                            exclude={"kind", "lines", "marker", "code_id", "text"}
                        ),
                        kind="body",
                        text=f"{b.marker} {b.text}",
                    )
        if extra_evidence[b.block_id]:
            b = b.model_copy(
                update={"evidence": [*b.evidence, *extra_evidence[b.block_id]]}
            )
        result.append(b)
    # Image-only blocks have no raw order. Keep their order with respect to
    # the preceding correction anchor instead of pushing them behind all text.
    previous = {}
    for b in result:
        page = min(e.page for e in b.evidence)
        ranks.setdefault(b.block_id, previous.get(page, (page, -1, 0)))
        previous[page] = ranks[b.block_id]
    result.sort(key=lambda b: ranks[b.block_id])
    return answer.model_copy(
        update={"blocks": result, "uncertainties": issues, "exclusions": exclusions}
    )
