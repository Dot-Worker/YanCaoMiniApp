"""Small offline DOM tree; ignores script/style text and never executes source HTML."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re

@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: object = field(default=None, repr=False)

    @property
    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return re.sub(r"\s+", " ", "".join(c if isinstance(c, str) else c.text + " " for c in self.children)).strip()

    def walk(self, tag=None, cls=None, id=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get("class", "").split()) and (id is None or child.attrs.get("id") == id):
                    yield child
                yield from child.walk(tag, cls, id)

class Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict((k, v or "") for k, v in attrs), parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)
