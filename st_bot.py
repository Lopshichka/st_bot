import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN. Добавьте его в переменные окружения.")

api = TelegramAPIServer.from_base("https://k11wee-xc.honor505.workers.dev")
session = AiohttpSession(api=api)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

@dp.message(Command("/pay"))
async def cmd_buy(message: Message):
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="payment",
        description="",
        payload="tg_confirm",
        currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=1)],
        provider_token="",
    )

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_received(message: Message):
    payment = message.successful_payment
    print("charge_id:", payment.telegram_payment_charge_id)
    await message.answer("🎆successful payment🎆")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot, polling_timeout=15)

if __name__ == "__main__":
    asyncio.run(main())
