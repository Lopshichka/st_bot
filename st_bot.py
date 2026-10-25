import asyncio
import logging
import os
import threading
import json
import base64
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, BotCommand, LabeledPrice, PreCheckoutQuery,
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from flask import Flask
from cryptography.fernet import Fernet

# ============================================================
# КОНФИГ
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN")

ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("Не найден ENCRYPTION_KEY")

cipher = Fernet(ENCRYPTION_KEY.encode())

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# Настройки AI (OpenRouter)
AI_API_URL = os.environ.get("AI_API_URL", "https://openrouter.ai/api/v1/chat/completions")
AI_MODEL = os.environ.get("AI_MODEL", "openrouter/free")
AI_API_KEY = os.environ.get("AI_API_KEY")

# Настройки GitHub (для сохранения истории)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")  # например, "Lopshichka/st_bot"

SYSTEM_PROMPT = "Ты — эмпатичный психолог. Слушай пользователя, задавай уточняющие вопросы, помогай разобраться в проблеме. Отвечай тепло и по делу."

PRICES = [
    (10, 5),
    (30, 13),
    (50, 20),
    (100, 45),
    (200, 85),
    (500, 230),
]

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("tokens"), F.from_user.id == ADMIN_ID)
async def cmd_tokens(message: Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /tokens [id] [количество]")
        return

    try:
        target_id = int(parts[1])
        amount = int(parts[2])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("ID и количество должны быть положительными числами.")
        return

    data = load_user(target_id)
    data["tokens"] += amount
    save_user(target_id, data)

    await message.answer(
        f"пользователю {target_id} начислено {amount} токенов.\n"
        f"Текущий баланс: {data['tokens']}"
    )

# ============================================================
# ХРАНИЛИЩЕ (JSON-файлы + GitHub)
# ============================================================

def user_file(user_id: int) -> str:
    return os.path.join(DATA_DIR, f"user_{user_id}.json")

def load_user(user_id: int) -> dict:
    path = user_file(user_id)
    if not os.path.exists(path):
        return {"tokens": 0, "history": []}
    try:
        with open(path, "rb") as f:
            encrypted = f.read()
        decrypted = cipher.decrypt(encrypted)
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        return {"tokens": 0, "history": []}

async def save_user_to_github(user_id: int, encrypted_data: bytes):
    """Сохраняет зашифрованный файл в GitHub через Contents API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return  # GitHub не настроен, просто пропускаем

    file_path = f"data/user_{user_id}.json"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with aiohttp.ClientSession() as session:
        # 1. Проверяем, существует ли файл (нужно для получения sha при обновлении)
        async with session.get(url, headers=headers) as resp:
            sha = None
            if resp.status == 200:
                data = await resp.json()
                sha = data.get("sha")

        # 2. Создаём или обновляем файл
        payload = {
            "message": f"update history for user {user_id}",
            "content": base64.b64encode(encrypted_data).decode("utf-8"),
        }
        if sha:
            payload["sha"] = sha

        async with session.put(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                logging.error(f"GitHub save failed: {resp.status} {await resp.text()}")

def save_user(user_id: int, data: dict):
    """Сохраняет локально и запускает фоновое сохранение в GitHub."""
    path = user_file(user_id)
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    encrypted = cipher.encrypt(raw)
    with open(path, "wb") as f:
        f.write(encrypted)

    # Запускаем сохранение в GitHub асинхронно (не блокируем ответ)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(save_user_to_github(user_id, encrypted))
    except RuntimeError:
        # Если event loop не запущен (например, при старте), сохраняем синхронно позже
        pass

# ============================================================
# FLASK (для Render)
# ============================================================

app = Flask(__name__)

@app.route("/")
def health():
    return "Bot is running", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ============================================================
# ВЫЗОВ AI
# ============================================================

async def ask_ai(history: list) -> str:
    """Отправляет историю диалога в OpenRouter и возвращает ответ."""
    if not AI_API_KEY:
        return "[Ошибка] AI_API_KEY не настроен. Добавь его в Render Environment."

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(AI_API_URL, headers=headers, json=payload, timeout=60) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logging.error(f"AI API error: {resp.status} {error_text}")
                    return f"[Ошибка AI] Сервис вернул код {resp.status}. Попробуй позже."
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        return "[Ошибка AI] Превышено время ожидания. Попробуй ещё раз."
    except Exception as e:
        logging.error(f"AI request failed: {e}")
        return "[Ошибка AI] Не удалось получить ответ. Попробуй позже."

# ============================================================
# FSM
# ============================================================

class ChatStates(StatesGroup):
    chatting = State()

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def profile_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 купить токены", callback_data="buy_tokens")]
    ])

def prices_keyboard():
    buttons = []
    for tokens, stars in PRICES:
        buttons.append([InlineKeyboardButton(
            text=f"{tokens} токенов — {stars}⭐",
            callback_data=f"buy_{tokens}_{stars}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ назад", callback_data="back_to_profile")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ============================================================
# /start, /information, /account
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот-психолог.\n\n"
        "Команды:\n"
        "/account — твой профиль и баланс токенов\n"
        "/information — информация о боте\n\n"
        "Просто напиши мне что-нибудь, и я отвечу. 1 сообщение - один токен."
    )

@dp.message(Command("information"))
async def cmd_information(message: Message):
    await message.answer(
        "Ваш личный психолог. Поговорим обо всём!\n\n"
        "1 сообщение - 1 токен.\n"
        "Купить токены можно в /account.\n\n"
        "⚠️ Бот не заменяет живого специалиста. В кризисной ситуации обратись на горячую линию психологической помощи."
    )

@dp.message(Command("account"))
async def cmd_profile(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data = load_user(user_id)
    tokens = data["tokens"]

    text = (
        f"👤 Имя: {message.from_user.full_name}\n"
        f"🆔 ID: {user_id}\n"
        f"🪙 Токенов: {tokens}\n\n"
        f"1 сообщение - 1 токен."
    )
    await message.answer(text, reply_markup=profile_keyboard())
    await state.clear()

# ============================================================
# CALLBACK: ПОКУПКА ТОКЕНОВ
# ============================================================

@dp.callback_query(F.data == "buy_tokens")
async def cb_buy_tokens(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "Выберите пакет токенов:",
        reply_markup=prices_keyboard()
    )

@dp.callback_query(F.data.startswith("buy_") & ~F.data.startswith("buy_tokens"))
async def cb_select_package(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    tokens = int(parts[1])
    stars = int(parts[2])

    await callback.message.delete()
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{tokens} токенов",
        description=f"Пакет токенов для общения",
        payload=f"tokens_{tokens}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{tokens} токенов", amount=stars)],
        provider_token="",
    )

@dp.callback_query(F.data == "back_to_profile")
async def cb_back_to_profile(callback: CallbackQuery):
    await callback.answer()
    await callback.message.delete()

    user_id = callback.from_user.id
    data = load_user(user_id)
    tokens = data["tokens"]

    text = (
        f"👤 Имя: {callback.from_user.full_name}\n"
        f"🆔 ID: {user_id}\n"
        f"🪙 Токенов: {tokens}\n\n"
        f"1 сообщение - 1 токен."
    )
    await callback.bot.send_message(user_id, text, reply_markup=profile_keyboard())

# ============================================================
# ОПЛАТА
# ============================================================

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_received(message: Message):
    payment = message.successful_payment
    payload = payment.invoice_payload

    if payload.startswith("tokens_"):
        tokens_to_add = int(payload.split("_")[1])
        user_id = message.from_user.id
        data = load_user(user_id)
        data["tokens"] += tokens_to_add
        save_user(user_id, data)

        text = (
            f"✅ Оплата успешна!\n"
            f"Начислено: {tokens_to_add} токенов\n"
            f"Текущий баланс: {data['tokens']}\n\n"
            f"👤 {message.from_user.full_name}\n"
            f"🆔 {user_id}"
        )
        await message.answer(text, reply_markup=profile_keyboard())

# ============================================================
# ОБРАБОТКА СООБЩЕНИЙ (ПСИХОЛОГ)
# ============================================================

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text

    # Проверка на кризисные сообщения
    crisis_words = ["суицид", "убить себя", "не хочу жить", "покончить", "самоубий"]
    if any(w in text.lower() for w in crisis_words):
        await message.answer(
            "Мне очень жаль, что тебе сейчас так тяжело.\n\n"
            "Пожалуйста, обратись к живому специалисту. Это важно.\n\n"
            "📞 Горячая линия психологической помощи (РФ): 8-800-2000-122\n"
            "📞 Экстренная помощь: 112"
        )
        return

    data = load_user(user_id)

    if data["tokens"] <= 0:
        await message.answer(
            "❌ У тебя закончились токены.\n\n"
            "Пополни баланс в /account, чтобы продолжить общение."
        )
        return

    data["tokens"] -= 1
    data["history"].append({"role": "user", "content": text})

    if len(data["history"]) > 100:
        data["history"] = data["history"][-100:]

    # Отправляем запрос к AI
    await message.answer("⏳ Думаю над ответом...")
    ai_response = await ask_ai(data["history"])

    data["history"].append({"role": "assistant", "content": ai_response})
    save_user(user_id, data)

    await message.answer(ai_response)

# ============================================================
# МЕНЮ КОМАНД
# ============================================================

async def set_main_menu(bot: Bot):
    main_menu_commands = [
        BotCommand(command="start", description="начать"),
        BotCommand(command="account", description="аккаунт и токены"),
        BotCommand(command="information", description="информация"),
    ]
    await bot.set_my_commands(main_menu_commands)

# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=run_flask, daemon=True).start()
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
