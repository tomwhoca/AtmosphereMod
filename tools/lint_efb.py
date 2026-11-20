"""Check the redesigned EFB app's UI (efb/PackageSources/AtmoWeather/src/ui) for what the sim's tablet engine can't
do. The harness runs modern Chrome and shows none of it; the tablet runs CoherentGT 2.0 (WebKit 604, "Chrome/49").
Each rule says where it's from: measured in the stock EFB's view (notes/efb-ui.md, 2026-11-20), or in EFB+'s
measurements of the same engine (EFBplus-Framework docs/MEASURED-FACTS.md).

    python tools/lint_efb.py        # exit 1 on a finding

CSS rules apply to the stylesheets (files named styles*.ts) and to inline styles (style: "..." strings); script
rules to all of src/ui. Syntax (?. ?? class fields) needs no rule: the build's es2017 target rewrites it.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "efb" / "PackageSources" / "AtmoWeather" / "src" / "ui"

CSS_RULES = [
    (r"(?<![-\w])gap\s*:|(?<![-\w])(row|column)-gap\s*:", "gap is ignored on flex and grid (measured); use margins, or grid-gap on a grid"),
    (r"position\s*:\s*sticky", "position: sticky computes to static (measured)"),
    (r"\bclamp\(|(?<![-\w.])(min|max)\(", "clamp(), min() and max() drop the whole declaration (measured clamp; EFB+ min)"),
    (r"aspect-ratio\s*:", "aspect-ratio has no effect (measured)"),
    (r"(?<![-\w])inset\s*:", "inset isn't supported (measured): left/top/right/bottom"),
    (r"(?<![-\w])backdrop-filter\s*:", "backdrop-filter isn't supported (measured)"),
    (r"touch-action\s*:|overscroll-behavior|scroll-behavior\s*:", "touch-action, overscroll-behavior and scroll-behavior aren't supported (measured)"),
    (r":(is|where|has|focus-visible|focus-within)\(|:focus-visible|:focus-within", "newer selectors (:is, :where, :has, :focus-visible/within): assumed missing in WebKit 604, unmeasured"),
    (r"@container|@layer|color-mix\(|\benv\(", "@container, @layer, color-mix(), env(): newer than the engine (assumed, unmeasured)"),
    (r"#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{4}\b", "#rrggbbaa / #rgba colours: use rgba() (assumed, unmeasured)"),
    (r"\b\d+(\.\d+)?(dvh|svh|lvh|dvw|svw|lvw|cqw|cqh)\b", "dynamic and container units: newer than the engine (assumed)"),
    (r"(?<![-\w])(rotate|scale|translate)\s*:", "individual transform properties: use transform (assumed)"),
    (r"place-(items|content|self)\s*:", "place-* shorthands: assumed missing, use align-/justify-"),
    (r"display\s*:\s*flow-root", "display: flow-root: assumed missing"),
]
# The tablet styles these class names globally (measured 2026-11-20 in the stock EFB: hidden display:none, disabled a
# greyscale filter, slider display:flex, button and title colours and fonts): ours must not use them.
EFB_CLASSES = ("hidden", "disabled", "slider", "button", "title")

JS_RULES = [
    (r"\bResizeObserver\b", "ResizeObserver is undefined (measured): the host's onPanel tells the size"),
    (r"\bPointerEvent\b|['\"]pointer(down|move|up|enter|leave|cancel)['\"]|\.onpointer", "no pointer events (measured): mouse events only"),
    (r"\bIntersectionObserver\b", "IntersectionObserver: assumed missing (WebKit 604)"),
    (r"\btouch(start|move|end)\b", "touch events: the tablet is driven by the mouse (assumed)"),
]


def strings_after_style(text: str) -> list[tuple[int, str]]:
    """Inline styles: the string after `style:` or in setAttribute("style", ...) / .cssText = ..."""
    out = []
    for m in re.finditer(r"""(style\s*:\s*|setAttribute\(\s*["']style["']\s*,\s*|cssText\s*=\s*)(["'`])((?:\\.|(?!\2).)*)\2""",
                         text, re.S):
        out.append((m.start(3), m[3]))
    return out


def line_of(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def main() -> None:
    findings = []
    for f in sorted(UI.rglob("*.ts")):
        # (comments don't count: blanked, keeping the line numbers)
        text = re.sub(r"//[^\n]*|/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m[0]), f.read_text(encoding="utf-8"),
                      flags=re.S)
        rel = f.relative_to(ROOT)
        css_parts = [(0, text)] if f.name.startswith("styles") else strings_after_style(text)
        for start, part in css_parts:
            for pattern, why in CSS_RULES:
                for m in re.finditer(pattern, part):
                    findings.append(f"{rel}:{line_of(text, start + m.start())}: {m[0].strip()}: {why}")
        code = re.sub(r"//[^\n]*", "", text)  # (not in comments)
        # class names the tablet styles itself: in class strings, className, classList and selectors
        for m in re.finditer(r"""(class:\s*|className\s*=\s*|classList\.\w+\(\s*)(["'`])((?:\\.|(?!\2).)*)\2""", code, re.S):
            for name in EFB_CLASSES:
                if re.search(rf"(?<![-\w]){name}(?![-\w])", m[3]):
                    findings.append(f"{rel}:{line_of(code, m.start())}: class {name}: the tablet styles .{name} itself "
                                    "(measured): use another name")
        if f.name.startswith("styles") or f.name.startswith("theme"):
            for name in EFB_CLASSES:
                for m in re.finditer(rf"\.{name}(?![-\w])", text):
                    findings.append(f"{rel}:{line_of(text, m.start())}: .{name}: the tablet styles it itself (measured)")
        for pattern, why in JS_RULES:
            for m in re.finditer(pattern, code):
                findings.append(f"{rel}:{line_of(code, m.start())}: {m[0]}: {why}")
    for line in findings:
        print(line)
    print(f"{len(findings)} finding(s) in {UI.relative_to(ROOT)}" if findings else "lint_efb: no findings")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
