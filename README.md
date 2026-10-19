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

### Sem clonar: AppImage

Cada versão publicada (tag `v*`) gera um **`Sunhub-<versão>-x86_64.AppImage`** na página de Releases do GitHub:
um arquivo só, com Python, Qt e o Sunhub dentro. Baixe, dê permissão e abra:

```bash
chmod +x Sunhub-*-x86_64.AppImage
./Sunhub-*-x86_64.AppImage          # abre o app e cria o atalho no menu e o comando `sunhub`
```

Guarde o arquivo num lugar fixo (ex.: `~/Applications`): o MCP, o timer e o atalho apontam para ele. Se mudar de
lugar, abra uma vez pelo novo caminho e tudo é reapontado. Para gerar localmente: `packaging/appimage/build.sh`.

### O app

| Tela | O que faz |
|---|---|
| **Fontes** | adicionar/remover repositórios, sincronizar, ver erro de acesso e atividade |
| **Explorar** | busca (por texto e, se ligada, por significado) e filtro por tipo; documento renderizado, status do ADR, copia a referência, abre o arquivo |
| **Atividade** | novidades de cada sync (ADR novo, ADR que virou superseded, skill nova…) e o uso do agente: mais lidos, buscas sem resposta, nunca lidos |
| **Integrações** | pastas de skills, MCP por ferramenta, timer, recursos (notificações, busca por significado, propostas de ADR, bandeja), qualidade das fontes, diagnóstico |

Ícone na **bandeja**: abrir, sincronizar agora, ver a próxima verificação. Fechar a janela mantém o Sunhub na bandeja
(dá para desligar em Integrações).

Atalhos: `Ctrl+1..4` (telas), `Ctrl+F` (buscar), `Ctrl+R` (sincronizar tudo), `F5` (recarregar).

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
sunhub add ~/Downloads/confluence-export.zip     # export do Confluence/Notion (HTML, Markdown, PDF)
sunhub list                                      # fontes e situação
sunhub sync                                      # fetch/ff-only, reindexa o que mudou, religa skills
sunhub search "paginação" --kind adr
sunhub get adr:14                                # imprime o documento (bom para pipe)
sunhub context "endpoint de pedidos" --adr 14 --skill api-standards | xclip -sel clip
sunhub install-mcp --detected                    # registra o MCP nos clientes detectados
sunhub link --targets copilot,claude --mode symlink
sunhub init-project .                            # bloco curto no AGENTS.md / copilot-instructions.md
sunhub timer enable --interval 5                  # a checagem é barata: dá para conferir a cada poucos minutos
sunhub changes --days 7                          # o que mudou nas fontes
sunhub usage                                     # o que o agente consultou, buscas sem resposta, nunca lidos
sunhub lint                                      # qualidade das fontes (ou: sunhub lint pasta/ na CI)
sunhub semantic on                               # busca por significado (baixa um modelo local de ~220 MB)
sunhub propose-adr -t "Usar Kafka" --context … --decision … --consequences …
sunhub init-project . --sources eng-guidelines,architecture-adrs   # perfil do projeto (.sunhub.toml)
sunhub config allow_proposals true               # configurações (sem argumentos lista todas)
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
| doc | qualquer outro `.md`, `.rst`, `.txt`, `.adoc`, `.html` (exports) e `.pdf` com texto |

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

## Recursos

**Novidades.** Cada sync registra o que mudou: documento novo, removido, alterado e **mudança de status** de ADR.
Aparece em Atividade, em `sunhub changes`, no `sun_changes` do agente e numa notificação do sistema quando o timer traz algo.

**Checagem barata.** Antes de baixar, o Sunhub pergunta ao remoto só o último commit de cada branch (`git ls-remote`).
Sem mudança, não há download: dá para usar intervalos de 1–5 minutos.

**Perfis por projeto.** Um `.sunhub.toml` na raiz do projeto define quais fontes valem ali; CLI e MCP respeitam
(o editor informa a pasta do projeto ao MCP; também dá para usar `SUNHUB_PROJECT`). `--all` ignora o perfil.

```toml
sources = ["eng-guidelines", "time-pagamentos-*"]
exclude = ["rascunhos"]
```

As pastas de skills são globais (cada ferramenta lê `~/.copilot/skills` etc.); o perfil filtra o que o MCP devolve.

**Qualidade (`sunhub lint`).** ADR sem número ou status, número repetido, "substituído por" um ADR que não existe,
ADR que diz substituir outro que continua aceito, skill sem `description`, documento vazio, link relativo quebrado.
Na CI do repositório de conhecimento:

```yaml
- uses: actions/setup-python@v5
  with: { python-version: "3.12" }
- run: pip install "sunhub @ git+https://github.com/<voce>/sunhub" && sunhub lint .
```

**Busca por significado.** Opcional e local: `sunhub semantic on` (ou em Integrações) instala o `fastembed` e baixa
um modelo multilíngue (~220 MB, uma vez). A busca passa a combinar texto e significado.

**Propostas de ADR.** Com `sunhub config allow_proposals true`, o agente pode chamar `sun_propose_adr` quando nada
cobre uma decisão: o Sunhub segue o formato dos ADRs do repositório (pasta, numeração, template), cria a branch
num `git worktree` separado, envia e abre o PR com o `gh` (ou mostra o link de comparação). O clone gerenciado não é tocado.

**Uso do agente.** Toda consulta pelo MCP é registrada localmente: mais lidos, buscas sem resposta (lacunas na
documentação) e documentos que ninguém lê. Veja em Atividade ou `sunhub usage`.

## Servidor MCP

`sunhub mcp` (stdio). Ferramentas: `sun_search`, `sun_get`, `sun_get_adr`, `sun_list`, `sun_check_antipatterns`,
`sun_changes`, `sun_status` e `sun_propose_adr` (a única que escreve, desligada por padrão); recurso
`sunhub://{kind}/{key}`. Configuração manual, se preferir:

```json
{ "servers": { "sunhub": { "type": "stdio", "command": "sunhub", "args": ["mcp"] } } }
```

## Desenvolvimento

```bash
source app --install
uv pip install --python .venv/bin/python pytest   # ou: .venv/bin/pip install pytest
.venv/bin/pytest                                   # a CI roda o mesmo (.github/workflows/ci.yml)
```

Arquitetura e decisões: [ARCHITECTURE.md](ARCHITECTURE.md).
