"""Convert a saved platform documentation page (.mhtml) into Markdown files.

Usage: python3 tools/docs_from_mhtml.py ~/Downloads/Battleships.mhtml [--out docs/platform]

Stdlib only. Re-run on a fresh snapshot when the organizers change the rules.
"""

import argparse
import email
import re
import sys
from datetime import date
from email import policy
from html.parser import HTMLParser
from pathlib import Path

SOURCE_URL = "https://hackathon.no-offence.ru/documentation"

SECTIONS: dict[str, str] = {
    "Руководство по протоколу": "guide.md",
    "Справочник сообщений": "messages.md",
    "Значения и причины": "values.md",
    "Описание API": "http-api.md",
}

SKIP_TAGS = frozenset({"svg", "button", "script", "style", "nav"})
SKIP_CLASSES = frozenset(
    {
        "kit-same-width-label",
        "kit-documentation-marker",
        "kit-documentation-nested-marker",
        "kit-achievement-icon",
        "kit-secret-label",
        "kit-secret-confirmation-line",
    }
)
VOID_TAGS = frozenset({"hr", "br", "img", "input", "meta", "link"})
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
GUIDE_STEP = 3  # width of one nesting step ("└─ ", "├─ ") in field-list guides
ACHIEVEMENT_ROW = re.compile(r"^ \| (.+?) — (обычная|ступени|переходящая) \| (.*) \|$")


class MarkdownBuilder(HTMLParser):
    """Streaming HTML to Markdown for the documentation page markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[str, frozenset[str]]] = []
        self.skip_depth = 0
        self.table: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None
        self.name_cell = False
        self.pre: list[str] | None = None
        self.list_depth = 0
        self.in_guide = False
        self.method = ""

    def emit(self, text: str) -> None:
        if self.pre is not None:
            self.pre.append(text)
        elif self.cell is not None:
            self.cell.append(text)
        else:
            self.out.append(text)

    def paragraph_break(self) -> None:
        if self.cell is None and self.pre is None:
            self.out.append("\n\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            if tag == "hr":
                self.paragraph_break()
            return
        classes = frozenset((dict(attrs).get("class") or "").split())
        self.stack.append((tag, classes))
        if self.skip_depth or tag in SKIP_TAGS or classes & SKIP_CLASSES or "shell-header" in classes:
            self.skip_depth += 1
            return
        match tag:
            case "h1" | "h2" | "h3" | "h4":
                self.paragraph_break()
                level = 5 if "kit-subheading-state" in classes else HEADINGS[tag]
                self.out.append("#" * level + " ")
                if "kit-reference-article-title" in classes and self.method:
                    self.out.append(f"{self.method} ")
                    self.method = ""
            case "p":
                self.paragraph_break()
            case "ul":
                self.list_depth += 1
                self.out.append("\n" if self.list_depth > 1 else "\n\n")
            case "li":
                self.out.append("  " * (self.list_depth - 1) + "- ")
            case "table":
                self.table = []
            case "tr":
                self.row = []
            case "td" | "th":
                self.cell = []
                self.name_cell = "kit-field-list-name" in classes
            case "pre":
                self.pre = []
            case "code" if self.pre is None:
                self.emit("`")
            case "strong" | "b":
                self.emit("**")
            case "em" | "i":
                self.emit("*")
            case "header" if "kit-reference-article-header" in classes:
                self.paragraph_break()
            case "span":
                self.start_span(classes)

    def start_span(self, classes: frozenset[str]) -> None:
        if "kit-field-list-guide" in classes:
            self.in_guide = True
        elif "kit-field-list-nullable" in classes:
            self.emit(", ")
        elif "kit-secret-value" in classes:
            self.emit("`")

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        while self.stack and self.stack[-1][0] != tag:
            self.stack.pop()
        if not self.stack:
            return
        _, classes = self.stack.pop()
        if self.skip_depth:
            self.skip_depth -= 1
            return
        match tag:
            case "h1" | "h2" | "h3" | "h4":
                self.out.append("\n\n")
            case "p":
                self.out.append(" " if self.cell is not None else "\n\n")
            case "ul":
                self.list_depth -= 1
                self.out.append("\n" if self.list_depth else "\n\n")
            case "li":
                self.out.append("\n")
            case "td" | "th":
                self.row.append(self.finish_cell())
            case "tr":
                self.table.append(self.row)
            case "table":
                self.flush_table()
            case "pre":
                self.flush_code()
            case "code" if self.pre is None:
                self.emit("`")
            case "strong" | "b":
                self.emit("**")
            case "em" | "i":
                self.emit("*")
            case "header" if "kit-reference-article-header" in classes:
                self.out.append("\n\n")
            case "span":
                self.end_span(classes)
            case "div" if "kit-achievement-list-cell" in classes:
                self.emit(" | ")
            case "div" if "kit-achievement-list-row" in classes:
                self.out.append("\n")

    def end_span(self, classes: frozenset[str]) -> None:
        if "kit-field-list-guide" in classes:
            self.in_guide = False
        elif "kit-secret-value" in classes:
            self.emit("`")
        elif "kit-reference-article-direction" in classes:
            self.out.append("\n\n")
        elif "kit-achievement-list-title" in classes:
            self.emit(" — ")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_guide:
            self.emit("↳ " * (len(data) // GUIDE_STEP))
        elif any("kit-same-width-shown" in classes for _, classes in self.stack):
            self.method += data.strip()
        elif self.pre is not None:
            self.pre.append(data)
        elif data.strip() or self.cell is not None:
            self.emit(re.sub(r"\s+", " ", data))

    def finish_cell(self) -> str:
        text = re.sub(r"\s+", " ", "".join(self.cell or [])).strip().replace("|", "\\|")
        self.cell = None
        return re.sub(r"^((?:↳ )*)(.*)$", r"\1`\2`", text) if self.name_cell else text

    def flush_table(self) -> None:
        rows = [row for row in self.table if row]
        self.table = []
        if not rows:
            return
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        lines = [f"| {' | '.join(padded[0])} |", "|" + "---|" * width]
        lines += [f"| {' | '.join(row)} |" for row in padded[1:]]
        self.out.append("\n\n" + "\n".join(lines) + "\n\n")

    def flush_code(self) -> None:
        code = "".join(self.pre or []).strip("\n")
        self.pre = None
        language = "json" if code.lstrip().startswith(("{", "[")) else ""
        self.out.append(f"\n\n```{language}\n{code}\n```\n\n")


def achievements_table(markdown: str) -> str:
    lines: list[str] = []
    header_written = False
    for line in markdown.split("\n"):
        row = ACHIEVEMENT_ROW.match(line)
        if row is None:
            lines.append(line)
            continue
        if not header_written:
            lines += ["| Ачивка | Вид | Условие |", "|---|---|---|"]
            header_written = True
        lines.append(f"| {' | '.join(row.groups())} |")
    return "\n".join(lines)


def html_to_markdown(html: str) -> str:
    start = html.index('<main class="shell-main">')
    builder = MarkdownBuilder()
    builder.feed(html[start : html.index("</main>", start)])
    text = "".join(builder.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return achievements_table(text).strip() + "\n"


def split_sections(markdown: str, snapshot_name: str, snapshot_date: date) -> dict[str, str]:
    """Split by top-level section; headings move up one level so each file starts at H1."""
    note = (
        f"> Снимок страницы <{SOURCE_URL}> (`{snapshot_name}`, {snapshot_date.isoformat()}). "
        "Платформа может менять правила: источник истины — страница и `MATCH_STARTED` партии.\n\n"
    )
    files: dict[str, str] = {}
    for chunk in re.split(r"(?m)^## ", markdown)[1:]:
        title, body = chunk.split("\n", 1)
        file_name = SECTIONS[title.strip()]
        body = re.sub(r"(?m)^(#{3,6}) ", lambda m: "#" * (len(m.group(1)) - 1) + " ", body)
        files[file_name] = f"# {title.strip()}\n\n{note}{body.strip()}\n"
    missing = set(SECTIONS.values()) - files.keys()
    if missing:
        raise ValueError(f"sections not found in page: {sorted(missing)}")
    return files


def read_page_html(mhtml: Path) -> str:
    message = email.message_from_binary_file(mhtml.open("rb"), policy=policy.default)
    for part in message.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8")
    raise ValueError(f"no text/html part in {mhtml}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mhtml", type=Path)
    parser.add_argument("--out", type=Path, default=Path("docs/platform"))
    args = parser.parse_args(argv)

    markdown = html_to_markdown(read_page_html(args.mhtml))
    files = split_sections(markdown, args.mhtml.name, date.today())
    args.out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (args.out / name).write_text(content, encoding="utf-8")
        print(f"{args.out / name}: {len(content.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
