content = open('bot.py').read()

old = '''async def safe_edit(msg, text: str):
    """Редактирует сообщение, молча игнорирует если уже нельзя."""
    try:
        await safe_edit(msg, text, fallback=update.message.reply_text)
    except Exception:
        pass'''

new = '''async def safe_edit(msg, text: str, fallback=None):
    try:
        await msg.edit_text(text)
    except Exception as e:
        print(f"[safe_edit] {type(e).__name__}: {e}")
        if fallback:
            try:
                await fallback(text)
            except Exception as e2:
                print(f"[safe_edit] fallback failed: {e2}")'''

if old in content:
    content = content.replace(old, new, 1)
    open('bot.py', 'w').write(content)
    print("OK — safe_edit исправлен")
else:
    print("NOT FOUND — покажи первые 300 символов safe_edit:")
    idx = content.find('async def safe_edit')
    print(repr(content[idx:idx+300]))
    