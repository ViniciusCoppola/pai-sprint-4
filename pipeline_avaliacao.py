import os
import time
import requests
import warnings
from pydantic import BaseModel, Field

# 1. Silenciar alertas do LangChain
warnings.filterwarnings("ignore")

# 2. Instalar dependências
!pip install -q langchain>=0.3.0 langchain-core>=0.3.0 langchain-groq>=0.3.0 pydantic requests

# 3. Puxar a chave de API
try:
    from kaggle_secrets import UserSecretsClient
    os.environ["GROQ_API_KEY"] = UserSecretsClient().get_secret("GROQ_API_KEY")
except Exception:
    pass

api_key = os.environ.get("GROQ_API_KEY", "")

# 4. Busca Dinâmica de Modelos
print("🔍 Buscando modelos na Groq...")
resp = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {api_key}"})

if resp.status_code != 200:
    print(f"❌ Erro na chave: {resp.text}")
else:
    modelos_dados = resp.json().get("data", [])
    
    # Filtra modelos de texto e modelos de segurança (guard)
    modelos_texto = [m["id"] for m in modelos_dados if "whisper" not in m["id"] and "vision" not in m["id"] and "guard" not in m["id"]]
    modelos_guard = [m["id"] for m in modelos_dados if "guard" in m["id"]]
    
    # Escolhe o melhor Juiz possível (prioriza Llama ou modelos de 70b)
    juiz_id = modelos_texto[0]
    for m in modelos_texto:
        if "70b" in m.lower() or "llama" in m.lower():
            juiz_id = m
            break
            
    # Escolhe 2 modelos textuais diferentes do juiz, e 1 modelo guard
    modelos_para_testar = [m for m in modelos_texto if m != juiz_id][:2]
    if modelos_guard:
        modelos_para_testar.append(modelos_guard[0])
    
    print(f"✅ Configuração Ideal Alcançada!\n- Juiz: {juiz_id}\n- Modelos Testados: {modelos_para_testar}\n")

    from langchain_core.chat_history import InMemoryChatMessageHistory
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_core.runnables.history import RunnableWithMessageHistory
    from langchain_groq import ChatGroq

    def gerar_dados():
        return "=== DADOS ===\nTarifa: R$ 1,30/kWh\nOntem: 12 sessoes | 180 kWh | R$ 234,00\nPonto 3: Offline (Erro Modbus)"

    _historicos = {}
    def _obter_historico(session_id: str):
        if session_id not in _historicos:
            _historicos[session_id] = InMemoryChatMessageHistory()
        return _historicos[session_id]

    def construir_agente(model_id: str):
        llm = ChatGroq(model=model_id, temperature=0.3, max_tokens=512)
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"Você é o ChargeGrid Assistant.\nDADOS:\n{gerar_dados()}"),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}")
        ])
        return RunnableWithMessageHistory(prompt | llm, _obter_historico, input_messages_key="input", history_messages_key="history")

    dataset = [
        {"id": "F01", "pergunta": "Qual foi o faturamento de ontem?", "criterio": "Dizer R$ 234,00."},
        {"id": "F02", "pergunta": "Tem algum carregador com problema?", "criterio": "Dizer Ponto 3 offline."}
    ]

    class AvaliacaoJuiz(BaseModel):
        nota: int = Field(description="Nota de 0 a 10.")
        justificativa: str = Field(description="Justificativa da nota.")

    juiz = ChatGroq(model=juiz_id, temperature=0.0, max_retries=3).with_structured_output(AvaliacaoJuiz)
    chain_juiz = ChatPromptTemplate.from_template("Avalie a resposta.\nPERGUNTA: {p}\nRESPOSTA DO BOT: {r}\nGABARITO: {c}\nDê nota de 0 a 10.") | juiz

    resultados_md = "| Modelo | ID | Nota | Justificativa | Latência (s) |\n|---|---|---|---|---|\n"

    print("🚀 Iniciando testes. Aguarde as pausas anti-bloqueio (12s entre testes)...\n")
    for mod_id in modelos_para_testar:
        print(f"⏳ Avaliando {mod_id}...")
        try:
            agente = construir_agente(mod_id)
            for item in dataset:
                print(f"   -> Avaliando pergunta {item['id']}...")
                try:
                    inicio = time.time()
                    res_bot = agente.invoke({"input": item["pergunta"]}, config={"configurable": {"session_id": f"sess-{mod_id}"}})
                    latencia = round(time.time() - inicio, 2)
                    
                    aval = chain_juiz.invoke({"p": item["pergunta"], "r": res_bot.content, "c": item["criterio"]})
                    resultados_md += f"| {mod_id} | {item['id']} | {aval.nota}/10 | {aval.justificativa} | {latencia} |\n"
                except Exception as e:
                    if "guard" in mod_id.lower():
                        resultados_md += f"| {mod_id} | {item['id']} | ERRO | Modelo de classificacao de seguranca incompativel com historico | 0.0 |\n"
                    else:
                        resultados_md += f"| {mod_id} | {item['id']} | ERRO | Limite de API ou erro de resposta: {str(e)[:50]} | 0.0 |\n"
                
                # PAUSA LONGA PARA BURLAR O RATE LIMIT
                time.sleep(12) 
        except Exception as e:
            resultados_md += f"| {mod_id} | GERAL | ERRO | Falha ao inicializar. | 0.0 |\n"
            
    print("\n Sucesso! Aqui está a tabela perfeita para o seu PDF:\n")
    print(resultados_md)
