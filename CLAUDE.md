# Jarvis

## Плъгините (от видеото „4 plugins“)

Настроени са в `.claude/settings.json` и се инсталират сами, когато отвориш проекта в Claude Code (приеми „trust“ / „install“).

- **Ponytail** (`ponytail@ponytail`): най-простото решение, което работи. Преди да пишеш код: трябва ли изобщо, има ли го вече в проекта, има ли го в стандартната библиотека? Един ред вместо петдесет.
- **Agent Skills** на Addy Osmani (`agent-skills@addy-agent-skills`): работата минава през фазите `/spec` → `/plan` → `/build` → `/test` → `/review` → `/ship`; уменията се включват сами.
- **Graphify**: при всяко отваряне на сесия графът на кода се строи наново в `graphify-out/` (без AI, ~1 секунда). Питай графа (`graphify query "..."`) вместо да четеш файл по файл.
- **OmniRoute** (по желание, само на компютъра): `npx omniroute` пуска шлюза на `localhost:20128`, после `npx omniroute launch` от папката на проекта пуска Claude Code през него (безплатни/евтини модели, когато свърши лимитът). Не се слага в настройките на проекта, иначе Claude Code без пуснат OmniRoute не тръгва.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
