"""Tool schemas every brain accepts, arguments made to fit, and the "done" check (Gemini once refused a whole
request with 400 INVALID_ARGUMENT "unrecognized type 'int' at properties.port", over one tool in a skill)."""

from __future__ import annotations

import inspect

from jarvis import gemini, router
from jarvis.brain import DONE_CHECK, VERIFY_CHECK, Jarvis
from jarvis.tools import JSON_TYPES, Tool, ToolRegistry, fit_args, normalize_schema, obj

from conftest import Approver, FakeClient, response, text_block, tool_block


def types_in(node) -> set:
    found = set()
    if isinstance(node, dict):
        if "type" in node:
            found.add(node["type"])
        for value in node.values():
            found |= types_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= types_in(value)
    return found


def test_python_type_names_become_json_schema_types():
    schema = normalize_schema(obj({"port": ("int", "Port"), "host?": ("str", "Host"), "secure?": ("bool", ""),
                                   "paths?": ("list", ""), "extra?": ("dict", ""), "ratio?": ("float", ""),
                                   "thing?": ("any", "")}))
    kinds = {k: v["type"] for k, v in schema["properties"].items()}
    assert kinds == {"port": "integer", "host": "string", "secure": "boolean", "paths": "array", "extra": "object",
                     "ratio": "number", "thing": "string"}
    assert schema["properties"]["paths"]["items"] == {"type": "string"} and schema["required"] == ["port"]
    # nested schemas, type lists and keys the APIs reject
    nested = normalize_schema({"type": "object", "additionalProperties": False, "properties": {
        "rows": {"type": "list", "items": {"type": "dict", "properties": {"n": {"type": ["int", "null"]}}}}}})
    assert nested["properties"]["rows"]["items"]["properties"]["n"]["type"] == "integer"
    assert "additionalProperties" not in nested
    assert normalize_schema(None) == {"type": "object", "properties": {}, "required": []}


def test_a_skill_with_an_int_is_sent_to_gemini_as_integer(settings, ctx, registry, monkeypatch):
    from jarvis.plugins.agent import load_skill, skills_dir

    (skills_dir(ctx) / "server.py").write_text(
        "from jarvis.tools import obj\n"
        "def register(registry, ctx):\n"
        "    @registry.tool('Start a server.', obj({'port': ('int', 'Port'), 'host?': ('str', 'Host')}))\n"
        "    def start_server(port: int, host: str = 'localhost'):\n"
        "        return f'{host}:{port} {type(port).__name__}'\n", encoding="utf-8")
    assert load_skill(registry, ctx, skills_dir(ctx) / "server.py") == ["start_server"]
    declared = {d.name: d.parameters_json_schema for d in gemini.to_declarations(registry.definitions())}
    assert declared["start_server"]["properties"]["port"]["type"] == "integer"
    assert types_in([d.parameters_json_schema for d in gemini.to_declarations(registry.definitions())]) <= JSON_TYPES
    # a text "8080" from the model becomes the integer the tool wants
    assert registry.run("start_server", {"port": "8080"}, Approver())[0] == "localhost:8080 int"


def test_every_built_in_tool_has_a_schema_every_brain_accepts(registry):
    for tool in registry.tools.values():
        assert types_in(tool.input_schema) <= JSON_TYPES, tool.name
        assert tool.input_schema["type"] == "object" and isinstance(tool.input_schema["properties"], dict), tool.name
        for prop in tool.input_schema["properties"].values():
            assert prop.get("type") != "array" or "items" in prop, tool.name
        # the schema's arguments are ones the function takes (or it takes anything)
        params = inspect.signature(tool.func).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            assert set(tool.input_schema["properties"]) <= set(params), tool.name


def test_arguments_are_made_to_fit_or_the_mistake_is_said_plainly():
    schema = normalize_schema(obj({"port": ("integer", ""), "on?": ("boolean", ""), "tags?": ("array", ""),
                                   "ratio?": ("number", ""), "name?": ("string", "")}))
    fitted, problem = fit_args(schema, {"Port": "8080", "on": "false", "tags": '["a", "b"]', "ratio": "0.5", "name": 7})
    assert problem is None and fitted == {"port": 8080, "on": False, "tags": ["a", "b"], "ratio": 0.5, "name": "7"}
    assert fit_args(schema, {"port": 1, "tags": "one"})[0]["tags"] == ["one"]
    assert "Missing required argument(s): port" in fit_args(schema, {"on": True})[1]
    assert "Unknown argument 'prt'" in fit_args(schema, {"prt": 1, "port": 1})[1]
    assert fit_args(schema, {"port": "many"})[0]["port"] == "many"  # not a number: left for the tool to complain

    registry = ToolRegistry()
    registry.add(Tool("echo", "Echo.", lambda text: text, obj({"text": ("string", "")})))
    out, error = registry.run("echo", {"txt": "hi"}, Approver())
    assert error and "Unknown argument 'txt'" in out and "text: string" in out


def test_bulgarian_in_latin_letters_is_recognised_and_hinted():
    assert router.latin_bulgarian("i sushto iskam da vidim dali she ima kak da napravish jarvis po umen")
    assert router.latin_bulgarian("pusni muzika")
    assert router.latin_bulgarian("zashto ne raboti tova be")
    assert not router.latin_bulgarian("open youtube please")
    assert not router.latin_bulgarian("what is the time")
    assert not router.latin_bulgarian("пусни музика")
    assert not router.latin_bulgarian("ok")
    assert router.to_cyrillic("pusni muzika na youtube") == "пусни музика на youtube"
    assert router.to_cyrillic("kakvo shte pravim dnes") == "какво ще правим днес"
    assert router.to_cyrillic("Jarvis, otvori chrome") == "Jarvis, отвори chrome"


def test_a_latin_message_reaches_the_brain_with_a_cyrillic_hint(settings, ctx, registry):
    client = FakeClient([response(text_block("Добре."))])
    Jarvis(settings, ctx.store, registry, Approver(), client=client).ask("kaji mi kakvo ima dnes v kalendara")
    sent = client.requests[0]["messages"][-1]["content"]
    assert "kaji mi kakvo ima dnes" in sent and "Написано на латиница" in sent and "кажи ми какво има днес" in sent
    assert "Написано на латиница" not in str(ctx.store.history("main", 10))  # the hint is not kept


def test_done_after_a_failed_step_gets_a_second_look(settings, ctx, registry):
    client = FakeClient([
        response(tool_block("read_file", {"path": "/nowhere/none.txt"}), stop="tool_use"),  # fails
        response(text_block("Готово, прочетох файла.")),  # claims done anyway
        response(text_block("Файлът /nowhere/none.txt не съществува. Да го потърся ли?")),
    ])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("прочети ми файла none.txt").startswith("Файлът")
    nudge = client.requests[2]["messages"][-1]["content"]
    assert nudge == DONE_CHECK.format(failed="read_file") and DONE_CHECK[:20] not in str(ctx.store.history("main", 10))

    # an honest answer after a failure is not questioned, and an empty one does not become "Готово."
    client.responses = [response(tool_block("read_file", {"path": "/nowhere/none.txt"}), stop="tool_use"),
                        response(text_block("Не успях: файлът липсва."))]
    client.requests.clear()
    assert jarvis.ask("прочети ми файла none.txt") == "Не успях: файлът липсва." and len(client.requests) == 2
    client.responses = [response(tool_block("read_file", {"path": "/nowhere/none.txt"}), stop="tool_use"), response()]
    assert jarvis.ask("прочети ми файла none.txt").startswith("Опитах, но последната стъпка не успя (read_file)")


def test_a_mission_gets_more_steps_and_a_check_of_the_result(settings, ctx, registry):
    assert router.is_mission("направи го и работи докато не стане перфектно")
    assert router.is_mission("raboti bez da spirash do 11 sutrinta")
    assert not router.is_mission("колко е часа") and not router.is_mission("пусни музика")
    settings.max_tool_rounds = 2
    client = FakeClient([
        response(tool_block("list_tasks", {}), stop="tool_use"),
        response(text_block("Готово, прегледах задачите.")),  # the result is checked first
        response(tool_block("list_tasks", {}, id="tu_2"), stop="tool_use"),  # more than 2 steps are allowed
        response(text_block("Проверих: списъкът е празен, нищо не остава.")),
    ])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("прегледай задачите и провери всичко до край").startswith("Проверих")
    assert client.requests[2]["messages"][-1]["content"] == VERIFY_CHECK
    assert len(client.requests) == 4 and VERIFY_CHECK[:30] not in str(ctx.store.history("main", 10))
    # an ordinary request gets no check
    client.responses = [response(tool_block("list_tasks", {}), stop="tool_use"), response(text_block("Нямаш задачи."))]
    client.requests.clear()
    assert jarvis.ask("виж ми задачите") == "Нямаш задачи." and len(client.requests) == 2


def test_the_exact_contact_wins_and_a_look_alike_is_not_called(registry):
    from jarvis.plugins.android import pick_rows

    rows = [{"display_name": "Alper's mom", "data1": "+359 1"}, {"display_name": "Mom", "data1": "+359 2"},
            {"display_name": "Ivan", "data1": "+359 3"}]
    assert pick_rows(rows, "mom", "display_name") == ([rows[1]], "")
    assert pick_rows(rows[:1] + rows[2:], "mom", "display_name")[1].startswith("No contact is named exactly 'mom'")
    picked, note = pick_rows([{"display_name": "Мама Ани", "data1": "1"}, {"display_name": "Омама", "data1": "2"}], "мама", "display_name")
    assert picked == [{"display_name": "Мама Ани", "data1": "1"}] and note  # a whole word ranks first, still with the note
    out, error = registry.run("phone_call", {"device": "phone", "number": "Alper's mom"}, Approver())
    assert error and "not a phone number" in out
    out, error = registry.run("phone_sms", {"device": "phone", "number": "mom", "text": "hi"}, Approver())
    assert error and "not a phone number" in out
    out, _ = registry.run("phone_call", {"device": "phone", "number": "+359 888 123 456"}, Approver())
    assert "not a phone number" not in out  # a real number goes on to the phone (not connected in tests)


def test_files_sent_in_the_app_are_saved_for_jarvis_to_open(tmp_path):
    import base64

    from jarvis.hub import HTTPError, save_files, with_files

    data = "data:text/plain;base64," + base64.b64encode("здрасти".encode()).decode()
    paths = save_files([{"name": "бележки.txt", "data": data}], tmp_path)
    assert len(paths) == 1 and paths[0].read_text(encoding="utf-8") == "здрасти" and paths[0].parent == tmp_path / "uploads"
    text = with_files("виж това", paths)
    assert text.startswith("виж това") and str(paths[0]) in text and "watch_video" in text
    assert with_files("нищо", []) == "нищо"
    try:
        save_files([{"name": "x", "data": "not a data url"}], tmp_path)
    except HTTPError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("a broken file must be refused")


def test_colloquial_greetings_take_the_quick_lane():
    from jarvis import router
    for text in ["кво става jarvis", "к'во става", "що стана", "какво ново, Джарвис?"]:
        assert router.quick(text) and not router.wants_action(text), text
    assert router.latin_bulgarian("kvo staa jarvis") and router.to_cyrillic("kvo staa jarvis").startswith("кво стаа")
