import asyncio
import logging
import os
import threading

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN. Добавьте его в переменные окружения.")

invoice_messages = {}
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app = Flask(__name__)


@app.route("/")
def health():
    return "Bot is running", 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


@dp.message(F.dice)
async def handle_user_dice(message: Message):
    value = message.dice.value
    emoji = message.dice.emoji
    await message.answer(f"Вы выкинули {emoji} со значением {value}")


@dp.message(Command("pay"))
async def cmd_buy(message: Message):
    sent = await bot.send_invoice(
        chat_id=message.chat.id,
        title="payment",
        description="TELEGRAM_CONFIRM",
        payload="tg_confirm",
        currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=1)],
        provider_token="",
    )
    invoice_messages[message.from_user.id] = sent.message_id


@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def payment_received(message: Message):
    payment = message.successful_payment
    print("charge_id:", payment.telegram_payment_charge_id)

    msg_id = invoice_messages.pop(message.from_user.id, None)
    if msg_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except Exception as e:
            print(f"Не удалось удалить сообщение со счётом: {e}")

    await message.answer("successful payment")


async def main():
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=run_flask, daemon=True).start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
