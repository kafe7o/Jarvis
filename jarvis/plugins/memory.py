"""Long-term memory about the user, and an address book."""

from __future__ import annotations

from ..tools import ToolRegistry, obj


def register(registry: ToolRegistry, ctx) -> None:
    store = ctx.store

    @registry.tool(
        "Remember a lasting fact about the user (preference, person, plan, account detail). "
        "Remembered facts are shown to you in every future conversation.",
        obj({"topic": ("string", "Short category, e.g. 'family', 'work', 'food'"), "fact": ("string", "The fact")}),
    )
    def remember(topic: str, fact: str):
        fid = store.insert("facts", topic=topic, fact=fact)
        return f"Remembered (id {fid})."

    @registry.tool(
        "Search remembered facts by keyword. Empty query lists everything.",
        obj({"query?": ("string", "Keyword")}),
    )
    def recall(query: str = ""):
        return store.query(
            "SELECT id, topic, fact, created FROM facts WHERE casefold(topic) LIKE casefold(?) OR casefold(fact) LIKE casefold(?) ORDER BY id",
            (f"%{query}%", f"%{query}%"),
        )

    @registry.tool("Forget a remembered fact by id.", obj({"fact_id": ("integer", "Fact id")}))
    def forget(fact_id: int):
        n = store.execute("DELETE FROM facts WHERE id=?", (fact_id,)).rowcount
        return "Forgotten." if n else "No such fact."

    @registry.tool(
        "Save or update a contact (name is unique). Use E.164 phone format, e.g. +359888123456.",
        obj({
            "name": ("string", "Contact name"),
            "phone?": ("string", "Phone number, E.164"),
            "email?": ("string", "E-mail address"),
            "notes?": ("string", "Notes"),
        }),
    )
    def save_contact(name: str, phone: str | None = None, email: str | None = None, notes: str | None = None):
        existing = store.query("SELECT * FROM contacts WHERE casefold(name)=casefold(?)", (name,))
        if existing:
            row = existing[0]
            store.execute(
                "UPDATE contacts SET phone=?, email=?, notes=? WHERE id=?",
                (phone or row["phone"], email or row["email"], notes or row["notes"], row["id"]),
            )
            return f"Updated contact {name}."
        store.insert("contacts", name=name, phone=phone, email=email, notes=notes)
        return f"Saved contact {name}."

    @registry.tool("Find contacts whose name, phone, e-mail or notes match a keyword.", obj({"query": ("string", "Keyword")}))
    def find_contact(query: str):
        q = f"%{query}%"
        return store.query(
            "SELECT name, phone, email, notes FROM contacts WHERE casefold(name) LIKE casefold(?) OR phone LIKE ? "
            "OR casefold(email) LIKE casefold(?) OR casefold(notes) LIKE casefold(?)",
            (q, q, q, q),
        )

    @registry.tool("List all contacts.", obj({}))
    def list_contacts():
        return store.query("SELECT name, phone, email, notes FROM contacts ORDER BY name")

    @registry.tool("Delete a contact by exact name.", obj({"name": ("string", "Contact name")}))
    def delete_contact(name: str):
        n = store.execute("DELETE FROM contacts WHERE casefold(name)=casefold(?)", (name,)).rowcount
        return "Deleted." if n else "No such contact."
