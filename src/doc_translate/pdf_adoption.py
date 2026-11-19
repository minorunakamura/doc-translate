"""Reconcile owned Docling reading-order ranges with validated local proposals."""

import ctypes
import itertools
import re
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
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


def _local_text(provenances, page, read_original, inspect, *, strict_pdf):
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
                    return None
                p = pdf[page.page - 1]
                width, height = p.get_size()
                if (
                    p.get_rotation()
                    or p.get_bbox()[:2] != (0, 0)
                    or not isclose(width, page.width_points, abs_tol=0.01)
                    or not isclose(height, page.height_points, abs_tol=0.01)
                ):
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


def _line_origins(textpage, local, bounds, *, printed_lines):
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
                elif generated != 0 or not _paints_text(
                    native.FPDFText_GetTextObject(textpage, i)
                ):
                    return None
            positions.append((x.value, y.value))
        row = 0
        for end in range(1, len(positions) + 1):
            if end < len(positions) and isclose(
                positions[row][1], positions[end][1], abs_tol=0.01
            ):
                continue
            if not printed_lines and end < len(positions):
                return None
            x, y = positions[row]
            steps = [b[0] - a[0] for a, b in itertools.pairwise(positions[row:end])]
            if printed_lines and any(step <= 0 for step in steps):
                return None
            result.append((content[row:end], x, y, steps))
            row = end
        index += len(line)
    return result


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
):
    """Validate proposed lines individually against the complete local text runs."""
    original = _joined(parts)

    def candidate(text):
        if printed_lines:
            return _printed_candidate(original, text, relative_margin=relative_margin)
        return _layout_candidate(original, text)

    if (layout_version < 8 and not candidate(proposal)) or any(
        p.rank[0] != page.page
        or (caption is None and [p.start, p.end] != p.prov["charspan"])
        or "bbox" not in p.prov
        for p in parts
    ):
        return None
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

    def issue(reason, keys, evidence):
        issues.append(
            CorrectionUncertainty(
                reason=reason, target_ids=keys, evidence=evidence, origin="processing"
            )
        )

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
                caption, code = targets[cap_key], targets[code_key]
                if (
                    getattr(caption, "kind", None) == "caption"
                    and isinstance(code, CodeBlock)
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
                    ranks[key] = (page, order, a)
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

    if unsupported:
        # Keep original uncertainty locations, not references to a nonexistent example.
        issues = [
            q.model_copy(
                update={"target_ids": [k for k in q.target_ids if k not in unsupported]}
            )
            for q in issues
        ]

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
    for key, positions in indices.items():
        parts = [ranges[i][0] for i in positions]
        if positions[-1] - positions[0] + 1 == len(positions) and not separated(
            parts, code=isinstance(targets[key], CodeBlock)
        ):
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
    replacements = {}
    verified_margins = {}
    for key, parts in portions.items():
        item = targets[key]
        original = _joined(parts)
        if original == item.text and not (
            printed_lines and isinstance(item, CodeBlock)
        ):
            continue
        if verify_layout and isinstance(item, CodeBlock):
            checked = layout(parts, item.text, shared_captions.get(key))
            if checked is not None:
                text, differences, common = checked
                verified_margins[key] = common
                replacements[key] = item.model_copy(update={"text": text})
                for reason, evidence in differences:
                    issue(reason, [key], evidence)
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
        if any((e.page, e.ref) not in body_refs for e in item.evidence):
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
            raise ValueError(
                "Cannot isolate extraction range from other block evidence"
            )
        replacements[key] = item.model_copy(update={"text": original})
        issue(
            f"Extraction {original!r} differs from correction {item.text!r}",
            [key],
            [p.evidence() for p in parts],
        )
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
                if (
                    shift is not None
                    and all(n in before for n in b.lines)
                    and [proposed[n - 1] for n in before]
                    == [" " * shift + adopted[n - 1] for n in after]
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
