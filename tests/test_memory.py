import unittest

from jarvis.memory import MEMORY_TOOL_NAMES, MEMORY_TOOLS, MemoryStore


class MemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_facts_survive_reopening(self):
        import tempfile, os

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "memory.db")
            s = MemoryStore(path)
            s.add("Анастас пие кафе без захар", "preference")
            s.close()
            s = MemoryStore(path)
            self.assertEqual([f.text for f in s.all()], ["Анастас пие кафе без захар"])
            s.close()

    def test_duplicate_is_not_stored_twice(self):
        a = self.store.add("Likes  jazz")
        b = self.store.add("likes jazz", "preference")
        self.assertEqual(a.id, b.id)
        self.assertEqual(len(self.store.all()), 1)
        self.assertEqual(self.store.get(a.id).category, "preference")

    def test_search_matches_inflected_bulgarian(self):
        self.store.add("Сестрата на Анастас се казва Мария", "person")
        self.store.add("Работи като програмист в София", "general")
        found = self.store.search("Как се казваше сестра ми?")
        self.assertEqual([f.text for f in found], ["Сестрата на Анастас се казва Мария"])

    def test_relevant_always_includes_profile(self):
        self.store.add("Името на потребителя е Анастас", "profile")
        self.store.add("Има куче на име Рекс", "person")
        self.store.add("Обича пица", "preference")
        texts = [f.text for f in self.store.relevant("Нахрани ли кучето?")]
        self.assertEqual(texts, ["Името на потребителя е Анастас", "Има куче на име Рекс"])

    def test_prompt_section_lists_facts(self):
        self.store.add("Lives in Plovdiv", "profile")
        section = self.store.prompt_section("hello")
        self.assertIn("# Long-term memory", section)
        self.assertIn("Lives in Plovdiv", section)

    def test_prompt_section_when_empty(self):
        self.assertIn("don't know anything", self.store.prompt_section("hello"))

    def test_tools(self):
        self.assertEqual(MEMORY_TOOL_NAMES, {"remember_fact", "forget_fact", "recall_facts"})
        for tool in MEMORY_TOOLS:
            self.assertEqual(tool["input_schema"]["type"], "object")

        out = self.store.handle_tool("remember_fact", {"text": "Birthday is 3 May", "category": "profile"})
        self.assertIn("[1]", out)
        out = self.store.handle_tool("remember_fact", {"text": "Birthday is 4 May", "replaces_id": 1})
        self.assertIn("4 May", out)
        self.assertIn("4 May", self.store.handle_tool("recall_facts", {"query": "birthday"}))
        self.assertEqual(self.store.handle_tool("forget_fact", {"id": 1}), "Forgot fact 1.")
        self.assertEqual(self.store.handle_tool("recall_facts", {}), "Nothing found.")
        self.assertIn("Error", self.store.handle_tool("remember_fact", {"text": "  "}))
        self.assertIn("No fact", self.store.handle_tool("forget_fact", {"id": 99}))


if __name__ == "__main__":
    unittest.main()
