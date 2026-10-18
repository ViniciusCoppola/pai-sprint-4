"""
guardrails.py — Camada de segurança do ChargeGrid Assistant (Sprint 3)
EV Challenge 2026 — FIAP + GoodWe

Responsabilidade:
    Interceptar a pergunta do operador ANTES de enviá-la ao LLM, para:
    1) Detectar tentativas de prompt injection (ex.: "ignore as instruções
       anteriores", "revele seu system prompt", jailbreaks tipo DAN).
    2) Validar se a pergunta está dentro do escopo GoodWe/eletroposto
       (recusando pedidos de aconselhamento jurídico, financeiro ou de
       segurança elétrica formal, com orientação para procurar um
       profissional habilitado).

Este módulo é determinístico (não depende de chamada ao LLM), o que permite
testá-lo de forma automatizada e reprodutível — ver testes_seguranca.py.

Em produção, esta camada roda ANTES da chain do LangChain (ver
chatbot_agent.py). Se `permitido=False`, a pergunta nunca chega ao LLM.
"""

from dataclasses import dataclass
import re
import unicodedata


def _normalizar(texto: str) -> str:
    """Remove acentos e caixa alta para tornar as regras mais robustas."""
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto


@dataclass
class ResultadoSeguranca:
    permitido: bool
    categoria: str          # "ok" | "prompt_injection" | "fora_de_escopo"
    subcategoria: str       # ex.: "juridico", "medico", "financeiro", "seguranca_eletrica", "jailbreak", "exfiltracao_prompt"
    motivo: str
    resposta_padrao: str = ""


# ──────────────────────────────────────────────
# 1. REGRAS — PROMPT INJECTION / JAILBREAK
# ──────────────────────────────────────────────

PADROES_INJECAO = [
    (r"ignor[ea].{0,20}(instru|regra|prompt|comando)", "jailbreak"),
    (r"esque[çc]a.{0,25}(instru|regra|system prompt|quem voce e|que voce e)", "jailbreak"),
    (r"desconsidere.{0,20}(instru|regra|prompt)", "jailbreak"),
    (r"a partir de agora voce (e|eh|sera)", "jailbreak"),
    (r"\bdan\b|\bdo anything now\b|modo (sem|desenvolvedor|dev)\b", "jailbreak"),
    (r"finja que (voce )?(nao tem|nao possui) (restri|regra|limita)", "jailbreak"),
    (r"finja que (voce )?(e|eh)\b", "jailbreak"),
    (r"aja como (se voce|um)", "jailbreak"),
    (r"\[system\]|nova diretiva|diretiva priorit[aá]ria|instru[çc][aã]o priorit[aá]ria", "jailbreak"),
    (r"(revele|mostre|repita|imprima|exiba).{0,20}(system prompt|prompt do sistema|instrucoes do sistema|suas instrucoes)", "exfiltracao_prompt"),
    (r"qual (e|eh) (o seu|seu) (system prompt|prompt inicial)", "exfiltracao_prompt"),
    (r"copie e cole (o|seu) (prompt|texto acima)", "exfiltracao_prompt"),
    (r"traduza (o|seu) prompt", "exfiltracao_prompt"),
    (r"ignore.{0,10}tudo (acima|anterior)", "jailbreak"),
]

# ──────────────────────────────────────────────
# 2. REGRAS — FORA DE ESCOPO (domínios sensíveis)
# ──────────────────────────────────────────────

PADROES_ESCOPO = [
    (r"processo? judicial|processar|advogad|indeniza[çc][aã]o|acionar.{0,10}justi[çc]a|direito(s)? do consumidor|entrar com (uma )?a[çc][aã]o",
     "juridico",
     "Não posso fornecer aconselhamento jurídico. Para questões sobre processos, indenizações ou direitos do consumidor, recomendo consultar um advogado ou o setor jurídico da sua empresa."),
    (r"reme[dt]io|sintoma|diagn[oó]stico m[eé]dico|dor de cabe[çc]a|consulta m[eé]dica|doen[çc]a",
     "medico",
     "Não posso fornecer aconselhamento médico. Esse tema está fora do escopo do ChargeGrid Assistant, que atua exclusivamente na gestão do eletroposto."),
    (r"a[çc][oõ]es da bolsa|investir em (a[çc][oõ]es|criptomoeda|bitcoin)|onde investir|melhor investimento|comprar (a[çc][oõ]es|d[oó]lar)",
     "financeiro",
     "Não posso fornecer recomendação de investimentos ou aconselhamento financeiro pessoal. Posso, no entanto, analisar receita, tarifas e ocupação do seu eletroposto — deseja isso?"),
    (r"trocar (a|minha|a minha) fia[çc][aã]o|mexer no (quadro|disjuntor)|desmontar o carregador|abrir o carregador|risco de choque|fazer eu mesmo a instala[çc][aã]o el[eé]trica",
     "seguranca_eletrica",
     "Não posso orientar sobre intervenções físicas ou elétricas no equipamento — isso exige um eletricista ou técnico GoodWe certificado, por segurança. Recomendo abrir um chamado técnico."),
]


def checar_seguranca(pergunta: str) -> ResultadoSeguranca:
    """
    Analisa a pergunta do operador e decide se ela pode seguir para o LLM.
    Retorna um ResultadoSeguranca; se permitido=False, o chamador deve usar
    `resposta_padrao` diretamente, SEM chamar o modelo.
    """
    texto = _normalizar(pergunta)

    for padrao, subcategoria in PADROES_INJECAO:
        if re.search(padrao, texto):
            return ResultadoSeguranca(
                permitido=False,
                categoria="prompt_injection",
                subcategoria=subcategoria,
                motivo=f"Padrão de injection/jailbreak detectado: /{padrao}/",
                resposta_padrao=(
                    "Não posso seguir instruções que tentem alterar meu comportamento, "
                    "revelar minhas instruções internas ou ignorar minhas regras de operação. "
                    "Posso ajudar com faturamento, status dos carregadores, demanda e energia solar do seu eletroposto."
                ),
            )

    for padrao, subcategoria, resposta in PADROES_ESCOPO:
        if re.search(padrao, texto):
            return ResultadoSeguranca(
                permitido=False,
                categoria="fora_de_escopo",
                subcategoria=subcategoria,
                motivo=f"Pergunta fora do escopo GoodWe/eletroposto ({subcategoria})",
                resposta_padrao=resposta,
            )

    return ResultadoSeguranca(
        permitido=True,
        categoria="ok",
        subcategoria="",
        motivo="Nenhum padrão de risco detectado; pergunta segue para o LLM.",
    )


# ──────────────────────────────────────────────
# 3. GUARDRAIL DE SAÍDA (defesa em profundidade)
# ──────────────────────────────────────────────

FRASES_PROIBIDAS_NA_SAIDA = [
    "system prompt",
    "prompt do sistema",
    "minhas instrucoes internas",
]


def checar_saida(resposta_llm: str) -> ResultadoSeguranca:
    """
    Segunda camada: verifica se o próprio LLM, apesar do guardrail de entrada,
    vazou instruções internas na resposta (defesa em profundidade).
    """
    texto = _normalizar(resposta_llm)
    for frase in FRASES_PROIBIDAS_NA_SAIDA:
        if frase in texto:
            return ResultadoSeguranca(
                permitido=False,
                categoria="vazamento_saida",
                subcategoria="exfiltracao_prompt",
                motivo=f"Resposta do modelo menciona '{frase}'",
                resposta_padrao=(
                    "Não posso compartilhar detalhes da minha configuração interna. "
                    "Posso ajudar com dados do seu eletroposto — faturamento, status, demanda ou energia solar."
                ),
            )
    return ResultadoSeguranca(permitido=True, categoria="ok", subcategoria="", motivo="Saída sem vazamento detectado.")
