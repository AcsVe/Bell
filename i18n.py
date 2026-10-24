"""Two-language messages for code that runs outside the templates (imports, services).

    raise Bi('الملف فارغ.', 'The file is empty.')      # a ValueError that carries both languages
    flash(err_text(e))                                  # the admin's language (session['lang'])
"""
from flask import has_request_context, session


class Bi(ValueError):
    def __init__(self, ar, en):
        super().__init__(ar)
        self.ar, self.en = ar, en


def lang():
    return session.get('lang', 'ar') if has_request_context() else 'ar'


def pick(ar, en):
    return en if lang() == 'en' else ar


def err_text(e):
    return pick(e.ar, e.en) if isinstance(e, Bi) else str(e)


def join_pairs(pairs, sep=' | '):
    """[(ar, en), …] → (ar text, en text)."""
    return sep.join(a for a, _ in pairs), sep.join(b for _, b in pairs)
