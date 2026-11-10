from jinja2 import Template, Undefined


class KeepUndefined(Undefined):
    def __str__(self):
        return "{{ " + self._undefined_name + " }}"

    __repr__ = __str__

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return KeepUndefined(name=f"{self._undefined_name}.{name}")

    def __getitem__(self, key):
        return KeepUndefined(name=f"{self._undefined_name}.{key}")


class PathDict(dict):
    """Dict that returns a path-aware KeepUndefined for missing keys."""

    def __init__(self, data, path=""):
        super().__init__(data)
        self._path = path

    def __missing__(self, key):
        full = f"{self._path}.{key}" if self._path else str(key)
        return KeepUndefined(name=full)


def wrap(value, path=""):
    if isinstance(value, dict):
        return PathDict(
            {k: wrap(v, f"{path}.{k}" if path else str(k)) for k, v in value.items()},
            path,
        )
    return value


def render_prompt(template: str, state: dict) -> str:
    context = {**wrap(state), "state": wrap(state, "state")}
    return Template(template, undefined=KeepUndefined).render(context)


if __name__ == "__main__":
    # caller missing entirely
    assert render_prompt("{{ caller.name }}", {}) == "{{ caller.name }}"

    # caller exists, name missing
    assert render_prompt("{{ caller.name }}", {"caller": {"id": 7}}) == "{{ caller.name }}"

    # caller.name present
    assert render_prompt("{{ caller.name }}", {"caller": {"name": "Bob"}}) == "Bob"

    # nested
    assert render_prompt("{{ a.b.c }}", {"a": {"b": {}}}) == "{{ a.b.c }}"

    # via the state object
    assert render_prompt("{{ state.caller.name }}", {}) == "{{ state.caller.name }}"

    print("All checks passed.")