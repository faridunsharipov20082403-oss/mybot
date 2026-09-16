import os, sqlite3, threading, random
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import date, timedelta
from google import genai
from google.genai import types
import telebot

BOT_TOKEN=os.environ.get('BOT_TOKEN')
GEMINI_API_KEY=os.environ.get('GEMINI_API_KEY')
GEMINI_MODEL=os.environ.get('GEMINI_MODEL','gemini-3.8-flash')
PORT=int(os.environ.get('PORT',8080))
DB_PATH=os.environ.get('DB_PATH','language_teacher.db')
if not BOT_TOKEN: raise RuntimeError('BOT_TOKEN is not set.')
if not GEMINI_API_KEY: raise RuntimeError('GEMINI_API_KEY is not set.')

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','text/plain; charset=utf-8'); self.end_headers(); self.wfile.write(b'Language Teacher bot is running!')
    def log_message(self,*args): pass

def run_http(): HTTPServer(('0.0.0.0',PORT),Health).serve_forever()
threading.Thread(target=run_http,daemon=True).start()

bot=telebot.TeleBot(BOT_TOKEN,parse_mode='HTML')
ai=genai.Client(api_key=GEMINI_API_KEY)
lock=threading.Lock(); states={}; histories={}; quizzes={}

def conn():
    c=sqlite3.connect(DB_PATH,check_same_thread=False); c.row_factory=sqlite3.Row; return c

def init_db():
    with lock:
        c=conn()
        c.execute('''CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,first_name TEXT DEFAULT '',language TEXT DEFAULT 'zh',level TEXT DEFAULT 'beginner',xp INTEGER DEFAULT 0,streak INTEGER DEFAULT 0,last_study TEXT DEFAULT '',total_lessons INTEGER DEFAULT 0,total_correct INTEGER DEFAULT 0,total_answers INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS vocabulary(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,language TEXT,word TEXT,meaning TEXT DEFAULT '',pinyin TEXT DEFAULT '',example TEXT DEFAULT '',mastery INTEGER DEFAULT 0,next_review TEXT DEFAULT '',UNIQUE(user_id,language,word))''')
        c.execute('''CREATE TABLE IF NOT EXISTS mistakes(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,language TEXT,question TEXT,user_answer TEXT,correct_answer TEXT,explanation TEXT,created_at TEXT)''')
        c.commit(); c.close()

def ensure(uid,first='Ученик'):
    with lock:
        c=conn(); c.execute('INSERT OR IGNORE INTO users(user_id,first_name) VALUES(?,?)',(uid,first)); c.execute('UPDATE users SET first_name=? WHERE user_id=?',(first,uid)); c.commit(); c.close()

def user(uid):
    with lock:
        c=conn(); r=c.execute('SELECT * FROM users WHERE user_id=?',(uid,)).fetchone(); c.close()
    return dict(r)

def update(uid,**kw):
    allowed={'first_name','language','level','xp','streak','last_study','total_lessons','total_correct','total_answers'}; kw={k:v for k,v in kw.items() if k in allowed}
    if not kw:return
    with lock:
        c=conn(); c.execute('UPDATE users SET '+','.join(f'{k}=?' for k in kw)+' WHERE user_id=?',list(kw.values())+[uid]); c.commit(); c.close()

LEVELS={'beginner':'A1 / Начальный','elementary':'A2 / Элементарный','intermediate':'B1 / Средний','upper':'B2 / Выше среднего','advanced':'C1 / Продвинутый'}
def level(xp): return 'beginner' if xp<300 else 'elementary' if xp<900 else 'intermediate' if xp<1800 else 'upper' if xp<3200 else 'advanced'
def add_xp(uid,n):
    u=user(uid); old=u['level']; newxp=u['xp']+n; new=level(newxp); update(uid,xp=newxp,level=new)
    if new!=old: bot.send_message(uid,f'🎉 <b>Новый уровень!</b>\n{LEVELS[old]} → <b>{LEVELS[new]}</b>')
def study(uid):
    u=user(uid); today=date.today().isoformat()
    if u['last_study']==today:return u['streak']
    streak=u['streak']
    try: streak=streak+1 if date.fromisoformat(u['last_study'])==date.today()-timedelta(days=1) else 1
    except: streak=1
    update(uid,last_study=today,streak=streak,total_lessons=u['total_lessons']+1); return streak

def main_kb():
    m=telebot.types.ReplyKeyboardMarkup(resize_keyboard=True,row_width=2)
    m.row('🇨🇳 Китайский','🇰🇷 Корейский'); m.row('📚 Урок','🧠 Тренировка'); m.row('📝 Словарь','📊 Прогресс'); m.row('🤖 Учитель ИИ','🎯 Тест'); m.row('⚙️ Настройки','ℹ️ Помощь'); return m

def lang_kb():
    m=telebot.types.InlineKeyboardMarkup(); m.add(telebot.types.InlineKeyboardButton('🇨🇳 Китайский',callback_data='lang:zh'),telebot.types.InlineKeyboardButton('🇰🇷 Корейский',callback_data='lang:ko')); return m

def lesson_kb(lang):
    m=telebot.types.InlineKeyboardMarkup(row_width=2)
    m.add(telebot.types.InlineKeyboardButton('📖 Урок',callback_data=f'lesson:{lang}'),telebot.types.InlineKeyboardButton('🗣 Практика',callback_data=f'practice:{lang}'))
    m.add(telebot.types.InlineKeyboardButton('✍️ Грамматика',callback_data=f'grammar:{lang}'),telebot.types.InlineKeyboardButton('🧠 Слова',callback_data=f'words:{lang}'))
    m.add(telebot.types.InlineKeyboardButton('🎯 Мини-тест',callback_data=f'quiz:{lang}')); return m

def lname(lang): return 'китайского (中文 / Mandarin)' if lang=='zh' else 'корейского (한국어 / Korean)'
def teacher_prompt(lang,lvl):
    return f'''Ты — персональный AI-преподаватель {lname(lang)}. Ученик говорит по-русски. Уровень: {LEVELS[lvl]}.
Веди ученика как настоящий терпеливый преподаватель: объясняй простыми словами, исправляй ошибки конкретно, задавай вопросы и упражнения, не раскрывай ответ до попытки ученика. Для китайского показывай иероглифы + pinyin + перевод и обращай внимание на тоны. Для корейского показывай корейское написание + при необходимости romanization + перевод и объясняй частицы, окончания и уровни вежливости. Используй русский для объяснений. Обычно одна небольшая тема за раз. Если ученик просит перевод — дай перевод и кратко объясни ключевые слова. Если он пишет фразу на изучаемом языке — оцени естественность и исправь её. Формат при необходимости: 📌 Тема / 📚 Объяснение / 🧩 Пример / ✍️ Твоё задание.'''

def ask(uid,text,lang=None):
    u=user(uid); lang=lang or u['language']; key=(uid,lang); h=histories.setdefault(key,[]); h.append(('Ученик',text)); h[:]=h[-10:]
    context='\n'.join(f'{a}: {b}' for a,b in h)
    r = ai.models.generate_content(
    model=GEMINI_MODEL,
    contents=teacher_prompt(lang, u['level']) +
             '\nУчебная сессия:\n' +
             context +
             '\nОтветь на последнее сообщение.',
    config=types.GenerateContentConfig(
        max_output_tokens=900
    )
    )
    ans=r.text or 'Не удалось получить ответ.'; h.append(('Учитель',ans)); return ans

TOPICS={'zh':[('Приветствие','你好 / nǐ hǎo','привет'),('Спасибо','谢谢 / xièxie','спасибо'),('Меня зовут','我叫… / wǒ jiào…','меня зовут…'),('Я изучаю','我学习… / wǒ xuéxí…','я изучаю…'),('Где?','哪里 / nǎlǐ','где?')],'ko':[('Приветствие','안녕하세요 / annyeonghaseyo','здравствуйте'),('Спасибо','감사합니다 / gamsahamnida','спасибо'),('Меня зовут','제 이름은 …예요','меня зовут…'),('Я учусь','저는 공부해요','я учусь'),('Где?','어디예요?','где?')]}

def lesson(uid,lang):
    topic,phrase,meaning=random.choice(TOPICS[lang]); streak=study(uid); add_xp(uid,20)
    bot.send_message(uid,f'📚 <b>Мини-урок: {topic}</b>\n\n<b>{phrase}</b>\n🇷🇺 {meaning}\n\n💡 Попробуй написать фразу самостоятельно.\n🔥 Серия: {streak} дн.\n⭐ +20 XP',reply_markup=lesson_kb(lang))

def words(uid,lang):
    with lock:
        c=conn(); rows=c.execute('SELECT word,meaning,pinyin,mastery FROM vocabulary WHERE user_id=? AND language=? ORDER BY id DESC LIMIT 15',(uid,lang)).fetchall(); c.close()
    if not rows: bot.send_message(uid,'📚 Словарь пока пуст. В «Учитель ИИ» попроси: «дай мне 5 новых слов и добавь их в мой словарь».'); return
    s=['📚 <b>Мой словарь</b>']+[f"{i}. <b>{r['word']}</b>{' · '+r['pinyin'] if r['pinyin'] else ''} — {r['meaning']} ({r['mastery']}/5)" for i,r in enumerate(rows,1)]; bot.send_message(uid,'\n'.join(s))

def progress(uid):
    u=user(uid)
    with lock:
        c=conn(); vc=c.execute('SELECT COUNT(*) FROM vocabulary WHERE user_id=?',(uid,)).fetchone()[0]; mc=c.execute('SELECT COUNT(*) FROM mistakes WHERE user_id=?',(uid,)).fetchone()[0]; c.close()
    acc=round(u['total_correct']/u['total_answers']*100) if u['total_answers'] else 0
    bot.send_message(uid,f"📊 <b>Прогресс</b>\n\n🌐 {lname(u['language'])}\n🎓 {LEVELS[u['level']]}\n⭐ XP: <b>{u['xp']}</b>\n🔥 Серия: <b>{u['streak']} дн.</b>\n📚 Уроков: <b>{u['total_lessons']}</b>\n📝 Слов: <b>{vc}</b>\n❌ Ошибок: <b>{mc}</b>\n🎯 Точность тестов: <b>{acc}%</b>")

def practice(uid,lang):
    u=user(uid); p=f'Создай одно короткое упражнение по {lname(lang)} для уровня {LEVELS[u["level"]]}. Не показывай ответ. Попроси ученика написать свой вариант.'
    r=ai.generate_content(model=GEMINI_MODEL,contents=teacher_prompt(lang,u['level'])+'\n'+p,config=types.GenerateContentConfig(temperature=.5,max_output_tokens=500)); states[uid]=f'PRACTICE:{lang}'; bot.send_message(uid,r.text or 'Напиши простую фразу на изучаемом языке.')

def start_quiz(uid,lang):
    q=[('Как будет «спасибо»?',['你好','谢谢','再见','朋友'],1),('Что означает «你好»?',['спасибо','пока','привет','сколько'],2),('Как будет «я»?',['你','我','他','她'],1),('Что означает «哪里»?',['когда','почему','где','кто'],2)] if lang=='zh' else [('Как будет «спасибо»?',['안녕','감사합니다','친구','학교'],1),('Что означает «안녕하세요»?',['спасибо','здравствуйте','пока','сколько'],1),('Как будет «я» в вежливой форме?',['저','너','그','우리'],0),('Что означает «어디»?',['кто','где','почему','когда'],1)]
    quizzes[uid]={'questions':q,'i':0,'score':0}; send_q(uid)

def send_q(uid):
    s=quizzes.get(uid)
    if not s:return
    if s['i']>=len(s['questions']):
        score=s['score']; total=len(s['questions']); quizzes.pop(uid,None); u=user(uid); update(uid,total_answers=u['total_answers']+total,total_correct=u['total_correct']+score); add_xp(uid,score*15); bot.send_message(uid,f'🏁 <b>Тест завершён</b>\nРезультат: <b>{score}/{total}</b>\n⭐ +{score*15} XP',reply_markup=main_kb()); return
    question,opts,correct=s['questions'][s['i']]; m=telebot.types.InlineKeyboardMarkup()
    for i,o in enumerate(opts): m.add(telebot.types.InlineKeyboardButton(o,callback_data=f'answer:{i}'))
    bot.send_message(uid,f"🎯 <b>Вопрос {s['i']+1}/{len(s['questions'])}</b>\n\n{question}",reply_markup=m)

@bot.message_handler(commands=['start'])
def start(m):
    ensure(m.chat.id,m.from_user.first_name or 'Ученик'); states[m.chat.id]=None
    bot.send_message(m.chat.id,'👋 <b>Привет!</b> Я AI-преподаватель китайского и корейского.\n\nЯ веду уроки, исправляю ошибки, тренирую слова и грамматику, провожу тесты и сохраняю прогресс.',reply_markup=main_kb()); bot.send_message(m.chat.id,'🌐 Выбери основной язык:',reply_markup=lang_kb())

@bot.message_handler(func=lambda m:m.text in ['🇨🇳 Китайский','🇰🇷 Корейский'])
def choose(m):
    ensure(m.chat.id); lang='zh' if m.text.startswith('🇨🇳') else 'ko'; update(m.chat.id,language=lang); states[m.chat.id]=None; bot.send_message(m.chat.id,f'✅ Выбран {lname(lang)}',reply_markup=lesson_kb(lang))

@bot.message_handler(func=lambda m:m.text=='📚 Урок')
def lm(m): ensure(m.chat.id); lang=user(m.chat.id)['language']; bot.send_message(m.chat.id,'📚 <b>Учебный центр</b>',reply_markup=lesson_kb(lang))
@bot.message_handler(func=lambda m:m.text=='🧠 Тренировка')
def pm(m): ensure(m.chat.id); practice(m.chat.id,user(m.chat.id)['language'])
@bot.message_handler(func=lambda m:m.text=='📝 Словарь')
def wm(m): ensure(m.chat.id); words(m.chat.id,user(m.chat.id)['language'])
@bot.message_handler(func=lambda m:m.text=='📊 Прогресс')
def gm(m): ensure(m.chat.id); progress(m.chat.id)
@bot.message_handler(func=lambda m:m.text=='🎯 Тест')
def tm(m): ensure(m.chat.id); start_quiz(m.chat.id,user(m.chat.id)['language'])
@bot.message_handler(func=lambda m:m.text=='🤖 Учитель ИИ')
def am(m):
    ensure(m.chat.id); lang=user(m.chat.id)['language']; states[m.chat.id]=f'AI:{lang}'; bot.send_message(m.chat.id,f'🤖 <b>Учитель ИИ включён</b>\n\nЯзык: {lname(lang)}\nУровень: {LEVELS[user(m.chat.id)["level"]]}\n\nПиши фразы, проси объяснить грамматику, перевод, диалог или упражнение.')
@bot.message_handler(func=lambda m:m.text=='⚙️ Настройки')
def sm(m): ensure(m.chat.id); bot.send_message(m.chat.id,'⚙️ Выбери основной язык:',reply_markup=lang_kb())
@bot.message_handler(func=lambda m:m.text=='ℹ️ Помощь')
def hm(m): ensure(m.chat.id); bot.send_message(m.chat.id,'ℹ️ <b>Как заниматься</b>\n\n1. Каждый день проходи урок.\n2. Делай тренировку.\n3. Проверяй себя тестом.\n4. Включай AI-учителя для свободного общения и исправления ошибок.\n5. Следи за XP и серией.',reply_markup=main_kb())

@bot.callback_query_handler(func=lambda c:c.data.startswith('lang:'))
def cb_lang(c):
    lang=c.data.split(':')[1]; update(c.message.chat.id,language=lang); states[c.message.chat.id]=None; bot.answer_callback_query(c.id,'Сохранено'); bot.send_message(c.message.chat.id,f'✅ Основной язык: {lname(lang)}',reply_markup=main_kb())
@bot.callback_query_handler(func=lambda c:c.data.startswith('lesson:'))
def cb_l(c): bot.answer_callback_query(c.id); lesson(c.message.chat.id,c.data.split(':')[1])
@bot.callback_query_handler(func=lambda c:c.data.startswith('practice:'))
def cb_p(c): bot.answer_callback_query(c.id); practice(c.message.chat.id,c.data.split(':')[1])
@bot.callback_query_handler(func=lambda c:c.data.startswith('words:'))
def cb_w(c): bot.answer_callback_query(c.id); words(c.message.chat.id,c.data.split(':')[1])
@bot.callback_query_handler(func=lambda c:c.data.startswith('grammar:'))
def cb_g(c):
    uid=c.message.chat.id; lang=c.data.split(':')[1]; bot.answer_callback_query(c.id)
    try: states[uid]=f'AI:{lang}'; bot.send_message(uid,'✍️ <b>Грамматика</b>\n\n'+ask(uid,f'Дай короткий урок грамматики. Выбери одну практическую конструкцию и в конце дай одно упражнение.',lang))
    except Exception as e: print('grammar:',repr(e)); bot.send_message(uid,'⚠️ Не удалось загрузить урок.')
@bot.callback_query_handler(func=lambda c:c.data.startswith('quiz:'))
def cb_q(c): bot.answer_callback_query(c.id); start_quiz(c.message.chat.id,c.data.split(':')[1])
@bot.callback_query_handler(func=lambda c:c.data.startswith('answer:'))
def cb_a(c):
    uid=c.message.chat.id; s=quizzes.get(uid)
    if not s: bot.answer_callback_query(c.id,'Тест уже завершён.'); return
    selected=int(c.data.split(':')[1]); q,opts,correct=s['questions'][s['i']]
    if selected==correct: s['score']+=1; bot.answer_callback_query(c.id,'✅ Правильно!'); bot.send_message(uid,'✅ Правильно!')
    else: bot.answer_callback_query(c.id,'❌ Ошибка'); bot.send_message(uid,f'❌ Правильный ответ: <b>{opts[correct]}</b>')
    s['i']+=1; send_q(uid)

@bot.message_handler(content_types=['text'])
def text(m):
    uid=m.chat.id; ensure(uid,m.from_user.first_name or 'Ученик'); t=(m.text or '').strip(); state=states.get(uid)
    if state and state.startswith(('AI:','PRACTICE:')):
        lang=state.split(':')[1]
        try:
            bot.send_chat_action(uid,'typing')
            if state.startswith('PRACTICE:'): prompt=f'Ученик выполняет упражнение. Его ответ: {t}. Проверь как преподаватель: правильно/неправильно, исправление, короткое объяснение и одно следующее задание.'
            else: prompt=t
            bot.send_message(uid,ask(uid,prompt,lang))
        except Exception as e: print('AI:',repr(e)); bot.send_message(uid,'⚠️ Ошибка AI. Проверь GEMINI_API_KEY и попробуй снова.')
        return
    u=user(uid); low=t.lower()
    if any(x in low for x in ['китай','китайский','корей','корейский','иероглиф','граммат','переведи','перевод','упражнен','объясни']):
        states[uid]=f'AI:{u["language"]}'
        try: bot.send_message(uid,ask(uid,t,u['language']))
        except Exception as e: print('auto:',repr(e)); bot.send_message(uid,'⚠️ Ошибка AI.')
        return
    bot.send_message(uid,'Выбери раздел ниже 👇',reply_markup=main_kb())

if __name__=='__main__':
    init_db(); print('Language Teacher started:',GEMINI_MODEL); bot.infinity_polling(skip_pending=True,timeout=60,long_polling_timeout=60)

