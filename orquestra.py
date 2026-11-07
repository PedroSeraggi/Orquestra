"""Orquestra: equipes de agentes do Claude Code montadas numa tela, com tudo acontecendo ao vivo.

Uma cadeia (fluxo) tem uma FONTE (Eu, ou o Zera, que melhora o pedido), um DIRETOR (o 1º agente ligado à fonte) e
EXECUTORES (os agentes ligados ao diretor). O diretor roda no Claude Code (`claude -p`, usando o login/plano do
Claude Code) e delega aos executores, que viram subagentes em .claude/agents/. Um JUIZ opcional (outra sessão)
avalia o resultado, dá nota e manda corrigir; as críticas viram LIÇÕES para os próximos trabalhos.
Os eventos (stream-json) vão para a tela em http://localhost:8790."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # chaves do Gemini/Groq no .env (opcional)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

BASE = os.getenv("ORQUESTRA_DIR") or os.path.join(os.path.expanduser("~"), "Orquestra")
FLUXOS = os.path.join(BASE, "fluxos.json")
CLAUDE = os.getenv("CLAUDE_BIN") or shutil.which("claude") or os.path.join(
    os.path.expanduser("~"), ".local", "bin", "claude.exe" if os.name == "nt" else "claude")
SEM_JANELA = 0x08000000 if os.name == "nt" else 0  # Windows: não abre janela de terminal
PORTA = 8790
WEB = os.path.join(os.path.dirname(__file__), "orquestra_web.html")

MODELOS = {  # nome na tela -> id do modelo
    "Opus 5.5": "claude-opus-5-5", "Fable 5.1": "claude-fable-5-1",
    "Sonnet 5.5": "claude-sonnet-5-5", "Haiku 5.5": "claude-haiku-5-5",
}

# Poderes que cada agente pode ter (marcados na tela). Sem poderes: só lê e escreve arquivos da pasta.
PODERES = {"internet": ["WebSearch", "WebFetch"], "terminal": ["Bash"]}
_EU_ZERA = [{"id": "eu", "tipo": "fonte", "nome": "Eu", "x": 60, "y": 160},
            {"id": "zera", "tipo": "fonte", "nome": "Zera", "x": 250, "y": 330}]


def _ag(id_, nome, modelo, x, y, papel, **poderes):
    return {"id": id_, "tipo": "agente", "nome": nome, "modelo": modelo, "x": x, "y": y, "papel": papel,
            "poderes": poderes}


def _juiz(x, y, criterios, modelo="Opus 5.5", meta=8, rodadas=2):
    """Juiz: avalia o trabalho do diretor numa sessão separada, dá nota e devolve críticas (não recebe ordens)."""
    return {"id": "juiz", "tipo": "agente", "juiz": True, "nome": "Juiz", "modelo": modelo, "x": x, "y": y,
            "papel": criterios, "meta": meta, "rodadas": rodadas, "poderes": {"terminal": True}}


# Modelos prontos (aparecem no menu; editar e salvar na tela substitui pela sua versão)
FLUXOS_PRONTOS = [
    {"id": "sites", "nome": "Sites (Opus dirige, Sonnet programa)",
     "nos": _EU_ZERA + [
         _ag("diretor", "Diretor", "Opus 5.5", 480, 200, "Planeja, divide o trabalho, revisa o que os executores "
             "fizeram (inclusive pelos prints do site) e cobra melhorias até ficar excelente.", terminal=True),
         _ag("dev", "Programador", "Sonnet 5.5", 800, 200, "Escreve e corrige o código exatamente como o diretor "
             "pediu (HTML, CSS e JS).", terminal=True),
         _juiz(480, 420, "Primeira impressão no celular (o cliente abre pelo WhatsApp); visual premium e "
               "coerente com o negócio; fotos boas e sem repetir; textos persuasivos, sem erro de português e sem "
               "inventar fatos; botões de WhatsApp funcionando; nada quebrado, cortado ou sobreposto.")],
     "ligacoes": [["eu", "zera"], ["zera", "diretor"], ["diretor", "dev"], ["diretor", "juiz"]]},
    {"id": "pesquisa", "nome": "Pesquisa na internet (relatório)",
     "nos": _EU_ZERA + [
         _ag("diretor", "Coordenador", "Opus 5.5", 480, 200, "Divide a pergunta em frentes de pesquisa, cobra fontes "
             "confiáveis e monta o relatório final (relatorio.md) com conclusão clara e links.", internet=True),
         _ag("pesq", "Pesquisador", "Sonnet 5.5", 800, 120, "Pesquisa na internet a frente pedida, lê as fontes e "
             "devolve fatos com link, data e o que é opinião vs. fato.", internet=True),
         _ag("red", "Redator", "Haiku 5.5", 800, 300, "Organiza o material em texto claro, em português, com "
             "tópicos curtos e as fontes citadas.")],
     "ligacoes": [["eu", "zera"], ["zera", "diretor"], ["diretor", "pesq"], ["diretor", "red"]]},
    {"id": "revisao", "nome": "Revisão de código",
     "nos": _EU_ZERA + [
         _ag("diretor", "Revisor", "Opus 5.5", 480, 200, "Lê o projeto, encontra bugs, falhas de segurança e código "
             "confuso; escreve REVISAO.md com os problemas em ordem de gravidade e pede as correções.", terminal=True),
         _ag("dev", "Corretor", "Sonnet 5.5", 800, 200, "Aplica as correções que o revisor pediu, sem mudar o que "
             "não foi pedido, e confirma rodando o projeto/testes quando houver.", terminal=True)],
     "ligacoes": [["eu", "zera"], ["zera", "diretor"], ["diretor", "dev"]]},
    {"id": "proposta", "nome": "Proposta comercial + mensagens",
     "nos": _EU_ZERA + [
         _ag("diretor", "Vendedor", "Opus 5.5", 480, 200, "Monta a estratégia: 3 opções de pacote com preço, "
             "argumentos e condição especial; revisa tudo até ficar persuasivo e honesto.", internet=True),
         _ag("red", "Redator", "Sonnet 5.5", 800, 200, "Escreve a proposta em HTML pronta para virar PDF (no "
             "padrão de uma proposta profissional) e as mensagens de WhatsApp para enviar junto.")],
     "ligacoes": [["eu", "zera"], ["zera", "diretor"], ["diretor", "red"]]},
]
FLUXO_SITES = FLUXOS_PRONTOS[0]

_execucoes: dict[str, dict] = {}  # id -> execução (com eventos)
uso_plano: dict = {}  # último rate_limit_event: {"five_hour": {...}, "seven_day": {...}, "t": ...}


def _guardar_uso(e: dict):
    info = e.get("rate_limit_info") or {}
    janelas = info.get("unifiedWindows") or {}
    if janelas:
        uso_plano.clear()
        uso_plano.update({k: {"usado": round(100 * (v.get("utilization") or 0)), "renova": v.get("resetsAt"),
                              "status": v.get("status")} for k, v in janelas.items()}, t=time.time())


def _env_limpo() -> dict:
    """Sem as variáveis de API/sessão herdadas: o Claude Code usa o login do plano de quem usa."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE"))}


def atualizar_uso() -> dict:
    """Consulta o uso do plano com uma chamada mínima ao Haiku (custa quase nada); guarda por 5 min."""
    if uso_plano and time.time() - uso_plano.get("t", 0) < 300:
        return uso_plano
    try:
        p = subprocess.run([CLAUDE, "-p", "responda só: ok", "--model", "haiku", "--output-format", "stream-json",
                            "--verbose"], env=_env_limpo(), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90, creationflags=SEM_JANELA, cwd=os.path.expanduser("~"))
        for linha in p.stdout.splitlines():
            if '"rate_limit_event"' in linha:
                _guardar_uso(json.loads(linha))
    except Exception as e:
        print("(uso do plano:", e, ")")
    return uso_plano
_trava = threading.Lock()


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"[^a-z0-9]+", "-", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip("-")[:40] or "x"


# ---------------- fluxos salvos ----------------
def fluxos() -> list[dict]:
    try:
        with open(FLUXOS, encoding="utf-8") as f:
            lista = json.load(f)
    except (OSError, ValueError):
        lista = []
    ids = {f["id"] for f in lista}
    return [f for f in FLUXOS_PRONTOS if f["id"] not in ids] + lista  # modelos prontos que você não editou


def _ferramentas(no: dict) -> list[str]:
    """Ferramentas de um agente: ler/escrever arquivos sempre; internet e terminal só se marcados na tela."""
    base = ["Read", "Write", "Edit", "Glob", "Grep"]
    for poder, ferr in PODERES.items():
        if (no.get("poderes") or {}).get(poder):
            base += ferr
    return base


def salvar_fluxo(fluxo: dict) -> dict:
    lista = [f for f in fluxos() if f["id"] != fluxo.get("id")]
    fluxo.setdefault("id", uuid.uuid4().hex[:8])
    lista.append(fluxo)
    os.makedirs(BASE, exist_ok=True)
    with open(FLUXOS, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)
    return fluxo


def _estrutura(fluxo: dict):
    """(diretor, [executores]) a partir das ligações desenhadas."""
    nos = {n["id"]: n for n in fluxo["nos"]}
    fontes = {n["id"] for n in fluxo["nos"] if n["tipo"] == "fonte"}
    diretor = next((nos[b] for a, b in fluxo["ligacoes"] if a in fontes and nos.get(b, {}).get("tipo") == "agente"),
                   None)
    if not diretor:
        raise ValueError("ligue uma fonte (Eu ou Zera) a um agente: ele será o diretor")
    execs = [nos[b] for a, b in fluxo["ligacoes"] if a == diretor["id"] and nos.get(b, {}).get("tipo") == "agente"
             and not nos[b].get("juiz")]
    return diretor, execs


def _juiz_do(fluxo: dict):
    """O juiz ligado ao diretor (se houver)."""
    nos = {n["id"]: n for n in fluxo["nos"]}
    diretor, _ = _estrutura(fluxo)
    return next((nos[b] for a, b in fluxo["ligacoes"] if a == diretor["id"] and nos.get(b, {}).get("juiz")), None)


# ---------------- Zera melhora o pedido ----------------
REFINAR = """Você é o ZERA, assistente do usuário. Ele escreveu um pedido curto para uma equipe de agentes de IA. Reescreva
como um BRIEFING claro e completo para o DIRETOR ({diretor}), que comanda: {equipe}.
Regras: mantenha TUDO o que o usuário disse; não invente fatos do cliente/negócio (nome, endereço, preços, telefone):
o que faltar, liste em "A confirmar"; estilo, estrutura e boas práticas podem vir como sugestão.
Organize em: Objetivo · Contexto · O que entregar · Requisitos e detalhes · Critérios de qualidade · A confirmar.
Em português, direto, sem enrolação. Responda SÓ com o briefing.

PEDIDO DO USUÁRIO:
{pedido}"""


def _refinar(pedido: str, diretor: dict, execs: list[dict]) -> str | None:
    """O Zera (Gemini, com o Groq de reserva) transforma o pedido curto num briefing para o diretor."""
    return _llm(REFINAR.format(diretor=f"{diretor['nome']}, {diretor.get('modelo')}", pedido=pedido,
                               equipe=", ".join(f"{e['nome']} ({e.get('modelo')})" for e in execs) or "ninguém"))


# ---------------- memória de lições (o que o juiz mais critica vira regra para os próximos) ----------------
LICOES = os.path.join(BASE, "licoes")
APRENDER = """Você mantém a lista de LIÇÕES de uma equipe de IA que faz trabalhos do tipo "{fluxo}". Um juiz avaliou
o último trabalho. Junte as lições atuais com as críticas novas numa lista de no máximo 15 regras CURTAS e GERAIS
(valem para qualquer cliente: nada de nomes, cores ou textos de um cliente específico). Una as repetidas e deixe
primeiro as mais importantes/mais repetidas. Responda SÓ com a lista, uma regra por linha começando com "- ".

LIÇÕES ATUAIS:
{atuais}

CRÍTICAS NOVAS DO JUIZ (nota {nota}/10):
{criticas}"""


def licoes(fluxo_id: str) -> str:
    try:
        with open(os.path.join(LICOES, f"{fluxo_id}.md"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _aprender(fluxo_id: str, fluxo_nome: str, notas: list[dict]):
    """Depois do juiz: as críticas viram regras gerais em Orquestra/licoes/<fluxo>.md (dá para editar à mão)."""
    criticas = [c for n in notas for c in n.get("criticas") or []]
    if not criticas:
        return
    novo = _llm(APRENDER.format(fluxo=fluxo_nome, atuais=licoes(fluxo_id) or "(nenhuma ainda)",
                                nota=notas[0].get("nota"), criticas="\n".join(f"- {c}" for c in criticas)))
    linhas = [l.strip() for l in (novo or "").splitlines() if l.strip().startswith("- ")][:15]
    if linhas:
        os.makedirs(LICOES, exist_ok=True)
        with open(os.path.join(LICOES, f"{fluxo_id}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _llm(texto: str) -> str | None:
    """Chamada rápida e gratuita (Gemini, com o Groq de reserva): não gasta o plano do Claude.
    Sem GEMINI_API_KEY nem GROQ_API_KEY no .env, devolve None (o pedido vai ao diretor como está)."""
    import requests
    chave = os.getenv("GEMINI_API_KEY")
    if chave:
        for modelo in [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash,gemini-3.5-flash-lite")
                       .split(",") if m.strip()]:
            try:
                r = requests.post(GEMINI_API.format(model=modelo), timeout=40, headers={"x-goog-api-key": chave},
                                  json={"contents": [{"role": "user", "parts": [{"text": texto}]}]})
                if r.ok:
                    partes = r.json()["candidates"][0]["content"].get("parts", [])
                    saida = "".join(p.get("text", "") for p in partes if not p.get("thought")).strip()
                    if saida:
                        return saida
            except Exception:
                continue
    if os.getenv("GROQ_API_KEY"):
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=40,
                              headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
                              json={"model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                                    "messages": [{"role": "user", "content": texto}]})
            if r.ok:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            pass
    return None


# ---------------- execução ----------------
ANEXOS = os.path.join(BASE, "_anexos")  # imagens enviadas pela tela, antes de irem para a pasta do trabalho


def guardar_anexo(nome: str, dados: str) -> dict:
    """Recebe uma imagem (data URL) da tela e guarda até a execução começar."""
    import base64
    cabeca, _, b64 = dados.partition(",")
    if not cabeca.startswith("data:image/"):
        return {"erro": "só imagens"}
    ext = os.path.splitext(nome)[1].lower() or "." + cabeca[11:].split(";")[0]
    nome = _slug(os.path.splitext(nome)[0]) + ext
    aid = uuid.uuid4().hex[:8]
    os.makedirs(ANEXOS, exist_ok=True)
    with open(os.path.join(ANEXOS, f"{aid}_{nome}"), "wb") as f:
        f.write(base64.b64decode(b64))
    return {"id": aid, "nome": nome}


def executar(fluxo_id: str, prompt: str, origem: str = "eu", pasta: str = "", titulo: str = "",
             ao_terminar=None, anexos: list | None = None, molde: str = "", modelo_diretor: str = "") -> dict:
    """Começa uma execução em segundo plano. Devolve o id e a pasta de trabalho."""
    fluxo = next((f for f in fluxos() if f["id"] == fluxo_id), None)
    if not fluxo:
        return {"erro": "fluxo não encontrado"}
    try:
        diretor, execs = _estrutura(fluxo)
    except ValueError as e:
        return {"erro": str(e)}
    if modelo_diretor:  # roteador: ajuste pequeno não precisa do Opus
        diretor = {**diretor, "modelo": modelo_diretor}
    if not os.path.exists(CLAUDE):
        return {"erro": "Claude Code não encontrado neste PC"}
    # Eu -> Zera -> diretor: o Zera reescreve o pedido antes (só para pedidos escritos na tela)
    zera = next((n for n in fluxo["nos"] if n["tipo"] == "fonte" and n["nome"] == "Zera"), None)
    refinar = (origem == "eu" and zera is not None and [zera["id"], diretor["id"]] in fluxo["ligacoes"]
               and any(b == zera["id"] for a, b in fluxo["ligacoes"]))
    titulo = titulo or prompt.strip().split("\n")[0][:60]
    pasta = pasta or os.path.join(BASE, "trabalhos", datetime.now().strftime("%Y-%m-%d_%H%M") + "_" + _slug(titulo))
    os.makedirs(os.path.join(pasta, ".claude", "agents"), exist_ok=True)
    nota_anexos = ""
    if anexos:  # imagens do pedido (logo, prints do Instagram...) vão para anexos/ e o pedido diz o que é cada uma
        import shutil
        os.makedirs(os.path.join(pasta, "anexos"), exist_ok=True)
        linhas = []
        for a in anexos:
            origem_arq = os.path.join(ANEXOS, f"{a['id']}_{a['nome']}")
            if os.path.exists(origem_arq):
                shutil.move(origem_arq, os.path.join(pasta, "anexos", a["nome"]))
                linhas.append(f"- anexos/{a['nome']}: {a.get('descricao') or 'imagem de referência'}")
        if linhas:
            nota_anexos = ("\n\nIMAGENS ANEXADAS pelo usuário (abra cada uma com a ferramenta Read e use como "
                           "referência; logo e cores do cliente devem ser respeitados):\n" + "\n".join(linhas))
            prompt = prompt + nota_anexos
    # molde: texto fixo em volta do pedido (ex.: as regras do fluxo de sites), com {briefing} no lugar do pedido.
    # O Zera melhora só o pedido; o molde vai inteiro ao diretor.
    pedido = prompt
    if molde:
        prompt = molde.replace("{briefing}", pedido)
    nomes = {}
    for e in execs:
        nome = _slug(e["nome"]) or e["id"]
        nomes[e["id"]] = nome
        with open(os.path.join(pasta, ".claude", "agents", nome + ".md"), "w", encoding="utf-8") as f:
            f.write(f"---\nname: {nome}\ndescription: {e.get('papel') or e['nome']}\n"
                    f"model: {MODELOS.get(e.get('modelo'), 'haiku')}\ntools: {', '.join(_ferramentas(e))}\n---\n"
                    f"{e.get('papel') or ''}\nTrabalhe só nesta pasta. Se houver a pasta anexos/, olhe as imagens dela "
                    "quando forem úteis (Read abre imagens). Ao terminar, resuma em poucas linhas o que fez.\n")
    equipe = "\n".join(f"- `{nomes[e['id']]}` ({e.get('modelo')}): {e.get('papel') or e['nome']}" for e in execs)
    sistema = (f"Você é o DIRETOR ({diretor.get('papel') or ''}). "
               + (f"Sua equipe (delegue com a ferramenta Agent, subagent_type = nome):\n{equipe}\n"
                  "Você planeja e revisa; quem executa é a equipe. Depois de cada entrega, leia o resultado e cobre "
                  "melhorias concretas até ficar excelente." if execs else "Você trabalha sozinho.")
               + " Trabalhe só na pasta atual. No fim, responda em 3 a 5 linhas o que foi feito e o que falta.")
    if licoes(fluxo_id):
        sistema += ("\n\nLIÇÕES dos trabalhos anteriores (o juiz de qualidade já cobrou isto; cumpra desde o início "
                    "e passe à equipe o que for dela):\n" + licoes(fluxo_id))
    rid = uuid.uuid4().hex[:10]
    ex = {"id": rid, "fluxo": fluxo["nome"], "fluxo_id": fluxo_id, "titulo": titulo, "prompt": prompt,
          "origem": origem, "pasta": pasta, "inicio": time.time(), "fim": None, "status": "rodando",
          "agentes": {diretor["id"]: {"nome": diretor["nome"], "modelo": diretor.get("modelo"), "estado": "trabalhando",
                                      "acoes": 0, "agora": "lendo o pedido"},
                      **{e["id"]: {"nome": e["nome"], "modelo": e.get("modelo"), "estado": "esperando", "acoes": 0,
                                   "agora": ""} for e in execs}},
          "eventos": [], "arquivos": [], "resultado": "", "custo": None, "entregas": 0}
    if refinar:
        ex["agentes"] = {zera["id"]: {"nome": "Zera", "modelo": "Zera", "estado": "trabalhando", "acoes": 0,
                                      "agora": "melhorando o seu pedido"}, **ex["agentes"]}
        ex["agentes"][diretor["id"]].update(estado="esperando", agora="")
    with _trava:
        _execucoes[rid] = ex
    por_nome = {v: k for k, v in nomes.items()}
    juiz = _juiz_do(fluxo) if molde != AJUSTE else None  # ajuste pequeno não passa pelo juiz
    meta = float((juiz or {}).get("meta") or 8)
    rodadas = int((juiz or {}).get("rodadas") or 2)
    if juiz:
        ex["agentes"][juiz["id"]] = {"nome": juiz["nome"], "modelo": juiz.get("modelo"), "estado": "esperando",
                                     "acoes": 0, "agora": "espera o trabalho ficar pronto"}

    def evento(agente, tipo, texto, completo=""):
        """texto = resumo para a linha do tempo; completo = o texto inteiro (briefing, pedido à equipe...), que
        aparece no painel do agente."""
        e = {"t": round(time.time() - ex["inicio"], 1), "agente": agente, "tipo": tipo, "texto": texto[:400]}
        if completo or len(texto) > 400:
            e["completo"] = (completo or texto)[:30000]
        ex["eventos"].append(e)

    def rodar(texto: str, quem_id: str, modelo: str, ferramentas: list[str], sistema_txt: str) -> int:
        """Uma sessão do Claude Code (diretor, juiz ou rodada de correção), com os eventos indo para a tela."""
        env = _env_limpo()
        env.update(PYTHONIOENCODING="utf-8", **(ex.get("env") or {}))
        cmd = [CLAUDE, "-p", texto, "--model", MODELOS.get(modelo, "opus"), "--append-system-prompt", sistema_txt,
               "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose",
               "--allowedTools", *ferramentas, *ex.get("extra_args", [])]
        delegacoes = {}  # id da chamada Agent -> id do nó executor
        ex["resultado_parcial"] = ""
        p = subprocess.Popen(cmd, cwd=pasta, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
        ex["pid"] = p.pid
        for linha in p.stdout:
            try:
                e = json.loads(linha)
            except ValueError:
                continue
            if e.get("type") == "rate_limit_event":
                _guardar_uso(e)
                continue
            if e.get("type") == "result":
                ex["resultado_parcial"] = e.get("result", "")
                continue
            if e.get("type") == "system" and e.get("tool_use_id") in delegacoes:
                # o Claude Code avisa início, progresso e fim de cada tarefa delegada
                alvo = delegacoes[e["tool_use_id"]]
                a = ex["agentes"][alvo]
                if e.get("subtype") == "task_started":
                    a.update(estado="trabalhando", agora=e.get("description", ""))
                elif e.get("subtype") == "task_progress":
                    a.update(estado="trabalhando", agora=e.get("description", a["agora"]))
                elif e.get("subtype") == "task_notification":
                    ok = e.get("status") == "completed"
                    a.update(estado="entregou" if ok else "erro", agora="entregou ao diretor" if ok else "falhou")
                    ex["agentes"][quem_id]["agora"] = "revisando a entrega"
                    resumo_ent = ("Entregou: " if ok else "Falhou: ") + (e.get("summary") or "")
                    evento(alvo, "entregou", resumo_ent[:300], resumo_ent)
                continue
            if e.get("type") != "assistant":
                continue
            pai = e.get("parent_tool_use_id")
            quem = delegacoes.get(pai, quem_id) if pai else quem_id
            ag = ex["agentes"][quem]
            if pai:  # evento do executor: ele está trabalhando (inclusive depois de um pedido de ajuste)
                ag["estado"] = "trabalhando"
            for c in (e.get("message") or {}).get("content") or []:
                if c.get("type") == "text" and c.get("text", "").strip():
                    ag["agora"] = "pensando"
                    evento(quem, "texto", c["text"].strip())
                elif c.get("type") == "tool_use":
                    ag["acoes"] += 1
                    nome, inp = c.get("name"), c.get("input") or {}
                    if nome in ("Agent", "Task"):
                        alvo = por_nome.get(inp.get("subagent_type"), None)
                        if alvo:
                            delegacoes[c["id"]] = alvo
                            ex["agentes"][alvo]["estado"] = "trabalhando"
                            ex["agentes"][alvo]["agora"] = inp.get("description", "")
                            ex["entregas"] += 1
                        ag["agora"] = f"delegou: {inp.get('description', '')}"
                        cabeca = f"→ {ex['agentes'].get(alvo, {}).get('nome', '?')}: {inp.get('description', '')}"
                        evento(quem, "delegou", f"{cabeca}\n{inp.get('prompt', '')[:300]}",
                               f"{cabeca}\n\n{inp.get('prompt', '')}")
                    else:
                        arq = inp.get("file_path") or inp.get("path") or ""
                        rel = os.path.relpath(arq, pasta) if arq and arq.startswith(pasta) else arq
                        desc = {"Write": f"escrevendo {rel}", "Edit": f"editando {rel}", "Read": f"lendo {rel}",
                                "Glob": "procurando arquivos", "Grep": "procurando no código",
                                "Bash": f"rodando: {str(inp.get('command', ''))[:80]}",
                                "WebFetch": f"abrindo {inp.get('url', '')[:60]}",
                                "SendMessage": f"pediu um ajuste: {str(inp.get('message', ''))[:200]}"}.get(nome, nome)
                        ag["agora"] = desc
                        if nome in ("Write", "Edit") and rel and rel not in ex["arquivos"]:
                            ex["arquivos"].append(rel)
                        evento(quem, "acao", desc)
        p.wait()
        return p.returncode

    def avaliar(rodada: int) -> dict:
        """O juiz (sessão separada, só lê e confere) dá nota de 0 a 10 com críticas concretas."""
        jid = juiz["id"]
        ex["agentes"][jid].update(estado="trabalhando", agora=f"avaliando (rodada {rodada})")
        evento(jid, "inicio", f"Rodada {rodada}: avaliando o trabalho com olhos de cliente exigente…")
        texto = JUIZ.replace("{criterios}", juiz.get("papel") or "").replace("{pedido}", ex.get("prompt_refinado")
                                                                                 or pedido)
        rc = rodar(texto, jid, juiz.get("modelo"), ["Read", "Glob", "Grep", "Bash(python:*)"],
                   "Você é o JUIZ independente: avalia, não edita nenhum arquivo.")
        bruto = ex["resultado_parcial"]
        m = re.search(r"\{.*\}", bruto, re.S)
        try:
            av = json.loads(m.group(0)) if m else {}
        except ValueError:
            av = {}
        nota = av.get("nota")
        if rc != 0 or not isinstance(nota, (int, float)):
            evento(jid, "erro", "Não consegui ler a nota do juiz; sigo com o trabalho como está.", bruto)
            ex["agentes"][jid].update(estado="erro", agora="sem nota")
            return {}
        criticas = av.get("criticas") or []
        resumo = f"Nota {nota}/10 (meta {meta}). " + ("; ".join(criticas[:3]) if criticas else "Sem críticas.")
        evento(jid, "nota", resumo, json.dumps(av, ensure_ascii=False, indent=1))
        ex.setdefault("notas", []).append({"rodada": rodada, "nota": nota, "criticas": criticas})
        ex["agentes"][jid].update(estado="entregou", agora=f"nota {nota}/10")
        return av

    def tarefa():
        if refinar:
            evento(zera["id"], "inicio", "Recebeu o seu pedido e está escrevendo um briefing melhor para o diretor…")
            melhor = _refinar(pedido, diretor, execs)
            z = ex["agentes"][zera["id"]]
            if melhor:
                melhor += nota_anexos  # a lista de imagens vai sempre, mesmo se o briefing resumir
                ex["prompt_inicial"] = molde.replace("{briefing}", melhor) if molde else melhor
                ex["prompt_refinado"] = melhor
                evento(zera["id"], "delegou", f"→ {diretor['nome']}: briefing pronto\n{melhor}")
                z.update(estado="entregou", agora="briefing entregue ao diretor", acoes=1)
            else:
                evento(zera["id"], "erro", "Não consegui melhorar agora (Gemini/Groq fora); mandei o pedido como está.")
                z.update(estado="erro", agora="mandou o pedido original")
            ex["agentes"][diretor["id"]].update(estado="trabalhando", agora="lendo o briefing")
        evento(diretor["id"], "inicio", f"Recebeu o pedido de {'Zera' if origem == 'zera' or refinar else 'você'}")
        ferr_diretor = [*_ferramentas(diretor), "Agent", "Task", "SendMessage"]
        try:
            rc = rodar(ex.get("prompt_inicial") or prompt, diretor["id"], diretor.get("modelo"), ferr_diretor, sistema)
            resultados = [ex["resultado_parcial"]]
            # juiz: avalia; abaixo da meta, o diretor corrige com as críticas; repete até a meta ou o limite
            for rodada in range(1, (rodadas if juiz and rc == 0 else 0) + 1):
                av = avaliar(rodada)
                if not av or av["nota"] >= meta or rodada == rodadas:
                    break
                ex["agentes"][diretor["id"]].update(estado="trabalhando", agora=f"corrigindo (rodada {rodada})")
                criticas = "\n".join(f"- {c}" for c in av.get("criticas") or [])
                evento(diretor["id"], "inicio", f"O juiz deu {av['nota']}/10: corrigindo o que ele apontou…")
                rc = rodar(CORRECAO.replace("{nota}", str(av["nota"])).replace("{meta}", str(meta))
                           .replace("{criticas}", criticas), diretor["id"], diretor.get("modelo"), ferr_diretor,
                           sistema)
                resultados.append(ex["resultado_parcial"])
                if rc != 0:
                    break
            ex["resultado"] = resultados[-1] + (
                f"\n\nNota final do juiz: {ex['notas'][-1]['nota']}/10 (rodadas: {len(ex['notas'])})."
                if ex.get("notas") else "")
            ex["status"] = "concluido" if rc == 0 else "erro"
            if ex.get("notas"):
                threading.Thread(target=_aprender, args=(fluxo_id, fluxo["nome"], ex["notas"]), daemon=True).start()
        except Exception as erro:
            ex["status"] = "erro"
            evento(diretor["id"], "erro", str(erro))
        finally:
            ex["fim"] = time.time()
            for a in ex["agentes"].values():
                a["estado"] = "pronto" if ex["status"] == "concluido" else a["estado"]
                a["agora"] = ""
            evento(diretor["id"], "fim", ex["resultado"] or ("Terminou." if ex["status"] == "concluido" else
                                                             "Parou com erro."))
            os.makedirs(os.path.join(pasta, ".orquestra"), exist_ok=True)
            for arq in ("orquestra_log.json", os.path.join(".orquestra", f"{ex['id']}.json")):  # último + histórico
                with open(os.path.join(pasta, arq), "w", encoding="utf-8") as f:
                    json.dump({k: v for k, v in ex.items() if k not in ("env", "_iniciar")}, f, ensure_ascii=False,
                              indent=1, default=str)  # sem a chave do Pixabay
            if ao_terminar:
                ao_terminar(ex)

    ex["_iniciar"] = tarefa
    return ex


def iniciar(ex: dict) -> dict:
    threading.Thread(target=ex.pop("_iniciar"), daemon=True).start()
    return {"ok": True, "id": ex["id"], "pasta": ex["pasta"]}


def resumo(ex: dict, desde: int = 0) -> dict:
    agora = (ex["fim"] or time.time()) - ex["inicio"]
    return {k: v for k, v in ex.items() if k not in ("eventos", "_iniciar", "env", "extra_args", "pid")} | {
        "duracao": round(agora), "eventos": ex["eventos"][desde:], "total_eventos": len(ex["eventos"])}


def trabalhos() -> list[dict]:
    """Um item por trabalho (pasta): o nome do cliente, o último ajuste e quantos ajustes já teve."""
    grupos: dict[str, list] = {}
    for e in _execucoes.values():
        grupos.setdefault(os.path.normcase(os.path.normpath(e["pasta"])), []).append(e)
    lista = []
    for pasta, exs in grupos.items():
        exs.sort(key=lambda e: e["inicio"])
        ultimo, primeiro = exs[-1], exs[0]
        nome = primeiro["titulo"]
        for pre in ("Ajuste: ", "Site: "):
            nome = nome.removeprefix(pre)
        notas = [n["nota"] for e in exs for n in e.get("notas") or []]
        lista.append({"id": ultimo["id"], "nome": nome, "fluxo": primeiro.get("fluxo", ""),
                      "fluxo_id": primeiro.get("fluxo_id"), "criado": primeiro["inicio"], "mexido": ultimo["inicio"],
                      "ajustes": len(exs) - 1, "status": ultimo["status"], "nota": notas[-1] if notas else None,
                      "print": os.path.exists(os.path.join(ultimo["pasta"], "prints", "celular-1.png"))})
    return sorted(lista, key=lambda t: -t["mexido"])


def parar(rid: str) -> dict:
    ex = _execucoes.get(rid)
    if not ex or ex["status"] != "rodando" or not ex.get("pid"):
        return {"erro": "não está rodando"}
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(ex["pid"]), "/T", "/F"], capture_output=True,
                       creationflags=SEM_JANELA)
    else:
        import signal
        os.kill(ex["pid"], signal.SIGTERM)
    return {"ok": True}


# ---------------- página ----------------
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, dados, codigo=200):
        corpo = json.dumps(dados, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            corpo = open(WEB, "rb").read()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(corpo)
        elif self.path == "/api/fluxos":
            self._json({"fluxos": fluxos(), "modelos": list(MODELOS)})
        elif self.path.startswith("/api/uso"):
            self._json(atualizar_uso() if "atualizar" in self.path else uso_plano)
        elif self.path == "/api/execucoes":
            self._json({"execucoes": [resumo(e, len(e["eventos"])) for e in
                                      sorted(_execucoes.values(), key=lambda e: -e["inicio"])]})
        elif self.path == "/api/trabalhos":
            self._json({"trabalhos": trabalhos()})
        elif self.path.startswith("/api/print/"):
            ex = _execucoes.get(self.path.split("/")[-1].split("?")[0])
            arq = os.path.join(ex["pasta"], "prints", "celular-1.png") if ex else ""
            if not arq or not os.path.exists(arq):
                return self._json({"erro": "sem print"}, 404)
            corpo = open(arq, "rb").read()
            self.send_response(200)
            self.send_header("content-type", "image/png")
            self.send_header("cache-control", "max-age=60")
            self.end_headers()
            self.wfile.write(corpo)
        elif self.path.startswith("/api/execucoes/"):
            rid, _, q = self.path.split("/")[-1].partition("?")
            ex = _execucoes.get(rid)
            desde = int(q.split("=")[1]) if q.startswith("desde=") else 0
            self._json(resumo(ex, desde) if ex else {"erro": "não encontrada"}, 200 if ex else 404)
        else:
            self._json({"erro": "não encontrado"}, 404)

    def do_POST(self):
        bruto = self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}"
        dados = json.loads(bruto.decode("utf-8", errors="replace"))
        if self.path == "/api/fluxos":
            self._json(salvar_fluxo(dados))
        elif self.path == "/api/executar":
            anterior = _execucoes.get(dados.get("continuar") or "")
            if anterior:  # ajuste num trabalho já feito: mesma pasta, só o que foi pedido
                self._json(ajustar(anterior, dados.get("prompt", ""), dados.get("anexos")))
            else:
                ex = executar(dados.get("fluxo"), dados.get("prompt", ""), "eu", anexos=dados.get("anexos"))
                self._json(ex if "erro" in ex else iniciar(ex))
        elif self.path == "/api/anexos":
            self._json(guardar_anexo(dados.get("nome", "imagem.png"), dados.get("dados", "")))
        elif self.path == "/api/parar":
            self._json(parar(dados.get("id", "")))
        elif self.path == "/api/abrir":
            ex = _execucoes.get(dados.get("id", ""))
            if ex:
                alvo = os.path.join(ex["pasta"], "index.html")
                os.startfile(alvo if dados.get("site") and os.path.exists(alvo) else ex["pasta"])
            self._json({"ok": bool(ex)})
        else:
            self._json({"erro": "não encontrado"}, 404)


_servidor = None


JUIZ = """Você é o JUIZ de qualidade, independente da equipe que fez este trabalho (está nesta pasta). Avalie como
um cliente exigente que vai pagar por ele. NÃO edite nenhum arquivo.

O QUE FOI PEDIDO:
{pedido}

CRITÉRIOS:
{criterios}

Como avaliar: leia os arquivos principais; se existir ver_site.py, rode `python ver_site.py` e abra com Read os prints
de prints/ (o celular primeiro); leia também prints/checagem.txt (testes automáticos: cada FALHA é crítica
obrigatória). Seja rigoroso e concreto: 10 é impecável, 8 é bom para entregar ao cliente.
Cada crítica deve dizer O QUE está errado, ONDE e COMO corrigir.

Responda no fim SÓ com este JSON (sem mais nada depois):
{"nota": 0-10, "criterios": {"<criterio>": nota, ...}, "criticas": ["...", "..."]}"""

CORRECAO = """O juiz de qualidade avaliou o trabalho desta pasta e deu nota {nota}/10 (a meta é {meta}). Corrija o que
ele apontou, sem refazer o que está bom:

{criticas}

Leia os arquivos atuais, delegue as correções à equipe e confira (se houver ver_site.py, rode e olhe os prints do
celular). No fim, diga em 3 a 5 linhas o que mudou."""

AJUSTE = """Este trabalho já está PRONTO nesta pasta e foi aprovado. O usuário pediu um AJUSTE:

{briefing}

Regras do ajuste: faça SOMENTE o que foi pedido; não refaça nem redesenhe o resto, não troque textos, cores ou
estrutura que ele não citou. Leia antes os arquivos atuais para entender como estão. Delegue a mudança à equipe e
confira o resultado (se houver ver_site.py, rode e olhe os prints do celular). No fim, diga em 2 a 4 linhas o que
mudou."""


GRANDE = re.compile(r"refa[zç]|redesenh|recri|nova (se[çc][ãa]o|p[áa]gina|vers[ãa]o)|mud\w* tudo|todo o site|"
                    r"layout|reestrutur|identidade|do zero", re.I)


def rota_ajuste(pedido: str, anexos: list | None = None) -> str:
    """Roteador: ajuste pequeno e pontual (texto, cor, foto, botão) vai para o Sonnet; o resto fica com o Opus.
    O diretor confere pelos prints de qualquer jeito."""
    pequeno = len(pedido) < 350 and not anexos and not GRANDE.search(pedido) and pedido.count("\n") < 6
    return "Sonnet 5.5" if pequeno else ""


def ajustar(anterior: dict, pedido: str, anexos: list | None = None) -> dict:
    """Pede uma mudança num trabalho já feito: mesmo fluxo, mesma pasta, só o que foi pedido."""
    if any(e["status"] == "rodando" and e["pasta"] == anterior["pasta"] for e in _execucoes.values()):
        return {"erro": "esse trabalho ainda está rodando; espere terminar para pedir o ajuste"}
    titulo = "Ajuste: " + anterior["titulo"].removeprefix("Ajuste: ")
    ex = executar(anterior["fluxo_id"], pedido, "eu", pasta=anterior["pasta"], titulo=titulo, anexos=anexos,
                  molde=AJUSTE, modelo_diretor=rota_ajuste(pedido, anexos))
    return ex if "erro" in ex else iniciar(ex)


def _carregar_historico():
    """Execuções antigas (orquestra_log.json de cada pasta), para ver e ajustar depois de reiniciar a Orquestra."""
    import glob
    for arq in [a for d in (os.path.join(BASE, "trabalhos"),)
                for a in glob.glob(os.path.join(d, "*", "orquestra_log.json"))
                + glob.glob(os.path.join(d, "*", ".orquestra", "*.json"))]:
        try:
            with open(arq, encoding="utf-8") as f:
                ex = json.load(f)
        except (OSError, ValueError):
            continue
        if ex.get("id") and ex["id"] not in _execucoes:
            if ex.get("status") == "rodando":
                ex["status"] = "erro"  # ficou pela metade quando a Orquestra fechou
            ex["inicio"] = float(ex.get("inicio") or 0)
            ex["fim"] = float(ex.get("fim") or ex["inicio"])
            _execucoes[ex["id"]] = ex


def servidor():
    """Liga a página da Orquestra (uma vez por processo)."""
    global _servidor
    if _servidor is None:
        _carregar_historico()
        _servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), _Handler)
        threading.Thread(target=_servidor.serve_forever, daemon=True).start()
    return f"http://localhost:{PORTA}"


def abrir_tela():
    webbrowser.open(servidor())
    return {"ok": True, "url": f"http://localhost:{PORTA}"}
