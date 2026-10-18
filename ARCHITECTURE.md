# sunhub — Arquitetura

> Nome definido. Hub local de conhecimento (skills, ADRs, guidelines, antipatterns, docs)
> que vincula N repositórios Git, mantém tudo atualizado sozinho e expõe o conteúdo para
> CLI, VS Code, GitHub Copilot, Claude Code, Cursor etc.
> Escopo v1: Linux, Python, GUI mínima.

## 1. Problema e ideia central

Hoje: clonar repo, dar pull na mão, copiar skills para cada ferramenta, e o agente não
sabe achar "o ADR-014".

Ideia: **o sunhub é dono dos clones**. Você só cadastra a URL do repo; ele clona, atualiza,
indexa e **publica** o conteúdo por dois canais:

| Canal | Para quê | Quem consome |
|---|---|---|
| **Link de skills** (symlinks) | `SKILL.md` aparecem nos diretórios nativos de cada ferramenta | Copilot (`~/.copilot/skills`, `~/.agents/skills`), Claude Code (`~/.claude/skills`), etc. |
| **Servidor MCP** (stdio) | Buscar/ler ADRs, guidelines, antipatterns, docs sob demanda, com semântica ("ADR aceito", "superseded") | Qualquer cliente MCP: VS Code/Copilot, Claude Code, Cursor, Gemini CLI… |
| **CLI** | Uso manual, scripts, e fallback p/ ferramentas sem MCP | Terminal |

Diferença para "git pull + MCP filesystem": o MCP próprio entende *tipos* de documento
(ADR por ID/status, skill por nome, antipattern por tag), resolve referências e cita a fonte.

## 2. Visão geral

```
                         ┌───────────────────────────────────────────┐
  Repos remotos          │                 sunhub                   │
  (GitHub/GitLab/...)    │                                           │
  ┌────────┐  git fetch  │  ┌──────────┐   ┌─────────┐  ┌─────────┐  │
  │ repo A │────────────▶│  │  Sync    │──▶│ Indexer │─▶│ SQLite  │  │
  │ repo B │             │  │ Engine   │   │ (parse) │  │ + FTS5  │  │
  │ repo C │             │  └────┬─────┘   └─────────┘  └────┬────┘  │
  └────────┘             │       │ clones em                 │       │
                         │       ▼ ~/.local/share/sunhub    │       │
                         │  ┌──────────┐                     │       │
                         │  │ Linker   │ symlinks de skills  │       │
                         │  └────┬─────┘                     │       │
                         │       │            ┌──────────────▼────┐  │
                         │       │            │  Core API (lib)   │  │
                         │       │            └─┬──────┬───────┬──┘  │
                         └───────┼──────────────┼──────┼───────┼─────┘
                                 ▼              ▼      ▼       ▼
                     ~/.copilot/skills   MCP server   CLI     GUI local
                     ~/.claude/skills    (stdio)   `sunhub`  (web 127.0.0.1)
                     ~/.agents/skills        │
                                    VS Code · Copilot · Claude Code · Cursor
```

Princípio: **tudo passa pela Core API**. CLI, MCP, GUI e daemon são finos e só chamam o core.

## 3. Componentes

### 3.1 Source Manager (config)
- Arquivo: `~/.config/sunhub/config.toml` (editável à mão ou pela GUI/CLI).
- Cada *source* = um repositório (URL Git) **ou** pasta local.

```toml
[settings]
sync_interval_minutes = 30
link_targets = ["copilot", "claude", "agents"]   # onde publicar skills

[[sources]]
name     = "eng-guidelines"
url      = "git@github.com:acme/eng-guidelines.git"
branch   = "main"
priority = 10            # maior vence em conflito de nome
# ref    = "v2.3.0"      # opcional: pinar tag/commit
# paths  = ["docs/", "skills/"]   # opcional: sparse checkout

[[sources]]
name = "architecture-adrs"
url  = "git@github.com:acme/adrs.git"

[[sources]]
name  = "meu-rascunho"
local = "~/notes/kb"      # pasta local, sem git (só indexa/linka)
```

### 3.2 Sync Engine
- Clona em `~/.local/share/sunhub/sources/<name>/` (partial clone `--filter=blob:none` + sparse opcional).
- Atualiza com `git fetch` + `git merge --ff-only` (nunca sobrescreve; clone gerenciado é read-only por contrato).
- Usa o `git` do sistema via `subprocess` → herda SSH agent, credential helper, `gh auth`. **Sem guardar token próprio.**
- Guarda estado por source: último commit, hora, erro. Falha em um repo não bloqueia os outros.
- Dispara **re-index incremental** só nos arquivos alterados (`git diff --name-status old..new`).
- Agendamento: **systemd user timer** (`sunhub-sync.timer`) chamando `sunhub sync`; sem daemon residente na v1.
  Opcional: sync "lazy" quando o MCP inicia, se o último sync for mais velho que X min.

### 3.3 Indexer
Classifica cada arquivo em um *kind* por convenção, ou por `sunhub.toml` dentro do repo-fonte:

| kind | Detecção padrão | Metadados extraídos |
|---|---|---|
| `skill` | pasta com `SKILL.md` | name, description (frontmatter) |
| `adr` | `adr/`, `adrs/`, `decisions/`, `NNNN-*.md` | id, título, **status** (accepted/superseded/…), supersedes, data |
| `guideline` | `guidelines/`, `standards/` | título, tags |
| `antipattern` | `antipatterns/`, `anti-patterns/` | título, tags, severidade |
| `doc` | demais `.md`/`.rst`/`.txt` | título |

- Repo-fonte pode trazer `sunhub.toml` na raiz para sobrescrever caminhos/kinds.
- Armazenamento: **SQLite + FTS5** (zero dependência extra, rápido, arquivo único).
  Tabelas: `sources`, `documents(id, source, kind, path, title, status, tags, body, commit)`, `documents_fts`.
- Embeddings/busca semântica: **fora da v1**; interface `Searcher` permite plugar depois (ex.: `sqlite-vec`).

### 3.4 Linker (publicação de skills)
- Para cada skill indexada, cria **symlink** em cada alvo habilitado:
  `~/.copilot/skills/<nome>` → `.../sources/<source>/skills/<nome>`.
- Como é symlink para o working tree, **um `git pull` já atualiza a skill** — nada a copiar.
- Manifesto `~/.local/share/sunhub/links.json` registra o que foi criado → `unlink` remove só o que é dele
  (nunca toca em skills que você criou à mão).
- Conflito de nome entre sources: vence maior `priority`; o perdedor é linkado como `<source>--<nome>` e avisado.
- Alvos são **adapters** pequenos (um dict de caminho por ferramenta), fáceis de adicionar.

### 3.5 Servidor MCP (o coração para "usar x ADR como referência")
Transporte **stdio** (`sunhub mcp`), registrado uma vez em cada cliente. Ferramentas (somente leitura):

| Tool | Função |
|---|---|
| `sun_search(query, kind?, source?, limit?)` | Busca FTS; retorna título, kind, status, trecho, caminho |
| `sun_get(ref)` | Lê um documento por referência (ver §4) |
| `sun_get_adr(id)` | Atalho: ADR por ID, inclui status e cadeia de `supersedes` |
| `sun_list(kind, source?)` | Lista skills/ADRs/guidelines/antipatterns |
| `sun_check_antipatterns(text_or_path)` | Devolve antipatterns relevantes por tags/busca para revisar um trecho |
| `sun_status()` | Sources, último sync, erros |

Também expõe `resources` (`sunhub://adr/ADR-014`) e `prompts` opcionais. Toda resposta inclui `source`, `path` e `commit`
para o agente **citar a fonte**.

### 3.6 CLI (`sunhub`, via Typer)
```
sunhub add <url|pasta> [--name N] [--branch B] [--priority P]
sunhub remove <name>
sunhub list                       # sources + status
sunhub sync [name]                # pull + reindex + relink
sunhub search "<texto>" [--kind adr]
sunhub get adr:ADR-014            # imprime o doc (pipe p/ qualquer ferramenta)
sunhub context "<tarefa>" --adr 14 --skill api-standards   # monta bundle p/ colar em prompt
sunhub link | unlink
sunhub install-mcp [--vscode|--claude|--cursor]   # escreve a config MCP do cliente
sunhub init-project               # gera bloco AGENTS.md / copilot-instructions.md curto
sunhub timer enable|disable       # instala/remove o systemd user timer
sunhub gui                        # abre a GUI
sunhub doctor                     # diagnóstico (git, ssh, symlinks, mcp)
```
`sunhub context` é o fallback para qualquer ferramenta **sem** MCP: gera o texto pronto para colar.

### 3.7 GUI (mínima)
- **Web local** servida em `127.0.0.1` (FastAPI + Jinja + htmx, sem build de frontend) e aberta no navegador (`sunhub gui`).
  Motivo: Python puro, zero toolkit pesado, fácil de manter. (Alternativa: Tkinter, que é stdlib porém feio.)
- Telas: 
  1. **Sources** — lista, adicionar/remover, botão *Sync agora*, status/erro por repo.
  2. **Explorar** — busca + filtro por kind, preview do Markdown.
  3. **Integrações** — ligar/desligar alvos de skills, botão "Instalar MCP no VS Code/Claude/Cursor", resultado do `doctor`.
- A GUI **não tem lógica própria**: chama a Core API (mesmo processo) — ou os mesmos comandos da CLI.

## 4. Sintaxe de referência (o "usando como referência x ADR")

Resolver único, usado por MCP, CLI e GUI:

```
adr:ADR-014            adr:14
skill:api-standards
guideline:rest-naming
antipattern:god-object
eng-guidelines:docs/security.md        # <source>:<caminho>
```
Uso em prompt: *"Crie o endpoint de exportação seguindo `adr:14` e `skill:api-standards`, e rode `sun_check_antipatterns`."*
Como o agente tem o MCP + a skill-roteadora (abaixo), ele sabe chamar `sun_get`/`sun_get_adr`.

### Skill-roteadora (gerada pelo sunhub)
Uma única skill `sunhub` linkada globalmente, que ensina o agente: "para ADRs/guidelines/antipatterns use as tools
`sun_*`; cite sempre id, status e fonte; se nenhum ADR cobre, diga". Assim o `AGENTS.md` do projeto fica de 3 linhas.

## 5. Layout do repositório Python

```
sunhub/
├── pyproject.toml            # hatch/uv; entrypoint: sunhub = sunhub.cli:app
├── src/sunhub/
│   ├── core/
│   │   ├── config.py         # carrega/valida config.toml (pydantic)
│   │   ├── models.py         # Source, Document, Kind, Ref
│   │   ├── git.py            # wrapper de subprocess git
│   │   ├── sync.py           # SyncEngine
│   │   ├── indexer.py        # classificação + parsers (frontmatter, ADR)
│   │   ├── store.py          # SQLite/FTS5
│   │   ├── search.py         # interface Searcher (FTS hoje, vetorial depois)
│   │   ├── resolver.py       # "adr:14" -> Document
│   │   └── linker.py         # symlinks + manifesto
│   ├── adapters/             # alvos: copilot.py, claude.py, agents.py, vscode_mcp.py...
│   ├── mcp_server.py         # FastMCP (SDK oficial `mcp`)
│   ├── cli.py                # Typer
│   ├── gui/                  # app.py (FastAPI), templates/, static/
│   └── system/               # systemd.py (timer), paths.py (platformdirs/XDG)
└── tests/                    # repos Git de fixture criados em tmp_path
```

Dependências: `typer`, `pydantic`, `platformdirs`, `mcp`, `fastapi`+`uvicorn`+`jinja2`, `python-frontmatter`.
Distribuição: `pipx install sunhub` / `uv tool install sunhub`.

## 6. Dados e caminhos (XDG)

```
~/.config/sunhub/config.toml
~/.local/share/sunhub/sources/<name>/      # clones gerenciados
~/.local/share/sunhub/index.db             # SQLite
~/.local/share/sunhub/links.json           # manifesto do linker
~/.local/state/sunhub/sync.log
~/.config/systemd/user/sunhub-sync.{service,timer}
```

## 7. Fluxos principais

**Adicionar fonte:** `sunhub add URL` → grava config → clone → index → link de skills → (GUI mostra status).

**Atualização automática:** timer → `sunhub sync` → por source: fetch/ff-only → se HEAD mudou: diff → reindex incremental → relink (skills novas/removidas).
Skills existentes já refletem o pull via symlink.

**Uso no Copilot/VS Code:** `sunhub install-mcp --vscode` (uma vez) → no chat: "use `adr:14`" → agente chama `sun_get_adr` → responde citando ADR, status e commit.

**Ferramenta sem MCP:** `sunhub context ... | xclip` ou `sunhub get adr:14` → cola no prompt.

## 8. Decisões (mini-ADRs)

| # | Decisão | Motivo | Trade-off |
|---|---|---|---|
| D1 | sunhub é dono dos clones | Elimina clone/pull manual | Clones gerenciados não são para editar; para contribuir, faça no repo normal |
| D2 | `git` do sistema, não libgit2/GitPython | Herda auth (SSH/credential helper) sem tratar segredos | Depende de git instalado |
| D3 | Symlinks para skills | Atualização instantânea, sem duplicar | Ferramenta que não siga symlink → fallback `--copy` |
| D4 | MCP próprio em vez de MCP filesystem | Entende ADR/status/skill; resposta cita fonte | Mais código a manter |
| D5 | SQLite FTS5 na v1 | Zero infra, rápido o bastante p/ milhares de .md | Sem semântica; vetor fica para v2 |
| D6 | systemd user timer, sem daemon | Simples, robusto, sem processo residente | Granularidade de minutos (ok) |
| D7 | GUI web local | Python puro, sem toolkit pesado | Abre no navegador, não é app nativa |
| D8 | MCP somente leitura | Segurança | Agente não grava ADR (pode virar tool opt-in depois) |

## 9. Segurança
- MCP e GUI só **leitura**; GUI escuta apenas `127.0.0.1`.
- Resolver bloqueia path traversal e symlinks que saiam do clone.
- Conteúdo dos repos é **dado não confiável** (risco de prompt injection): respostas MCP marcam origem e o texto vem delimitado.
- Nenhum segredo armazenado; auth via git/SSH do usuário.

## 10. Roadmap

1. **MVP (CLI + core):** config, add/sync/list, indexer (skill/adr/doc), SQLite FTS, resolver, `get`/`search`.
2. **Integração:** Linker (Copilot/Claude/agents), servidor MCP, `install-mcp`, skill-roteadora, `init-project`.
3. **Automação:** systemd timer, `doctor`, `context`, sync lazy.
4. **GUI:** telas Sources / Explorar / Integrações.
5. **Depois:** busca semântica (sqlite-vec), webhooks/`git ls-remote` para sync mais barato, pinagem por projeto
   (`.sunhub.toml` no projeto escolhe versões), tool de criação de ADR, tray icon.

## 11. Pontos em aberto (decidir antes de codar)
- Convenção de ADR esperada: MADR, Nygard ou formato próprio? (define o parser)
- Repos privados: todos acessíveis por SSH/`gh auth`? (afeta `doctor`)
- Skills de diferentes sources com mesmo nome: política de prioridade basta ou quer namespacing sempre?
- Confirmar nos docs atuais de cada cliente os caminhos de skills e o formato de config MCP (mudam rápido) — fica isolado nos adapters.

## 12. Instalação, execução e vínculo com o sistema (decidido)

**Decisões do dono:** ADRs em formato próprio (parser tolerante, IA interpreta o corpo); repos públicos ou onde é colaborador;
colisão de nomes resolvida pela IA (→ sempre expor com prefixo `<source>--<nome>` além do nome curto quando único).

### 12.1 Instalação (fluxo alvo)
```
git clone https://github.com/<voce>/sunhub && cd sunhub
./install.sh          # instala o comando `sunhub` (editable) e roda `sunhub setup`
sunhub gui           # abre a GUI local; 1ª execução = assistente que pede as URLs
```
- `install.sh`: verifica `python3 >= 3.11` e `git`; instala `uv` se faltar; roda `uv tool install --editable .`
  → comando `sunhub` em `~/.local/bin` (garante PATH). *Editable*: `git pull` do próprio projeto atualiza a ferramenta.
- Sem sudo. Tudo dentro de `$HOME`.
- Depois da v1: publicar no PyPI (`pipx install sunhub`) sem mudar nada no resto.

### 12.2 `sunhub setup` / assistente da GUI
1. Cria o hub **visível**: `~/sunhub/` (`sources/` com os clones, `config.toml`, `index.db`) e grava `SUNHUB_HOME` em `~/.profile` e rc do shell.
2. Pede as URLs (GUI) → clona → indexa.
3. Detecta ferramentas instaladas (Copilot/VS Code, Claude Code, Cursor, Gemini CLI) e mostra checkboxes.
4. Para cada marcada:
   - symlinks de skills em `~/.copilot/skills`, `~/.claude/skills`, `~/.agents/skills`;
   - registra o MCP (`~/.config/Code/User/mcp.json`, `claude mcp add`, `~/.cursor/mcp.json`);
   - opcional: bloco gerenciado nas instruções globais (`~/.claude/CLAUDE.md`, etc.) apontando para a skill-roteadora.
5. Instala o systemd user timer de sync.

### 12.3 Segurança das alterações em arquivos do usuário
- Edita configs existentes só dentro de **blocos marcados** (`# >>> sunhub >>>` … `# <<< sunhub <<<`) ou chaves JSON próprias; faz **backup** antes.
- `sunhub uninstall` desfaz tudo via manifesto (links, blocos, timer). `sunhub doctor` verifica.

### 12.4 Limite importante
Não existe pasta "universal" que toda ferramenta enxergue. O vínculo é feito pelos **caminhos nativos de cada ferramenta**
(symlinks + registro MCP) e por `SUNHUB_HOME` + comando `sunhub` no PATH para terminal/scripts/editores.
Ferramenta nova = um adapter novo (~20 linhas).

## 13. Estado da implementação (v0.1.0)

Implementado conforme as seções acima, com estes ajustes:

- **Python 3.10+** (não 3.11): `tomli` como fallback de `tomllib`.
- **MCP SDK 1.x e 2.x** suportados (`FastMCP` foi renomeado para `MCPServer` no 2.x). Ferramentas: `sun_search`,
  `sun_get`, `sun_get_adr`, `sun_list`, `sun_check_antipatterns`, `sun_status`; recurso `sunhub://{kind}/{key}`.
  O servidor delimita cada documento em `<sunhub-document …>` e marca ADRs substituídos.
- **Nomes de skill repetidos** entre fontes: todas são publicadas como `<fonte>--<nome>` (sem vencedor); a IA escolhe.
- **GUI** sem htmx: formulários HTML + 20 linhas de JS. Proteções: só `127.0.0.1`/`localhost` (TrustedHost) e token
  por sessão em todo POST (CSRF).
- **Config e estado** dentro do hub: `~/sunhub/config.toml`, `~/sunhub/index.db`, `~/sunhub/.state/manifest.json`,
  `~/sunhub/.state/sync.log`. Lock de sync (`flock`) evita timer + MCP sincronizando ao mesmo tempo.
- **Sync lazy**: ao iniciar, o servidor MCP sincroniza em segundo plano se o último sync tiver mais de 2× o intervalo.
- **Clientes MCP**: VS Code, VS Code Insiders, Copilot CLI, Claude Code (`claude mcp add --scope user`), Cursor e
  Gemini CLI. JSONC (comentários) do VS Code é aceito; o arquivo original vai para `*.sunhub-bak-<data>`.

Módulos (`src/sunhub/`): `config`, `git`, `indexer`, `store`, `resolver`, `sync`, `linker`, `clients`, `systemd`,
`blocks`, `setup`, `doctor`, `render`, `context`, `mcp_server`, `cli`, `gui/`.

## 14. App de desktop (v0.2)

A GUI web (FastAPI) foi substituída por um **app de desktop nativo em Qt (PySide6-Essentials)**: sem navegador,
sem servidor HTTP e sem porta aberta.

- `src/sunhub/desktop/app.py`: janela com Fontes, Explorar, Integrações e o assistente da primeira execução.
  Operações demoradas rodam em `QThreadPool`; uma por vez; a janela não congela.
- `src/sunhub/actions.py`: operações compostas (primeiro setup, adicionar, sync, remover), compartilhadas.
- Distribuição: **`source app`** na raiz do clone. Cria `.venv/` local (uv, ou `python3 -m venv`, ou instala o uv),
  roda `sunhub setup` (hub, `SUNHUB_HOME`, `~/.local/bin/sunhub`, atalho `.desktop` + ícone) e abre o app em
  segundo plano. Reexecuções só abrem; mudanças no `pyproject.toml` disparam reinstalação. Instalações antigas via
  `uv tool` são migradas e as integrações (MCP, timer) são reapontadas para o novo Python.
- Desinstalação: tudo que foi criado entra no manifesto (`files`, `links`, `mcp`, `blocks`) e `sunhub uninstall` remove.
