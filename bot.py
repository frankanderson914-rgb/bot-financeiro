import os
import json
import httpx
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USERS = []

DATA_FILE = "financas.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"transactions": [], "budgets": {}, "goals": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def format_brl(value):
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def current_month_transactions(data):
    now = datetime.now()
    return [t for t in data["transactions"] if t["date"].startswith(f"{now.year}-{now.month:02d}")]

CATEGORIES = {
    "alimentacao": "Alimentacao",
    "transporte": "Transporte",
    "saude": "Saude",
    "educacao": "Educacao",
    "lazer": "Lazer",
    "moradia": "Moradia",
    "vestuario": "Vestuario",
    "outros": "Outros",
}

async def ask_claude(user_message, data):
    month_txs = current_month_transactions(data)
    total = sum(t["amount"] for t in month_txs)
    by_cat = {}
    for t in month_txs:
        by_cat[t["category"]] = by_cat.get(t["category"], 0) + t["amount"]

    system = f"""Voce e um agente financeiro familiar simpatico, em portugues do Brasil.
Responda SEMPRE em portugues. Seja conciso e use emojis com moderacao.
DADOS DO MES ATUAL:
- Total gasto: {format_brl(total)}
- Por categoria: {json.dumps(by_cat, ensure_ascii=False)}
- Ultimas transacoes: {json.dumps(month_txs[-10:], ensure_ascii=False)}
- Orcamentos: {json.dumps(data.get('budgets', {}), ensure_ascii=False)}
- Categorias: {', '.join(CATEGORIES.keys())}
- Data de hoje: {datetime.now().strftime('%Y-%m-%d')}
REGRA PRINCIPAL:
Se o usuario relatar um gasto, responda APENAS com JSON:
{{"action":"add","description":"...","amount":00.00,"category":"categoria_id","date":"YYYY-MM-DD"}}
Para qualquer outra mensagem, responda em texto normal."""

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 500, "system": system, "messages": [{"role": "user", "content": user_message}]},
        )
        text = response.json()["content"][0]["text"].strip()

    try:
        if text.startswith("{"):
            parsed = json.loads(text)
            if parsed.get("action") == "add":
                return {"type": "transaction", "data": parsed}
    except:
        pass
    return {"type": "text", "content": text}

def is_allowed(user_id):
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

async def cmd_start(update, context):
    await update.message.reply_text("Ola! Sou seu Agente Financeiro Familiar.\n\nDiga gastos como:\n- Gastei R$45 no supermercado\n- Paguei 200 reais de luz\n\nComandos:\n/resumo - resumo do mes\n/ultimos - ultimos 5 gastos\n/ajuda - lista de comandos")

async def cmd_resumo(update, context):
    if not is_allowed(update.effective_user.id):
        return
    data = load_data()
    month_txs = current_month_transactions(data)
    total = sum(t["amount"] for t in month_txs)
    now = datetime.now()
    by_cat = {}
    for t in month_txs:
        by_cat[t["category"]] = by_cat.get(t["category"], 0) + t["amount"]
    linhas = [f"Resumo do mes {now.month}/{now.year}\n", f"Total: {format_brl(total)}", f"Transacoes: {len(month_txs)}\n", "Por categoria:"]
    for cat, val in sorted(by_cat.items(), key=lambda x: -x[1]):
        linhas.append(f"  {cat}: {format_brl(val)}")
    await update.message.reply_text("\n".join(linhas))

async def cmd_ultimos(update, context):
    if not is_allowed(update.effective_user.id):
        return
    data = load_data()
    recentes = data["transactions"][-5:][::-1]
    if not recentes:
        await update.message.reply_text("Nenhum gasto registrado ainda.")
        return
    linhas = ["Ultimos 5 gastos:\n"]
    for t in recentes:
        linhas.append(f"{t['category']}: {t['description']} - {format_brl(t['amount'])} ({t['date']})")
    await update.message.reply_text("\n".join(linhas))

async def cmd_ajuda(update, context):
    await update.message.reply_text("/start - boas-vindas\n/resumo - resumo do mes\n/ultimos - ultimos 5 gastos\n/ajuda - esta mensagem")

async def handle_message(update, context):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("Acesso nao autorizado.")
        return
    user_text = update.message.text
    user_name = update.effective_user.first_name or "voce"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    data = load_data()
    try:
        result = await ask_claude(user_text, data)
    except Exception as e:
        await update.message.reply_text("Erro ao conectar com a IA. Tente novamente.")
        return
    if result["type"] == "transaction":
        tx = result["data"]
        tx["id"] = int(datetime.now().timestamp() * 1000)
        tx["registered_by"] = user_name
        tx["date"] = tx.get("date") or datetime.now().strftime("%Y-%m-%d")
        data["transactions"].append(tx)
        save_data(data)
        await update.message.reply_text(f"Gasto registrado por {user_name}!\n{tx['category']}: {tx['description']}\nValor: {format_brl(tx['amount'])}\nData: {tx['date']}")
    else:
        await update.message.reply_text(result["content"])

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("resumo", cmd_resumo))
    app.add_handler(CommandHandler("ultimos", cmd_ultimos))
    app.add_handler(CommandHandler("ajuda", cmd_ajuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot rodando...")
    app.run_polling()
