#!/data/data/com.termux/files/usr/bin/bash
# ================================================================
#  نظام الجرس (5.iajaward.org) من الهاتف (Termux) — احتياط عند انقطاع الكمبيوتر
#  يعمل بجانب موقع الجائزة (~/iaj) دون تعارض: منفذ 8005 ونفق مستقل «bell-standby»
#
#   bash ~/bell/standby/phone.sh setup        # مرة واحدة: المتطلبات
#   bash ~/bell/standby/phone.sh configure    # مرة واحدة: الرموز (من إعدادات IAJ تلقائياً) + الأزرار BELL
#   bash ~/bell/standby/phone.sh update-code  # آخر نسخة من GitHub (أو من Drive)
#   bash ~/bell/standby/phone.sh takeover     # ★ الطوارئ: أحدث بيانات + تشغيل + تحويل 5.iajaward.org للهاتف
#   bash ~/bell/standby/phone.sh stop         # إنهاء الطوارئ: إرجاع للكمبيوتر أو رفع بيانات الهاتف
#   bash ~/bell/standby/phone.sh server       # إرجاع 5.iajaward.org للكمبيوتر فوراً (بدون نقل بيانات)
#   bash ~/bell/standby/phone.sh status       # أين يعمل الآن
#   bash ~/bell/standby/phone.sh backup       # رفع بيانات الهاتف إلى Drive
#   bash ~/bell/standby/phone.sh try          # تجربة على الهاتف فقط: http://127.0.0.1:8005
#
#  القفل واللغة مشتركان مع زرّي «★ قفل أو فتح الأزرار» و«★ English - عربي» (standby/widgets.sh في IAJ).
# ================================================================
set -e
SITE="$HOME/bell"
LOCAL_BK="$HOME/bell-backups"
REMOTE="gdrive:IAJ-backups"
[ -f "$HOME/.iaj_standby" ] && . "$HOME/.iaj_standby"
BELL_REMOTE="${BELL_REMOTE:-$REMOTE/bell}"
[ -f "$HOME/.bell_standby" ] && . "$HOME/.bell_standby"
TOKEN_FILE="$HOME/.bell_tunnel_token"
export PYTHONUTF8=1
LANG_UI="${LANG_UI:-en}"   # Termux cannot join or right-to-left Arabic: English unless chosen
export IAJ_LANG="$LANG_UI"
T() { if [ "$LANG_UI" = "en" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
say() { printf '\n\033[1;36m» %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*"; exit 1; }
guard() {
  if [ -f "$HOME/.iaj_lock" ] && [ -z "$IAJ_NOGUARD" ]; then
    die "$(T "🔒 الأزرار الحساسة مقفلة — اضغط «★ قفل أو فتح الأزرار» لفتحها" "🔒 Sensitive buttons are locked - press Â«â ÙÙÙ Ø£Ù ÙØªØ­ Ø§ÙØ£Ø²Ø±Ø§Ø±Â» to unlock")"
  fi
}
need_code() { [ -f "$SITE/serve.py" ] || die "$(T "الكود غير موجود — شغّل: bash phone.sh update-code" "Code not found - run: bash phone.sh update-code")"; }
# إيقاف نفق هذا النظام فقط (لا يلمس نفق موقع الجائزة)
kill_my_tunnel() {
  [ -s "$TOKEN_FILE" ] || return 0
  local key; key=$(head -c 40 "$TOKEN_FILE")
  for p in $(pgrep -x cloudflared 2>/dev/null); do
    tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -qF "$key" && kill "$p" 2>/dev/null || true
  done
}
other_takeover() { pgrep -f "(iaj|rastkt|venues|attendance)/standby/phone.sh takeover" >/dev/null 2>&1; }

ensure_env() {
  if [ ! -f "$SITE/.env" ]; then
    KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
    printf 'SECRET_KEY=%s\nBASE_URL=https://5.iajaward.org\nPORT=8005\nTHREADS=4\nSECURE_COOKIES=1\n' "$KEY" > "$SITE/.env"
  fi
  # رمز Cloudflare: نفس رمز موقع الجائزة على هذا الهاتف
  if ! grep -q '^CF_API_TOKEN=.' "$SITE/.env" && grep -q '^CF_API_TOKEN=.' "$HOME/iaj/.env" 2>/dev/null; then
    grep '^CF_API_TOKEN=' "$HOME/iaj/.env" >> "$SITE/.env"
  fi
  chmod 600 "$SITE/.env" 2>/dev/null || true
}

case "$1" in
  setup)
    say "$(T "تثبيت المتطلبات" "Installing requirements")"
    pkg install -y python rclone cloudflared unzip >/dev/null
    pip install "Flask>=3.0" "Flask-SQLAlchemy>=3.1" "SQLAlchemy>=2.0.36" "openpyxl>=3.1" "waitress>=3.0.2" requests tzdata
    say "$(T "تم. الخطوة التالية: bash ~/bell/standby/phone.sh configure" "Done. Next: bash ~/bell/standby/phone.sh configure")"
    ;;

  update-code)
    if [ -f "$SITE/standby/gh_update.py" ]; then   # public repository: no token needed
      say "$(T "تنزيل الكود من GitHub" "Downloading code from GitHub")"
      if python "$SITE/standby/gh_update.py"; then
        say "$(T "تم تحديث الكود" "Code updated")"; exit 0
      fi
      say "$(T "تعذّر GitHub (تأكد أن رمز GitHub يشمل مستودع Bell) — أحاول من Drive" "GitHub failed (make sure the GitHub token includes the BELL repo) - trying Drive")"
    fi
    mkdir -p "$LOCAL_BK/code"
    rclone copy "$BELL_REMOTE/code" "$LOCAL_BK/code" --progress
    [ -f "$LOCAL_BK/code/bell-code.zip" ] || die "$(T "لم أجد bell-code.zip في Drive — شغّل backup_now.bat على الكمبيوتر" "bell-code.zip not in Drive - run backup_now.bat on the PC")"
    unzip -oq "$LOCAL_BK/code/bell-code.zip" -d "$HOME"
    sed -i 's/\r$//' "$SITE"/standby/*.sh 2>/dev/null || true
    say "$(T "تم تحديث الكود من Drive" "Code updated from Drive")"
    ;;

  restore)
    need_code; ensure_env
    say "$(T "تنزيل أحدث بيانات الجرس من Drive" "Downloading the latest bell data from Drive")"
    mkdir -p "$LOCAL_BK/db"
    if ! rclone lsf "$BELL_REMOTE/db" --include "*.db.gz" 2>/dev/null | grep -q . && ! ls "$LOCAL_BK/db"/*.db.gz >/dev/null 2>&1; then
      echo
      echo "$(T "  ✖ لا توجد أي نسخة احتياطية لنظام الجرس في Drive ($BELL_REMOTE/db)." "  ✖ There is no bell backup in Drive ($BELL_REMOTE/db).")"
      echo "$(T "    الكمبيوتر لم يرفع نسخة بعد. عند تشغيله: شغّل 3_autostart.bat كمسؤول (نسخة كل ساعة) ثم backup_now.bat" "    The PC has not uploaded one yet. When it is on: run 3_autostart.bat as admin (hourly backup), then backup_now.bat")"
      echo "$(T "    وتأكد أن مجلد النسخ BACKUP_DIR في .env داخل Google Drive: ...\\IAJ-backups\\bell" "    and that BACKUP_DIR in .env is inside Google Drive: ...\\IAJ-backups\\bell")"
      die "$(T "لم يتغيّر شيء — الموقع لم يُحوَّل" "Nothing changed - the site was not switched")"
    fi
    rclone copy "$BELL_REMOTE/db" "$LOCAL_BK/db" --include "*.db.gz" --max-age 30d --progress || true
    cd "$SITE" && python ops/restore.py --latest "$LOCAL_BK" --yes
    ;;

  try)
    need_code; ensure_env
    say "$(T "النظام يعمل على الهاتف فقط: http://127.0.0.1:8005 — للإيقاف Ctrl+C" "Running on the phone only: http://127.0.0.1:8005 - Ctrl+C to stop")"
    cd "$SITE" && DISABLE_SCHEDULER=1 python "$SITE/serve.py"
    ;;

  configure)
    need_code; ensure_env
    grep -q '^CF_API_TOKEN=.' "$SITE/.env" || {
      read -r -s -p "$(T "  الصق رمز Cloudflare API ثم Enter: " "  Paste the Cloudflare API token, then Enter: ")" TK; echo
      [ -n "$TK" ] && echo "CF_API_TOKEN=$TK" >> "$SITE/.env"; }
    say "$(T "جلب رمز نفق الهاتف bell-standby من Cloudflare (يُحفظ ولا يظهر)" "Fetching the phone tunnel token from Cloudflare (saved, not shown)")"
    (cd "$SITE" && python ops/cf.py token > "$TOKEN_FILE.tmp") && [ -s "$TOKEN_FILE.tmp" ] && mv "$TOKEN_FILE.tmp" "$TOKEN_FILE" && chmod 600 "$TOKEN_FILE" \
      || { rm -f "$TOKEN_FILE.tmp"; die "$(T "تعذّر — شغّل أولاً على الكمبيوتر 1_setup.bat (ينشئ النفق)" "Failed - run 1_setup.bat on the PC first (it creates the tunnel)")"; }
    mkdir -p "$HOME/.shortcuts"
    mk() { printf '#!/data/data/com.termux/files/usr/bin/bash\n%s\necho; read -r -p "Enter ↵" _\n' "$2" > "$HOME/.shortcuts/$1"; chmod +x "$HOME/.shortcuts/$1"; }
    mk "BELL 1 - تشغيل طوارئ الجرس"  "bash ~/bell/standby/phone.sh takeover"
    mk "BELL 1 - إنهاء طوارئ الجرس"  "bash ~/bell/standby/phone.sh stop"
    mk "BELL 2 - إرجاع الجرس للكمبيوتر" "bash ~/bell/standby/phone.sh server"
    mk "BELL 3 - أين يعمل نظام الجرس" "bash ~/bell/standby/phone.sh status"
    mk "BELL 4 - تحديث كود الجرس" "bash ~/bell/standby/phone.sh update-code && bash ~/bell/standby/phone.sh configure"
    mk "BELL 5 - نسخ بيانات الجرس إلى Drive" "bash ~/bell/standby/phone.sh backup"
    # one button per system instead of one per action (when the award site is on this phone): ~/iaj/standby/widgets.sh
    [ -f "$HOME/iaj/standby/widgets.sh" ] && WIDGETS_QUIET=1 bash "$HOME/iaj/standby/widgets.sh"
        say "$(T "تم. حدّث أداة Termux:Widget لتظهر أزرار BELL" "Done. Refresh the Termux:Widget to see the BELL buttons")"
    cd "$SITE" && python ops/cf.py status || true
    ;;

  backup)
    guard; need_code
    cd "$SITE"
    say "$(T "رفع بيانات الهاتف إلى Drive" "Uploading the phone data to Drive")"
    python ops/backup.py --dir "$LOCAL_BK" --tag phone --no-code --no-uploads
    rclone copy "$LOCAL_BK/db" "$BELL_REMOTE/db" --include "*-phone.db.gz" --max-age 1d --progress
    say "$(T "تم. على الكمبيوتر: back_to_server.bat" "Done. On the PC: back_to_server.bat")"
    ;;

  takeover)
    guard; need_code; ensure_env
    grep -q '^CF_API_TOKEN=.' "$SITE/.env" || die "$(T "شغّل configure أولاً" "Run configure first")"
    [ -s "$TOKEN_FILE" ] || die "$(T "شغّل configure أولاً (رمز النفق)" "Run configure first (tunnel token)")"
    if (cd "$SITE" && python ops/cf.py server-up 2>/dev/null); then
      echo
      echo "$(T "  ✋ الكمبيوتر يعمل ونظام الجرس سليم — لا حاجة للطوارئ." "  ✋ The PC is up and the bell system is fine - no need for emergency mode.")"
      read -r -p "$(T "  للتشغيل رغم ذلك اكتب FORCE، أو Enter للإلغاء: " "  To run anyway type FORCE, or Enter to cancel: ")" ANS
      [ "$ANS" = "FORCE" ] || die "$(T "تم الإلغاء" "Cancelled")"
    fi
    say "$(T "ملاحظة: جهاز الجرس المركزي في المكتب والتطبيقات ترنّ من جدولها المحفوظ حتى لو توقف الخادم — الطوارئ لتعديل الجدول أو تسجيل أجهزة جديدة فقط." "Note: the office bell player and the apps ring from their saved schedule even when the server is down - emergency mode is only needed to edit the schedule or register new devices.")"
    pkill -f "$SITE/serve.py" 2>/dev/null || true
    kill_my_tunnel; sleep 1
    bash "$0" restore
    cd "$SITE"
    termux-wake-lock 2>/dev/null || true
    say "$(T "تشغيل نظام الجرس" "Starting the bell system")"
    python "$SITE/serve.py" > "$HOME/bell-serve.log" 2>&1 &
    WEB=$!
    OK=0
    for i in $(seq 1 45); do
      kill -0 $WEB 2>/dev/null || break
      python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8005/',timeout=2)" 2>/dev/null && { OK=1; break; }
      sleep 2
    done
    if [ $OK != 1 ]; then
      kill $WEB 2>/dev/null; tail -20 "$HOME/bell-serve.log"
      die "$(T "لم يعمل على الهاتف — لم يُحوَّل شيء" "Did not start on the phone - nothing was switched")"
    fi
    say "$(T "تشغيل نفق الهاتف" "Starting the phone tunnel")"
    cloudflared tunnel --no-autoupdate run --token "$(cat "$TOKEN_FILE")" > "$HOME/bell-cloudflared.log" 2>&1 &
    CF=$!
    FP0=$(python ops/fingerprint.py 2>/dev/null | tail -1)
    DONE=0
    finish() {
      [ $DONE = 1 ] && return; DONE=1
      kill $WEB $CF 2>/dev/null
      say "$(T "إيقاف نظام الجرس على الهاتف" "Stopping the bell system on the phone")"
      FP1=$(python ops/fingerprint.py 2>/dev/null | tail -1)
      if [ "$FP0" = "$FP1" ]; then
        if python ops/cf.py server; then
          python -c "import sys;sys.path.insert(0,'ops');from common import stamp;print('server',stamp())" > "$LOCAL_BK/db/ACTIVE_SITE.txt"
          rclone copyto "$LOCAL_BK/db/ACTIVE_SITE.txt" "$BELL_REMOTE/db/ACTIVE_SITE.txt" 2>/dev/null || true
        else
          say "$(T "الكمبيوتر غير متصل — 5.iajaward.org سيبقى متوقفاً حتى يعود" "The PC is offline - 5.iajaward.org stays down until it is back")"
        fi
      else
        IAJ_NOGUARD=1 bash "$0" backup || true
        echo
        echo "$(T "  ⚠ سُجّلت بيانات حضور على الهاتف — على الكمبيوتر شغّل: back_to_server.bat" "  ⚠ Bell data was recorded on the phone - on the PC run: back_to_server.bat")"
      fi
      other_takeover || termux-wake-unlock 2>/dev/null || true
    }
    trap finish EXIT INT TERM
    say "$(T "تحويل 5.iajaward.org إلى الهاتف" "Switching 5.iajaward.org to the phone")"
    if python ops/cf.py phone; then
      python -c "import sys;sys.path.insert(0,'ops');from common import stamp;print('phone',stamp())" > "$LOCAL_BK/db/ACTIVE_SITE.txt"
      rclone copyto "$LOCAL_BK/db/ACTIVE_SITE.txt" "$BELL_REMOTE/db/ACTIVE_SITE.txt" 2>/dev/null || true
    else
      die "$(T "لم يتم التحويل" "Switch failed")"
    fi
    say "$(T "✅ الهاتف يشغّل 5.iajaward.org — لا تغلق النافذة. للإنهاء: زر «BELL» ← إنهاء الطوارئ أو Ctrl+C" "✅ The phone serves 5.iajaward.org - keep this window. To finish: the BELL button > End emergency mode, or Ctrl+C")"
    while kill -0 $WEB 2>/dev/null && kill -0 $CF 2>/dev/null; do sleep 5; done
    say "$(T "توقف النظام أو النفق على الهاتف" "The system or tunnel stopped on the phone")"
    exit 0
    ;;

  stop)
    PID=$(pgrep -f "bell/standby/phone.sh takeover" | grep -v "^$$\$" | head -1)
    if [ -z "$PID" ]; then
      say "$(T "طوارئ الجرس ليست قيد التشغيل" "Bell emergency mode is not running")"
    else
      say "$(T "إنهاء الطوارئ… (قد يستغرق دقيقة)" "Stopping emergency mode... (may take a minute)")"
      kill -INT "$PID" 2>/dev/null || true
      for i in $(seq 1 45); do kill -0 "$PID" 2>/dev/null || break; sleep 2; done
    fi
    need_code; cd "$SITE" && python ops/cf.py status 2>/dev/null || true
    ;;

  server)
    guard; need_code
    read -r -p "$(T "  إرجاع 5.iajaward.org للكمبيوتر بدون نقل بيانات — اكتب YES: " "  Move 5.iajaward.org back to the PC without moving data - type YES: ")" OK
    [ "$OK" = "YES" ] || die "$(T "تم الإلغاء" "Cancelled")"
    cd "$SITE" && python ops/cf.py server
    ;;

  status)
    need_code; cd "$SITE" && python ops/cf.py status
    ;;

  *)
    sed -n '2,18p' "$0"
    ;;
esac
