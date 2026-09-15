import os
import telebot
from google import genai

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

user_states = {}

def get_main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_korean = telebot.types.KeyboardButton("🇰🇷 Корейский")
    btn_python = telebot.types.KeyboardButton("🐍 Python")
    btn_ai = telebot.types.KeyboardButton("🧁 Задать вопрос ИИ")
    markup.add(btn_korean, btn_python)
    markup.add(btn_ai)
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_states[message.chat.id] = None
    bot.send_message(
        message.chat.id, 
        "Привет! Выбери урок или задай вопрос ИИ:", 
        reply_markup=get_main_keyboard()
    )

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text

    if text == "🐍 Python":
        user_states[chat_id] = None
        bot.send_message(
            chat_id, 
            "🐍 *Python с нуля*\n\nКоманда для вывода текста в консоль:\n`print('Hello World')`", 
            parse_mode="Markdown"
        )
    elif text == "🇰🇷 Корейский":
        user_states[chat_id] = None
        bot.send_message(
            chat_id, 
            "🇰🇷 *Корейский язык*\n\nПриветствие: 안녕하세요 (Аннёнхасеё)", 
            parse_mode="Markdown"
        )
    elif text == "🧁 Задать вопрос ИИ":
        user_states[chat_id] = "AI_MODE"
        bot.send_message(
            chat_id, 
            "Режим ИИ включен! Напиши любой вопрос:"
        )
    else:
        if user_states.get(chat_id) == "AI_MODE":
            try:
                response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=text,
                )
                bot.send_message(chat_id, response.text)
            except Exception as e:
                print(f"Ошибка Gemini API: {e}")
                bot.send_message(chat_id, "Произошла ошибка при обращении к ИИ.")
        else:
            bot.send_message(
                chat_id, 
                "Используй кнопки внизу для выбора темы 👇", 
                reply_markup=get_main_keyboard()
            )

if __name__ == "__main__":
    bot.infinity_polling()
    
