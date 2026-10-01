from .priority import PriorityDispatcher
from .scripted import ScriptedDispatcher


def make_dispatcher(name: str, scenario):
    if name == "priority":
        return PriorityDispatcher()
    if name == "scripted":
        return ScriptedDispatcher(scenario.dispatcher.get("directives", []))
    raise ValueError(f"неизвестный диспетчер {name}")


__all__ = ["PriorityDispatcher", "ScriptedDispatcher", "make_dispatcher"]
