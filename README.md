# ☀️ Sunhub

Hub local de conhecimento para agentes de IA. Você vincula repositórios Git com **skills, ADRs, guidelines,
antipatterns e documentação**; o Sunhub clona, mantém tudo atualizado sozinho e entrega o conteúdo para
**GitHub Copilot (VS Code e CLI), Claude Code, Cursor, Gemini CLI, terminal e qualquer editor**.

```text
você:   "Crie o endpoint de pedidos usando como referência o adr:14 e a skill api-standards"
agente: → sun_get_adr("14")  → sun_get("skill:api-standards")  → sun_check_antipatterns(...)
        "Implementei com paginação por cursor (ADR-014, accepted; substitui ADR-009) … Fontes: adrs@7be03d1"
```

Linux · Python 3.10+ · git.

## Instalação e uso

```bash
git clone https://github.com/<voce>/sunhub
cd sunhub
source app
```

Na primeira vez o `app` prepara tudo sozinho (ambiente Python em `.venv/` dentro do clone, ~100 MB) e abre o
**app de desktop** do Sunhub: uma janela nativa (Qt), sem navegador e sem servidor web. O assistente pede as URLs
dos repositórios e as ferramentas a integrar. Depois disso:

- abra pelo **menu de aplicativos** (Sunhub) ou com `source app` / `sunhub app`;
- no terminal, o comando `sunhub` fica disponível (`sunhub --help`).

`source app --install` só instala/atualiza, sem abrir. Funciona em bash e zsh, sem sudo. Para atualizar o app:
`git pull` e `source app`. Para remover: `./uninstall.sh` (`--purge` apaga também `~/sunhub`).

Requisitos: Linux com interface gráfica, Python 3.10+ e git. Se a janela não abrir no Ubuntu/Debian:
`sudo apt install libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0`.

### O app

| Tela | O que faz |
|---|---|
| **Fontes** | adicionar/remover repositórios, sincronizar, ver erro de acesso e atividade |
| **Explorar** | busca e filtro por tipo; mostra o documento renderizado, status do ADR, copia a referência, abre o arquivo |
| **Integrações** | pastas de skills, registro do MCP por ferramenta, timer de atualização, diagnóstico, pasta do hub |

Atalhos: `Ctrl+1/2/3` (telas), `Ctrl+F` (buscar), `Ctrl+R` (sincronizar tudo), `F5` (recarregar).

## O que o Sunhub configura

| Onde | O quê |
|---|---|
| `~/sunhub/` | `config.toml`, `sources/` (clones gerenciados), `index.db` (busca), `.state/` (manifesto, log) |
| `~/.copilot/skills`, `~/.claude/skills`, `~/.agents/skills` | symlinks para as skills de cada fonte + a skill `sunhub`, que ensina o agente a consultar o hub |
| `~/.config/Code/User/mcp.json`, `~/.copilot/mcp-config.json`, `claude mcp`, `~/.cursor/mcp.json`, `~/.gemini/settings.json` | servidor MCP `sunhub` (com backup do arquivo antes de editar) |
| `~/.config/systemd/user/sunhub-sync.timer` | `sunhub sync` periódico |
| `~/.profile` (e `.bashrc`/`.zshrc`) | bloco marcado com `SUNHUB_HOME` e `~/.local/bin` no PATH |
| `~/.local/bin/sunhub`, `~/.local/share/applications/sunhub.desktop` | comando no terminal e atalho no menu |

Tudo fica registrado em `~/sunhub/.state/manifest.json`, e `sunhub uninstall` desfaz. O Sunhub nunca mexe em skills
ou configurações que não criou.

## Comandos

```bash
sunhub add https://github.com/acme/adrs          # vincula (clona + indexa + publica skills)
sunhub add git@github.com:acme/guias.git -b main --priority 10
sunhub add ~/notas/kb                            # pasta local, sem clone
sunhub list                                      # fontes e situação
sunhub sync                                      # fetch/ff-only, reindexa o que mudou, religa skills
sunhub search "paginação" --kind adr
sunhub get adr:14                                # imprime o documento (bom para pipe)
sunhub context "endpoint de pedidos" --adr 14 --skill api-standards | xclip -sel clip
sunhub install-mcp --detected                    # registra o MCP nos clientes detectados
sunhub link --targets copilot,claude --mode symlink
sunhub init-project .                            # bloco curto no AGENTS.md / copilot-instructions.md
sunhub timer enable --interval 30
sunhub doctor
sunhub app                                       # abre o app de desktop
```

## Referências que você pode usar no prompt

`adr:14` · `ADR-014` · `skill:api-standards` · `guideline:rest-naming` · `antipattern:god-object` ·
`doc:setup` · `<fonte>:<caminho/arquivo.md>`

## Como o conteúdo é reconhecido

Sem configuração, por convenção de pastas:

| Tipo | Detectado por |
|---|---|
| skill | pasta com `SKILL.md` (a pasta inteira é publicada) |
| adr | `adr/`, `adrs/`, `decisions/`, `decisoes/` ou arquivo `adr-NNN-*.md` |
| guideline | `guidelines/`, `standards/`, `conventions/`, `rules/`, `regras/`, `padroes/`, `diretrizes/`, `guias/` |
| antipattern | `antipatterns/`, `anti-patterns/`, `antipadroes/` |
| doc | qualquer outro `.md`, `.rst`, `.txt`, `.adoc` |

ADRs em formato livre: o número vem do front matter (`id:`), do nome do arquivo (`0014-...`, `ADR-014`) ou do
título; o status vem de `status:` no front matter, de uma linha `Status: Aceito` ou de uma seção `## Status`.
Termos em português (aceito, proposto, substituído, obsoleto, rejeitado) são reconhecidos.

Para outro layout, coloque um `sunhub.toml` na raiz do repositório de conhecimento:

```toml
ignore = ["rascunhos/**"]

[kinds]
adr = ["arquitetura/registros/**"]
guideline = ["docs/como-fazemos/**"]
```

## Servidor MCP

`sunhub mcp` (stdio, somente leitura). Ferramentas: `sun_search`, `sun_get`, `sun_get_adr`, `sun_list`,
`sun_check_antipatterns`, `sun_status`; recurso `sunhub://{kind}/{key}`. Configuração manual, se preferir:

```json
{ "servers": { "sunhub": { "type": "stdio", "command": "sunhub", "args": ["mcp"] } } }
```

## Desenvolvimento

```bash
source app --install
.venv/bin/pip install pytest   # ou: uv pip install --python .venv/bin/python pytest
.venv/bin/pytest
```

Arquitetura e decisões: [ARCHITECTURE.md](ARCHITECTURE.md).
