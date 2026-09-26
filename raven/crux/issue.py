"""Issue card (deterministic intake): everything the issue text states that
code can use without a model call -- identifiers, file paths, stack-trace
frames, code snippets, and example calls with their stated expectations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_IDENT_RE = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`|\b([a-z_][a-z0-9]*_[a-z0-9_]+|[A-Z][a-z]+[A-Z]\w+)\b|\b([A-Za-z_]\w*)\(")
_PATH_RE = re.compile(r"[\w./-]+\.(?:py|pyi|js|ts|jsx|tsx|go|rs|java|rb)\b")
_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+), in (\w+)')
_FENCE_RE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)
_EXC_RE = re.compile(r"\b(\w+(?:Error|Exception|Warning))\b")
_STOP = {
    "print", "len", "str", "int", "float", "list", "dict", "set", "tuple", "range", "type", "isinstance",
    "return", "import", "from", "assert", "self", "None", "True", "False", "e", "g", "i", "if", "for",
}


@dataclass
class IssueCard:
    text: str
    identifiers: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    frames: list[tuple[str, int, str]] = field(default_factory=list)  # (file, line, function)
    snippets: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.identifiers:
            parts.append("identifiers: " + ", ".join(self.identifiers[:15]))
        if self.paths:
            parts.append("paths: " + ", ".join(self.paths[:10]))
        if self.frames:
            parts.append("stack frames: " + "; ".join(f"{f}:{l} in {fn}" for f, l, fn in self.frames[-6:]))
        if self.exceptions:
            parts.append("exceptions: " + ", ".join(self.exceptions[:5]))
        return "\n".join(parts)


def build_issue_card(text: str) -> IssueCard:
    idents: list[str] = []
    for m in _IDENT_RE.finditer(text):
        name = next(g for g in m.groups() if g)
        for part in name.split("."):
            if part and part not in _STOP and len(part) > 1 and part not in idents:
                idents.append(part)
    frames = [(f, int(line), fn) for f, line, fn in _FRAME_RE.findall(text)]
    for _, _, fn in frames:
        if fn not in idents and fn != "<module>":
            idents.insert(0, fn)
    return IssueCard(
        text=text,
        identifiers=idents,
        paths=list(dict.fromkeys(_PATH_RE.findall(text))),
        frames=frames,
        snippets=[s.strip() for s in _FENCE_RE.findall(text)],
        exceptions=list(dict.fromkeys(_EXC_RE.findall(text))),
    )
