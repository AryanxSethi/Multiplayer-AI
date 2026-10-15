"""Checks on the shape of the source, rather than on what it does."""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted(list((ROOT / "desktop_app").glob("*.py"))
                 + list((ROOT / "network_a2a").glob("*.py")))


def classes(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def accessor_companion(item):
    """Whether this definition is a property's setter, getter or deleter.

    Those are written as a second ``def`` with the same name, which is the
    idiom rather than a mistake: ``@http.setter def http(self, value)`` attaches
    to the property built by the first one. Only an undecorated repeat is a
    real redefinition.

    The decorator has to name *this* function. ``@other.setter def http`` is a
    plain redefinition that happens to carry an accessor decorator, and letting
    it through on the strength of the word "setter" hides exactly the sort of
    shadowing this check exists to catch.
    """
    for decorator in item.decorator_list:
        if not isinstance(decorator, ast.Attribute):
            continue
        if decorator.attr not in ("setter", "getter", "deleter"):
            continue
        if isinstance(decorator.value, ast.Name) and decorator.value.id == item.name:
            return True
    return False


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_class_defines_the_same_method_twice(path):
    """A repeated ``def`` in one class silently replaces the earlier one.

    Nothing complains and the file still imports, so the first definition
    simply stops being reachable. Both halves of this have happened here: a
    header button builder that was kept after its replacement landed beside
    it, and an HTTP handler whose name matched the method it called, which
    replaced it and made the model method uncallable. Both were found by
    reading the traceback, not by a failure.
    """
    clashes = []
    for node in classes(path):
        seen = {}
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name in seen and not accessor_companion(item):
                clashes.append(
                    f"{path.name}: {node.name}.{item.name} is defined at "
                    f"line {seen[item.name]} and again at line {item.lineno}, "
                    f"so the first one cannot be reached")
            seen[item.name] = item.lineno
    assert not clashes, "\n".join(clashes)


ACCESOR_CASES = {
    # The idiom: a property and the accessor that completes it.
    "class T:\n    def http(self): pass\n    @http.setter\n    def http(self, v): pass": [],
    "class T:\n    def http(self): pass\n    @http.getter\n    def http(self): pass": [],
    "class T:\n    def http(self): pass\n    @http.deleter\n    def http(self): pass": [],
    # The same words, a different name: a plain redefinition wearing an
    # accessor decorator, which is what letting any ".setter" through hides.
    "class T:\n    def http(self): pass\n    @other.setter\n    def http(self, v): pass": ["http"],
    # A dotted target is not a name this function could own either.
    "class T:\n    def http(self): pass\n    @a.b.setter\n    def http(self, v): pass": ["http"],
    # No decorator at all.
    "class T:\n    def http(self): pass\n    def http(self, v): pass": ["http"],
    # Two different names are two different methods, decorator or not.
    "class T:\n    def http(self): pass\n    @x.setter\n    def other(self, v): pass": [],
}


@pytest.mark.parametrize("source", ACCESOR_CASES,
                         ids=[f"case{index}" for index in range(len(ACCESOR_CASES))])
def test_the_accessor_exemption_needs_the_matching_name(source):
    """An accessor only excuses a repeat when it names the function itself.

    ``@other.setter def http`` shadows the earlier ``http`` and is not an
    accessor of anything called ``http``; exempting it on the strength of the
    word "setter" lets exactly the shadowing this check exists to find pass
    through. A dotted target is no better: ``@a.b.setter`` attaches to whatever
    ``a.b`` is, which is not the name being defined.
    """
    tree = ast.parse(source)
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        seen = set()
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name in seen and not accessor_companion(item):
                names.append(item.name)
            seen.add(item.name)
    assert names == ACCESOR_CASES[source], (
        f"expected {ACCESOR_CASES[source]} to be reported, got {names}")


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_a_module_defines_no_function_twice_either(path):
    """The same trap at module level, where the later name also wins."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen = {}
    clashes = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in seen:
            clashes.append(
                f"{path.name}: {node.name} is defined at line {seen[node.name]} "
                f"and again at line {node.lineno}")
        seen[node.name] = node.lineno
    assert not clashes, "\n".join(clashes)


IDENTITY_OPERATORS = (ast.Is, ast.IsNot)


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_dialog_result_is_compared_with_is(path):
    """Never ask a dialog whether the answer was No using ``is``.

    ``QMessageBox.question`` returns a plain ``int``; ``StandardButton.Yes`` is
    a Shiboken flag enum. They are equal by value and are never the same
    object, so ``is not Yes`` is always true and every confirmation behind it
    silently returns before doing anything -- the dialog opens, the answer is
    given, and nothing happens.

    It reads as a harmless way to say "not yes", it fails no test and raises
    nothing, and it cost three confirmations here before anyone noticed that
    Clear and Delete had never once worked. Compared with ``==`` it is always
    wrong and never says so.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left] + list(node.comparators)
        mentions_standard_button = any(
            isinstance(child, ast.Attribute) and child.attr == "StandardButton"
            for operand in operands for child in ast.walk(operand))
        if not mentions_standard_button:
            continue
        for operator in node.ops:
            if isinstance(operator, IDENTITY_OPERATORS):
                word = "is" if isinstance(operator, ast.Is) else "is not"
                offenders.append(
                    f"{path.name}: line {node.lineno} compares a StandardButton "
                    f"with '{word}'")
    assert not offenders, "\n".join(offenders)


# What actually gets written when UTF-8 text is decoded as Windows-1252 and
# encoded back as UTF-8: a two- or three-character sequence whose characters
# are individually perfectly good.
#
# Matched as sequences rather than as the single characters they are built
# from, because those characters are letters. U+00E2 is an a-circumflex and
# U+00C2 is an A-circumflex, which between them appear in ordinary French
# ("âge"), Portuguese ("árvore") and Romanian ("îngust") -- so matching them
# alone fails the build on correct text, which is a guard that stops being
# turned on rather than one that catches something.
MOJIBAKE = (
    "\u00c2\u00b7",   # Â·  a middot
    "\u00c2\u00b0",   # Â°  a degree sign
    "\u00c2\u00ab",   # Â«  a left guillemet
    "\u00c2\u00bb",   # Â»  a right guillemet
    "\u00c3\u00a1",   # Ã¡  an a-acute
    "\u00c3\u00a4",   # Ã¤  an a-umlaut
    "\u00c3\u00a7",   # Ã§  a c-cedilla
    "\u00c3\u00a8",   # Ã¨  an e-grave
    "\u00c3\u00a9",   # Ã©  an e-acute
    "\u00c3\u00ad",   # Ã­  an i-acute
    "\u00c3\u00b1",   # Ã±  an n-tilde
    "\u00c3\u00b3",   # Ã³  an o-acute
    "\u00c3\u00b6",   # Ã¶  an o-umlaut
    "\u00c3\u00ba",   # Ãº  a u-acute
    "\u00c3\u00bc",   # Ã¼  a u-umlaut
    "\u00e2\u20ac",   # â€  the start of every em-dash or curly quote
    "\u00e2\u0080",   # â\x80 the same, mid-sequence
    "\u00ef\u00bb\u00bf",  # ï»¿ a byte-order mark read as text
    "\ufffd",         # the replacement character, which is never correct
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_source_file_has_been_mangled_by_an_encoding_change(path):
    """Text read as one encoding and written back as another corrupts silently.

    Eight separators on the Cookbook page turned from a middot into the two
    characters its UTF-8 bytes look like when decoded as Windows-1252, so every
    model card read "1.5B Â· 940.4 MB download". Nothing threw, nothing failed,
    and no assertion covered the string -- the tests stayed green while the
    interface was wrong in front of whoever was reading it.

    A bulk edit is the usual way this happens, so the check is on the text
    rather than on anything an interpreter would notice.
    """
    text = path.read_text(encoding="utf-8")
    offenders = [f"{path.name}: line {number} contains {mark!r}"
                 for number, line in enumerate(text.splitlines(), 1)
                 for mark in MOJIBAKE if mark in line]
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("text", (
    "Le calcul de l’âge du modèle. Café au lait.",
    "A árvore e a relação entre camadas",
    "Além do cálculo, a página não cabe.",
    "Calculul îngust pentru un model",
))
def test_the_mojibake_guard_does_not_fail_on_correct_text(text):
    """A guard that cries wolf on French is a guard nobody leaves switched on.

    The first version matched the individual characters the corruption is built
    from, which rejected this whole list -- "âge" and "árvore" both contain an
    a-circumflex, which is U+00E2, and U+00E2 was one of the things it looked
    for. Every one of these is the second half of a check that is supposed to
    be impossible to turn off by accident.
    """
    assert not [mark for mark in MOJIBAKE if mark in text], (
        f"{text!r} is correct text and matched {MOJIBAKE!r}")


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_every_source_file_is_valid_utf8(path):
    """A file that is not valid UTF-8 fails to import, so it cannot reach here.

    Stated as its own check because the failure it prevents is invisible from
    the inside: the damage above was well-formed UTF-8 that decoded to the
    wrong characters, which is precisely why it passed everything.
    """
    path.read_bytes().decode("utf-8")


def test_the_cookbook_has_exactly_one_ungated_path_to_the_network():
    """Only ``ask_to_refresh`` may call ``do_refresh``; no control may be wired
    to it.

    ``do_refresh`` is the method the disclosure exists to sit in front of. It is
    left ungated so that tests can drive a refresh without a dialog, which is
    only safe while nothing else calls it -- the docstring on the gated method
    says exactly that.

    It was not true. The empty-state card offered its own "Refresh from <host>"
    and wired it straight to the ungated method, so a reader who filtered to a
    list with nothing in it could put a dozen requests out to a host they had
    never been told about and never be asked. The main button asked every time,
    which is what made it look like the page was covered.

    Checked on the syntax rather than at runtime because a second caller is
    easy to add and hard to notice: it works, it just works without asking.
    """
    panel = ROOT / "desktop_app" / "cookbook_panel.py"
    tree = ast.parse(panel.read_text(encoding="utf-8"))

    gate = [node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "ask_to_refresh"]
    assert gate, "ask_to_refresh has gone; this check is looking in the wrong place"
    gate = gate[0]
    gate_lines = range(gate.lineno, gate.end_lineno + 1)

    called, handed = [], []
    nodes = list(ast.walk(tree))
    for node in nodes:
        if not (isinstance(node, ast.Attribute) and node.attr == "do_refresh"):
            continue
        invoked = any(parent.func is node for parent in nodes
                      if isinstance(parent, ast.Call))
        # Invoked is ``self.do_refresh()``. Not invoked is the method being
        # handed to something as a value -- which is what a button callback is,
        # and what lets a reader press past the disclosure.
        (called if invoked else handed).append(node.lineno)

    assert not handed, (
        f"cookbook_panel.py hands do_refresh to a control on lines {handed}, "
        "so a reader can press it without the disclosure being asked for")
    stray = [line for line in called if line not in gate_lines]
    assert not stray, (
        f"cookbook_panel.py calls do_refresh outside ask_to_refresh, on lines "
        f"{stray}; it is meant to be reachable only from there")


def test_dialogs_module_holds_only_the_dialogs_the_app_can_open():
    """Keep the orphaned dialogs from quietly growing back.

    AgentDialog and JoinDialog were unreachable: nothing in the app constructed
    them, but three test modules still did, which kept roughly 400 lines of a
    second, stale implementation of the model card and the join form alive and
    passing. Deleting them was only safe once those tests went through the real
    widgets, and nothing else stops the next one from importing them again.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    source = (root / "desktop_app" / "dialogs.py").read_text(encoding="utf-8")
    names = {n.name for n in ast.parse(source).body
             if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    live = {"available_agent_name", "parse_invitation", "InviteDialog",
            "RefreshDisclosure"}
    assert names == live, f"dialogs.py now defines {sorted(names - live)}"

    # Matched on names, not text: prose that explains why these were deleted
    # still names them, and that is not a reference.
    removed = {"AgentDialog", "JoinDialog", "_style_error"}
    offenders = []
    for path in list((root / "desktop_app").rglob("*.py")) + list((root / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            found = set()
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                found = {(a.asname or a.name).split(".")[0] for a in node.names} & removed
            elif isinstance(node, ast.Name):
                found = {node.id} & removed
            elif isinstance(node, ast.Attribute):
                found = {node.attr} & removed
            offenders += [f"{path.relative_to(root)}: {name}" for name in sorted(found)]
    assert not offenders, "deleted dialog code is referenced again: " + ", ".join(offenders)
