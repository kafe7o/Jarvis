"""The brains on the laptop: downloading and adding them (Settings > Модели) and making a small one smarter."""

import json
from types import SimpleNamespace

import pytest

from jarvis import answers, brain, local, localmodels


def ollama_has(monkeypatch, *names):
    monkeypatch.setattr(local, "_seen", {"at": float("inf"), "models": list(names)})


def test_the_owners_words_name_a_model():
    assert localmodels.resolve("изтегли qwen 9b") == "huihui_ai/qwen3.5-abliterated:9b"
    assert localmodels.resolve("бързия 4b") == "huihui_ai/qwen3.5-abliterated:4B"
    assert localmodels.resolve("9b guff от hugging face").startswith("hf.co/")
    assert localmodels.resolve("pamet po smisal") == "qwen3-embedding:0.6b"
    assert localmodels.resolve("hf.co/user/repo:Q4_K_M") == "hf.co/user/repo:Q4_K_M"
    assert localmodels.resolve("gemma3:4b") == "gemma3:4b"
    assert localmodels.resolve("нещо") is None


def test_how_a_model_fits_the_owners_laptop():
    laptop = {"vram": 6.0, "ram": 15.4}
    assert "цял" in localmodels.fit(3.3, laptop)
    assert "част" in localmodels.fit(6.6, laptop)
    assert "твърде" in localmodels.fit(24, laptop)
    assert "процесора" in localmodels.fit(3.3, {"vram": 0, "ram": 16})


def test_a_gguf_in_downloads_is_found_but_not_its_vision_part_or_a_small_file(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    for name, size in (("Qwen3.5-9B-abliterated-Q4_K_M.gguf", 5_600_000_000), ("mmproj-f16.gguf", 900_000_000),
                       ("tiny.gguf", 1000)):
        with open(downloads / name, "wb") as f:
            f.truncate(size)
    found = localmodels.gguf_files([downloads])
    assert [f["name"] for f in found] == ["qwen3.5-9b-abliterated-q4_k_m"] and found[0]["gb"] == 5.6


def test_the_models_have_a_folder_of_their_own(tmp_path, monkeypatch, settings, ctx, registry):
    from test_hub import call, make_hub

    monkeypatch.setenv("JARVIS_MODELS_DIR", str(tmp_path / "Jarvis Models"))
    folder = localmodels.models_folder()
    assert folder.is_dir() and folder == tmp_path / "Jarvis Models"
    with open(folder / "Qwen3.5-4B-Q4_K_M.gguf", "wb") as f:
        f.truncate(3_300_000_000)
    monkeypatch.setattr(localmodels, "version", lambda: "0.40.8")
    monkeypatch.setattr(localmodels, "hardware", lambda: {"gpu": "", "vram": 0, "ram": 16, "disk": 300})
    assert localmodels.gguf_files()[0]["name"] == "qwen3.5-4b-q4_k_m"  # found there with no folder named
    _hub, port = make_hub(settings, ctx, registry, [])
    view = json.loads(call(port, "/api/local-models")[1])
    assert view["folder"] == str(folder) and view["files"][0]["path"] == str(folder / "Qwen3.5-4B-Q4_K_M.gguf")
    assert json.loads(call(port, "/api/local-models", {"action": "open_folder"})[1])["folder"] == str(folder)


def test_a_qwen_35_file_gets_ollamas_own_prompt_and_tool_reading():
    spec = localmodels.modelfile(r"C:\Users\Anastas\Downloads\Qwen3.5-9B-abliterated-Q4_K_M.gguf")
    assert spec.startswith("FROM C:\\Users") and "RENDERER qwen3.5" in spec and "PARSER qwen3.5" in spec
    assert f"num_ctx {local.NUM_CTX}" in spec
    assert "RENDERER" not in localmodels.modelfile("/x/llama-3.2-3b.Q4_K_M.gguf")
    assert "RENDERER" not in localmodels.modelfile("/x/Qwen3.5-9B.gguf", renderer=False)


def gguf(path, arch):
    name = b"general.architecture"
    path.write_bytes(b"GGUF" + (3).to_bytes(4, "little") + bytes(16) + len(name).to_bytes(8, "little") + name
                     + (8).to_bytes(4, "little") + len(arch).to_bytes(8, "little") + arch.encode())
    return path


def test_a_qwen_35_under_another_name_is_known_by_what_is_inside_the_file(tmp_path):
    ornith = gguf(tmp_path / "Ornith-1.5-9B-Q4_K_M.gguf", "qwen35")
    assert localmodels.architecture(ornith) == "qwen35" and "RENDERER qwen3.5" in localmodels.modelfile(str(ornith))
    gemma = gguf(tmp_path / "gemma-4-E4B-it-Q4_K_M.gguf", "gemma4")
    assert "RENDERER" not in localmodels.modelfile(str(gemma))
    assert localmodels.architecture(tmp_path / "missing.gguf") == ""


def test_a_file_too_big_for_the_laptop_is_not_added(tmp_path, monkeypatch):
    file = tmp_path / "Qwen3.5-9B-abliterated-F16.gguf"
    with open(file, "wb") as f:
        f.truncate(17_500_000_000)
    monkeypatch.setattr(localmodels, "hardware", lambda: {"gpu": "RTX 4050", "vram": 6, "ram": 16, "disk": 300})
    with pytest.raises(ValueError, match="Q4_K_M"):
        localmodels.import_gguf(str(file))


def test_a_models_folder_made_inside_jarvis_is_the_one_used(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_MODELS_DIR", raising=False)
    monkeypatch.setattr(localmodels.Path, "home", lambda: tmp_path)
    (tmp_path / "Documents" / "Jarvis" / "Jarvis Models").mkdir(parents=True)
    assert localmodels.models_folder() == tmp_path / "Documents" / "Jarvis" / "Jarvis Models"
    assert not (tmp_path / "Documents" / "Jarvis Models").exists()


def test_adding_a_file_falls_back_to_its_own_template_on_an_older_ollama(tmp_path, monkeypatch):
    file = tmp_path / "Qwen3.5-9B-abliterated-Q4_K_M.gguf"
    file.write_bytes(b"GGUF")
    monkeypatch.setattr(localmodels, "ollama_program", lambda: "ollama")
    monkeypatch.setattr(localmodels, "version", lambda: "0.20.0")
    monkeypatch.setattr(localmodels.Path, "home", lambda: tmp_path)
    specs = []

    def run(cmd, **kw):
        specs.append(open(cmd[-1], encoding="utf-8").read())
        return SimpleNamespace(returncode=1 if len(specs) == 1 else 0, stderr="unknown command RENDERER", stdout="")

    started = []
    monkeypatch.setattr(localmodels.threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: started.append(target)))
    assert "qwen3.5-9b-abliterated-q4_k_m" in localmodels.import_gguf(str(file), run=run)
    started[0]()
    assert "RENDERER" in specs[0] and "RENDERER" not in specs[1]
    assert localmodels.progress()["qwen3.5-9b-abliterated-q4_k_m"]["status"] == "готово"


def test_a_download_shows_its_progress_and_its_error():
    localmodels._pulls["m"] = {"status": "", "percent": 0, "done": False, "error": ""}
    seen = []

    def stream(path, body):
        assert body == {"model": "m", "stream": True}
        yield json.dumps({"status": "pulling manifest"})
        yield json.dumps({"status": "pulling a", "digest": "a", "total": 4_000_000_000, "completed": 1_000_000_000})
        seen.append(dict(localmodels._pulls["m"]))
        yield json.dumps({"status": "pulling b", "digest": "b", "total": 1_000_000_000, "completed": 1_000_000_000})
        seen.append(dict(localmodels._pulls["m"]))
        yield json.dumps({"status": "success"})

    localmodels._pull("m", post=stream)
    assert seen[0]["percent"] == 25 and seen[1]["percent"] == 40 and "от 5.0 GB" in seen[1]["status"]
    assert localmodels._pulls["m"] == {"status": "готово", "percent": 100, "done": True, "error": ""}
    localmodels._pulls["m"] = {"status": "", "percent": 0, "done": False, "error": ""}
    localmodels._pull("m", post=lambda p, b: iter([json.dumps({"error": "pull model manifest: file does not exist"})]))
    assert localmodels._pulls["m"]["done"] and "does not exist" in localmodels._pulls["m"]["error"]


def test_a_gguf_from_hugging_face_needs_a_new_enough_ollama(monkeypatch):
    monkeypatch.setattr(localmodels, "version", lambda: "0.18.2")
    with pytest.raises(RuntimeError, match="стар"):
        localmodels.pull("hf.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-i1-GGUF:Q4_K_S")
    with pytest.raises(ValueError):
        localmodels.pull("rm -rf /")


def test_the_owners_qwen_9b_is_the_laptop_brain_and_its_4b_takes_the_easy_steps(monkeypatch):
    ollama_has(monkeypatch, "qwen3-embedding:0.6b", "gemma3:4b", "huihui_ai/qwen3.5-abliterated:4B",
               "huihui_ai/qwen3.5-abliterated:9b")
    assert local.pick() == "huihui_ai/qwen3.5-abliterated:9b"
    assert local.quick_partner("huihui_ai/qwen3.5-abliterated:9b") == "huihui_ai/qwen3.5-abliterated:4B"
    assert local.quick_partner("gemma3:4b") is None
    ollama_has(monkeypatch, "qwen3-embedding:0.6b", "qwen3.5-9b-abliterated-q4_k_m:latest", "huihui_ai/qwen3.5-abliterated:4B")
    assert local.pick() == "qwen3.5-9b-abliterated-q4_k_m:latest"  # the file the owner added, not the memory model
    assert local.quick_partner(local.pick()) == "huihui_ai/qwen3.5-abliterated:4B"


def test_qwen_35_tool_calls_written_as_text_are_made():
    xml = "<tool_call>\n<function=open_target>\n<parameter=target>\ntaskmgr\n</parameter>\n</function>\n</tool_call>"
    assert answers.written_call(xml) == ("open_target", {"target": "taskmgr"})
    assert answers.written_call("<function=add_task>\n<parameter=title>\nмляко\n</parameter>\n<parameter=priority>\n2"
                               "\n</parameter>\n</function>") == ("add_task", {"title": "мляко", "priority": 2})
    tagged = 'Отварям го.\n<tool_call>\n{"name": "open_target", "arguments": {"target": "notepad"}}\n</tool_call>'
    assert answers.written_call(tagged) == ("open_target", {"target": "notepad"})
    assert answers.written_call("Телефонът е 0888123456.") is None
    reply = local.from_reply({"message": {"content": "<think>hm</think>" + xml}}, "q")
    assert reply.stop_reason == "tool_use" and reply.content[0].name == "open_target"


def test_a_laptop_model_gets_qwens_settings_short_rules_and_stays_loaded(monkeypatch):
    ollama_has(monkeypatch, "q")
    bodies = []
    brain_ = local.LocalBrain(lambda path, body: bodies.append(body) or {"message": {"content": "Да."}})
    tools = [{"name": "weather", "description": "d", "input_schema": {"type": "object"}}]
    brain_.create(model="q", system="Ти си Jarvis.", messages=[{"role": "user", "content": "x"}], tools=tools,
                  max_tokens=100, effort="low")
    body = bodies[0]
    assert body["keep_alive"] == "30m" and body["think"] is False and body["options"]["top_k"] == 20
    assert body["options"]["num_ctx"] == local.NUM_CTX == 32768 and "CALL a tool" in body["messages"][0]["content"]
    brain_.create(model="q", system="s", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=100,
                  effort="xhigh")
    assert bodies[1]["think"] is True


def _jarvis(settings, ctx, registry, replies):
    from test_router import Approver, FakeClient

    bodies = []

    def post(path, body):
        bodies.append(body)
        return replies.pop(0)

    jarvis = brain.Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]),
                          local_brain=local.LocalBrain(post))
    return jarvis, bodies


def test_the_laptop_split_between_its_two_models(settings, ctx, registry, monkeypatch):
    ollama_has(monkeypatch, "huihui_ai/qwen3.5-abliterated:4B", "huihui_ai/qwen3.5-abliterated:9b")
    settings.model, settings.effort = "local", "xhigh"
    jarvis, bodies = _jarvis(settings, ctx, registry, [{"message": {"content": "Здравей."}}] * 3)
    jarvis._request([{"role": "user", "content": "здрасти"}], None, "s", set(), tools=[], max_tokens=100)
    assert bodies[-1]["model"] == "huihui_ai/qwen3.5-abliterated:4B" and bodies[-1]["think"] is False
    tools = [{"name": "weather", "description": "d", "input_schema": {"type": "object"}}]
    jarvis._request([{"role": "user", "content": "времето"}], None, "s", set(), tools=tools, max_tokens=100)
    assert bodies[-1]["model"] == "huihui_ai/qwen3.5-abliterated:9b" and bodies[-1]["think"] is False
    failed = [{"role": "user", "content": "времето"},
              {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "weather", "input": {}}]},
              {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "boom", "is_error": True}]}]
    jarvis._request(failed, None, "s", set(), tools=tools, max_tokens=100)
    assert bodies[-1]["model"] == "huihui_ai/qwen3.5-abliterated:9b" and bodies[-1]["think"] is True


def test_many_memories_only_the_ones_about_the_request_and_the_newest_go_in():
    facts = [{"topic": "кола", "fact": "Колата е синя Тойота"}] + [
        {"topic": f"t{i}", "fact": f"факт номер {i}"} for i in range(60)]
    shown = brain.relevant_facts(facts, "каква е колата ми?")
    assert facts[0] in shown and len(shown) == brain.FACTS_NEWEST + 1 and shown[-1] == facts[-1]
    assert brain.relevant_facts(facts[:30], "нещо") == facts[:30]
    assert brain.relevant_facts(facts, "iskam da znam kakva e kolata mi")[0] == facts[0]  # typed in Latin letters too


def test_the_chat_tool_lists_the_laptop_brains(ctx, registry, monkeypatch):
    monkeypatch.setattr(localmodels, "overview", lambda *a: {
        "ollama": "0.31.0", "installed_ollama": True, "hardware": {"gpu": "RTX 4050", "vram": 6.0, "ram": 15.4, "disk": 380},
        "models": [dict(localmodels.CATALOG[0], fit="част", installed=True)], "others": [],
        "files": [{"path": "C:/Users/A/Downloads/q.gguf", "name": "q", "gb": 5.6}], "chosen": "x", "quick": None,
        "pulls": {}})
    output, is_error = registry.run("local_models", {"action": "list"}, lambda s: True)
    assert not is_error and "RTX 4050" in output and "q.gguf" in output and "Qwen 3.5 9B Abliterated" in output
    monkeypatch.setattr(localmodels, "gguf_files", lambda: [{"path": "C:/Users/A/Downloads/q.gguf"}])
    monkeypatch.setattr(localmodels, "import_gguf", lambda path: f"adding {path}")
    output, _ = registry.run("local_models", {"action": "add_file"}, lambda s: True)
    assert output == "adding C:/Users/A/Downloads/q.gguf"


def test_recall_finds_a_memory_by_meaning_with_the_laptops_memory_model(ctx, registry, monkeypatch):
    ollama_has(monkeypatch, "qwen3-embedding:0.6b", "huihui_ai/qwen3.5-abliterated:9b")
    monkeypatch.setattr(local, "_vectors", {})
    meaning = {"колата ми": [1, 0], "кола: карам синя Тойота": [0.9, 0.1], "храна: обичам баница": [0, 1]}
    monkeypatch.setattr(local, "_post", lambda path, body: {"embeddings": [meaning[t] for t in body["input"]]})
    ctx.store.insert("facts", topic="кола", fact="карам синя Тойота")
    ctx.store.insert("facts", topic="храна", fact="обичам баница")
    output, is_error = registry.run("recall", {"query": "колата ми"}, lambda s: True)
    assert not is_error and "Тойота" in str(output) and "баница" not in str(output)
    ollama_has(monkeypatch, "huihui_ai/qwen3.5-abliterated:9b")  # without the memory model: by words only
    output, _ = registry.run("recall", {"query": "колата ми"}, lambda s: True)
    assert "Тойота" not in str(output)


def test_the_memory_moves_to_the_new_laptop_with_its_keys(tmp_path):
    import sqlite3

    from jarvis import backup
    from jarvis.setup_wizard import read_env, write_env

    old = tmp_path / "old" / ".jarvis"
    (old / "skills").mkdir(parents=True)
    with sqlite3.connect(old / "jarvis.db") as db:
        db.execute("CREATE TABLE facts (fact TEXT)")
        db.execute("INSERT INTO facts VALUES ('пие кафе без захар')")
    (old / "LESSONS.md").write_text("- урок", encoding="utf-8")
    (old / "skills" / "mine.py").write_text("x = 1", encoding="utf-8")
    (old / "jarvis.log").write_text("big", encoding="utf-8")
    (old / "OllamaSetup.exe").write_bytes(b"MZ")
    old_env = tmp_path / "old.env"
    write_env(old_env, {"GEMINI_API_KEY": "g-old", "MISTRAL_API_KEY": "m-old"})
    archive = backup.backup(old, old_env, tmp_path / "Documents")
    names = set(__import__("zipfile").ZipFile(archive).namelist())
    assert {"jarvis.db", "LESSONS.md", "skills/mine.py", "project.env"} <= names and "jarvis.log" not in names
    assert "OllamaSetup.exe" not in names

    new = tmp_path / "new" / ".jarvis"
    new.mkdir(parents=True)
    (new / "jarvis.db").write_bytes(b"")
    new_env = tmp_path / "new.env"
    write_env(new_env, {"GEMINI_API_KEY": "g-new"})
    said = backup.restore(archive, new, new_env)
    assert "Добавени ключове в .env: 1" in said and ".jarvis-before-restore-" in said
    with sqlite3.connect(new / "jarvis.db") as db:
        assert db.execute("SELECT fact FROM facts").fetchone()[0] == "пие кафе без захар"
    env = read_env(new_env)
    assert env["GEMINI_API_KEY"] == "g-new" and env["MISTRAL_API_KEY"] == "m-old"  # the new laptop's key stays
    assert (new / "skills" / "mine.py").exists()


def test_a_9b_added_from_a_file_thinks_and_the_qwen_4b_takes_the_plain_answers(monkeypatch):
    ollama_has(monkeypatch, "qwen3.5-4b-q4_k_m:latest", "ornith-1.5-9b-q4_k_m:latest", "gemma-4-e4b-it-q4_k_m:latest")
    assert local.pick() == "ornith-1.5-9b-q4_k_m:latest"
    assert local.choose("local", []) == "qwen3.5-4b-q4_k_m:latest"


def test_a_picture_goes_to_a_model_that_can_see(monkeypatch):
    ollama_has(monkeypatch, "ornith-1.5-9b-q4_k_m:latest", "qwen3.5-4b-q4_k_m:latest", "gemma-4-e4b:latest")
    monkeypatch.setattr(local, "_abilities", {"ornith-1.5-9b-q4_k_m:latest": ["completion", "tools", "thinking"],
                                               "qwen3.5-4b-q4_k_m:latest": ["completion", "tools"],
                                               "gemma-4-e4b:latest": ["completion", "vision"]})
    monkeypatch.setattr(local, "_loaded", {"at": float("inf"), "models": []})
    assert local.choose("local", [{"name": "x"}], images=True) == "gemma-4-e4b:latest"
    assert local.choose("local", [{"name": "x"}]) == "ornith-1.5-9b-q4_k_m:latest"


def test_a_plain_answer_stays_on_the_big_model_when_only_it_is_in_memory(monkeypatch):
    ollama_has(monkeypatch, "qwen3.5-9b:latest", "qwen3.5-4b:latest")
    monkeypatch.setattr(local, "_loaded", {"at": float("inf"), "models": ["qwen3.5-9b:latest"]})
    assert local.choose("local", []) == "qwen3.5-9b:latest"  # no swap on a 6 GB card for one short answer
    monkeypatch.setattr(local, "_loaded", {"at": float("inf"), "models": []})
    assert local.choose("local", []) == "qwen3.5-4b:latest"


def test_a_model_that_cannot_use_tools_gives_way_for_work(monkeypatch):
    ollama_has(monkeypatch, "plain-9b:latest", "qwen3.5-4b:latest")
    monkeypatch.setattr(local, "_abilities", {"plain-9b:latest": ["completion"], "qwen3.5-4b:latest": ["completion", "tools"]})
    assert local.choose("local", [{"name": "x"}]) == "qwen3.5-4b:latest"


def test_ollama_is_tuned_for_a_small_graphics_card(monkeypatch):
    for key in localmodels.TUNING:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(localmodels, "WINDOWS", True)
    ran = []
    assert localmodels.tune(run=lambda cmd, **kw: ran.append(cmd)) == list(localmodels.TUNING)
    assert ["setx", "OLLAMA_KV_CACHE_TYPE", "q8_0"] in ran
    assert localmodels.tune(run=lambda cmd, **kw: ran.append(cmd)) == []  # once


def test_the_answer_is_shown_as_it_is_written(monkeypatch):
    ollama_has(monkeypatch, "qwen3.5-9b:latest")
    monkeypatch.setattr(local, "STREAM_EVERY", 0)
    words = ["Добър ", "вечер, ", "сър."]
    seen = []

    def lines(path, body):
        assert body["stream"] is True
        for w in words:
            yield {"message": {"content": w}, "done": False}
        yield {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 3}

    brain_ = local.LocalBrain(lines=lines)
    answer = brain_.create(model="local", system="s", messages=[{"role": "user", "content": "hi"}], tools=[],
                           max_tokens=100, effort="low", on_text=seen.append)
    assert seen == ["Добър", "Добър вечер,", "Добър вечер, сър."]
    assert answer.content[0].text == "Добър вечер, сър." and answer.usage["output"] == 3


def test_a_step_that_acts_takes_its_words_back(monkeypatch):
    ollama_has(monkeypatch, "qwen3.5-9b:latest")
    monkeypatch.setattr(local, "STREAM_EVERY", 0)
    seen = []

    def lines(path, body):
        yield {"message": {"content": "Отварям "}, "done": False}
        yield {"message": {"content": "", "tool_calls": [{"function": {"name": "open_page", "arguments": {"url": "x"}}}]},
               "done": False}
        yield {"message": {"content": ""}, "done": True}

    answer = local.LocalBrain(lines=lines).create(model="local", system="s", messages=[{"role": "user", "content": "x"}],
                                                  tools=[{"name": "open_page", "input_schema": {"type": "object"}}],
                                                  max_tokens=100, effort="low", on_text=seen.append)
    assert seen == ["Отварям", ""] and answer.stop_reason == "tool_use"


def test_the_growing_answer_keeps_one_event_per_chat():
    from jarvis.hub import EventLog

    log = EventLog()
    log.add("progress", chat=1, tool="x")
    for text in ("a", "ab", "abc"):
        log.add("partial", replace=True, chat=1, text=text)
    log.add("partial", replace=True, chat=2, text="z")
    assert [(e["kind"], e.get("text")) for e in log.events] == [("progress", None), ("partial", "abc"), ("partial", "z")]


def test_the_brain_slider_goes_from_the_fastest_model_to_the_smartest(monkeypatch):
    have = [{"id": "ornith-1.5-9b-q4_k_m:latest", "gb": 5.6}, {"id": "qwen3.5-4b-q4_k_m:latest", "gb": 2.7},
            {"id": "qwen3-embedding:0.6b", "gb": 0.6}]
    monkeypatch.setattr(localmodels, "installed", lambda: have)
    monkeypatch.setattr(localmodels, "hardware", lambda: {"gpu": "RTX 4050", "vram": 6, "ram": 16, "disk": 300})
    ollama_has(monkeypatch, *[m["id"] for m in have])
    sc = localmodels.scale("")
    assert [x["name"] for x in sc["stops"]] == ["Qwen 3.5 4B", "Ornith 1.5 9B"]  # the memory model is no brain
    assert sc["auto"] and sc["at"] == -1 and sc["stops"][1]["recommended"]
    fixed = localmodels.scale("qwen3.5-4b-q4_k_m:latest")
    assert not fixed["auto"] and fixed["at"] == 0


def test_a_model_too_big_for_the_laptop_is_not_on_the_slider_unless_it_is_the_chosen_one(monkeypatch):
    have = [{"id": "qwen3.5-4b-q4_k_m:latest", "gb": 2.7}, {"id": "qwen3.5-9b-abliterated-f16:latest", "gb": 17.9}]
    monkeypatch.setattr(localmodels, "installed", lambda: have)
    monkeypatch.setattr(localmodels, "hardware", lambda: {"gpu": "RTX 4050", "vram": 6, "ram": 15.4, "disk": 300})
    ollama_has(monkeypatch, *[m["id"] for m in have])
    assert [x["gb"] for x in localmodels.scale("")["stops"]] == [2.7]
    chosen = localmodels.scale("qwen3.5-9b-abliterated-f16:latest")
    assert [x["gb"] for x in chosen["stops"]] == [2.7, 17.9] and chosen["at"] == 1


def test_a_model_fixed_on_the_slider_takes_every_step(monkeypatch):
    ollama_has(monkeypatch, "qwen3.5-9b:latest", "qwen3.5-4b:latest")
    assert local.choose("local:qwen3.5-9b:latest", []) == "qwen3.5-9b:latest"  # no quick partner when fixed
    assert local.choose("local", []) == "qwen3.5-4b:latest"  # Авто


def test_the_slider_saves_a_model_or_auto(tmp_path, monkeypatch, settings, ctx, registry):
    from test_hub import call, make_hub

    monkeypatch.setattr(localmodels, "installed", lambda: [{"id": "qwen3.5-4b:latest", "gb": 2.7}])
    monkeypatch.setattr(localmodels, "version", lambda: "0.40.8")
    monkeypatch.setattr(localmodels, "hardware", lambda: {"gpu": "", "vram": 0, "ram": 16, "disk": 300})
    ollama_has(monkeypatch, "qwen3.5-4b:latest")
    _hub, port = make_hub(settings, ctx, registry, [])
    call(port, "/api/local-models", {"action": "use", "model": "qwen3.5-4b:latest"})
    assert json.loads(call(port, "/api/brain")[1])["at"] == 0
    call(port, "/api/local-models", {"action": "auto"})
    assert json.loads(call(port, "/api/brain")[1])["auto"] is True


def test_a_screenshot_is_described_by_the_model_with_eyes_and_the_thinking_model_keeps_the_task(monkeypatch):
    ollama_has(monkeypatch, "ornith-1.5-9b-q4_k_m:latest", "gemma-4-e4b:latest")
    monkeypatch.setattr(local, "_abilities", {"ornith-1.5-9b-q4_k_m:latest": ["completion", "tools"],
                                               "gemma-4-e4b:latest": ["completion", "vision", "tools"]})
    monkeypatch.setattr(local, "_described", {})
    asked = []

    def post(path, body):
        asked.append(body)
        if body["model"] == "gemma-4-e4b:latest":
            return {"message": {"content": "YouTube is open; the search box is at the top."}}
        return {"message": {"content": "Готово."}, "done_reason": "stop"}

    shot = {"type": "tool_result", "tool_use_id": "t1",
            "content": [{"type": "text", "text": "Screenshot"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}}]}
    messages = [{"role": "user", "content": "пусни най-новото видео на Хараламби Добрев"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "look_at_screen", "input": {}}]},
                {"role": "user", "content": [shot]}]
    brain = local.LocalBrain(post)
    for _ in range(2):  # the next step does not ask the eyes again
        brain.create(model="local", system="s", messages=messages, tools=[{"name": "x", "input_schema": {}}],
                     max_tokens=100, effort="low")
    seer = [b for b in asked if b["model"] == "gemma-4-e4b:latest"]
    assert len(seer) == 1 and "Хараламби" in seer[0]["messages"][1]["content"]
    thinking = [b for b in asked if b["model"] == "ornith-1.5-9b-q4_k_m:latest"]
    assert len(thinking) == 2 and not any("images" in m for m in thinking[0]["messages"])
    assert any("search box is at the top" in m["content"] for m in thinking[0]["messages"])


def test_a_photo_the_owner_sends_with_a_question_goes_to_the_model_with_eyes(monkeypatch):
    ollama_has(monkeypatch, "ornith-1.5-9b-q4_k_m:latest", "gemma-4-e4b:latest")
    monkeypatch.setattr(local, "_abilities", {"ornith-1.5-9b-q4_k_m:latest": ["completion", "tools"],
                                               "gemma-4-e4b:latest": ["completion", "vision", "tools"]})
    asked = []
    post = lambda path, body: asked.append(body) or {"message": {"content": "Котка."}, "done_reason": "stop"}
    photo = [{"type": "text", "text": "какво е това?"},
             {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "aGk="}}]
    local.LocalBrain(post).create(model="local", system="s", messages=[{"role": "user", "content": photo}],
                                  tools=[{"name": "x", "input_schema": {}}], max_tokens=100, effort="low")
    assert [b["model"] for b in asked] == ["gemma-4-e4b:latest"]
