# Orquestra

Monte **equipes de agentes de IA** numa tela, ligando cards como num fluxograma, e veja tudo acontecer ao vivo.
Por baixo, cada equipe roda no [Claude Code](https://claude.com/claude-code) em modo headless (`claude -p`): um
**diretor** planeja e delega, **executores** fazem o trabalho como subagentes, e um **juiz** independente dá nota e
manda corrigir até atingir a meta.

## Como funciona

```
Eu ──► Zera ──► Diretor (Opus) ──► Programador (Sonnet)
                     │
                     └──► Juiz (Opus) ── nota < meta? ──► diretor corrige ──► juiz de novo
```

- **Fontes**: `Eu` (o pedido escrito na tela) e `Zera`, um assistente que reescreve o pedido curto como um
  briefing completo antes de ele chegar ao diretor (usa Gemini/Groq, grátis, e não gasta o plano do Claude).
- **Diretor**: o primeiro agente ligado a uma fonte. Planeja, delega à equipe (ferramenta `Agent`) e revisa.
- **Executores**: os agentes ligados ao diretor. Viram subagentes em `.claude/agents/` da pasta do trabalho, cada
  um com o seu modelo, papel e poderes.
- **Juiz** (opcional): uma sessão separada, que só lê. Avalia pelos critérios escritos no card, devolve
  `{nota, criterios, criticas}` em JSON e, abaixo da meta, o diretor corrige (até N rodadas).
- **Lições**: as críticas do juiz viram regras gerais em `licoes/<fluxo>.md`, que o diretor lê no começo de cada
  trabalho novo. A equipe aprende com os erros e o arquivo pode ser editado à mão.
- **Roteador**: ajustes pequenos ("troca a cor do botão") vão para um diretor Sonnet em vez do Opus.

### Recursos da tela
- Paleta de modelos (Opus, Sonnet, Haiku…) e de poderes por agente: 🌐 Internet (`WebSearch`/`WebFetch`) e
  ⌨️ Terminal (`Bash`). Sem poderes, o agente só lê e escreve arquivos da pasta.
- Linha do tempo ao vivo e painel por agente (o que pensou, pediu, fez e entregou, com o texto completo).
- Medidor de uso do plano (janela de 5 h e semanal), lido dos eventos `rate_limit_event` do Claude Code.
- Anexos de imagem (botão, arrastar ou Ctrl+V), que vão para `anexos/` da pasta do trabalho.
- Aba **Trabalhos**: um card por trabalho, com print, busca e um clique para **pedir um ajuste** na mesma pasta.
- Fluxos prontos: sites, pesquisa na internet, revisão de código e proposta comercial. Edite e salve os seus.

## Instalação

Requisitos: Python 3.11+ e o Claude Code instalado e logado (`claude` no terminal).

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Linux/macOS: .venv/bin/pip
cp .env.example .env                               # opcional: chaves do Gemini/Groq
python app.py                                      # abre http://localhost:8790
```

Os dados ficam em `~/Orquestra` (ou em `ORQUESTRA_DIR`): `fluxos.json`, `trabalhos/<data>_<titulo>/`,
`licoes/` e `_anexos/`. Cada trabalho guarda o histórico em `orquestra_log.json` e `.orquestra/<id>.json`.

## Custos

Os agentes usam o **login do Claude Code**: com assinatura (Pro/Max), consomem o limite do plano, não créditos de
API. A Orquestra remove variáveis `ANTHROPIC_*` do ambiente para não cair na cobrança por API sem querer. O juiz e
cada rodada de correção são sessões a mais, por isso o padrão é no máximo 2 rodadas.

## API local

| Rota | O que faz |
|---|---|
| `GET /api/fluxos` | fluxos salvos e prontos |
| `POST /api/fluxos` | salva um fluxo |
| `POST /api/executar` | `{fluxo, prompt, anexos?, continuar?}`: começa um trabalho (ou um ajuste) |
| `GET /api/execucoes/<id>?desde=N` | estado e eventos novos |
| `GET /api/trabalhos` | um item por trabalho (pasta) |
| `GET /api/uso` | uso do plano |
| `POST /api/parar` | para uma execução |

A tela só escuta em `localhost`.
