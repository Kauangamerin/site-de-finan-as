import os, re, sqlite3, time
from functools import wraps

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finance.db")
PRICE_API = "https://api.coingecko.com/api/v3/simple/price"  # free, no key
DEFAULT_COINS = ["bitcoin", "ethereum", "solana"]
REFRESH_SECONDS = 60

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-deploying")


# ---------- database ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS watchlist(
        user_id INTEGER, coin TEXT, target REAL, PRIMARY KEY(user_id, coin));
    CREATE TABLE IF NOT EXISTS prices(
        coin TEXT, usd REAL, change24 REAL, ts INTEGER);
    CREATE INDEX IF NOT EXISTS idx_prices ON prices(coin, ts);
    CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY, user_id INTEGER, coin TEXT,
        price REAL, target REAL, ts INTEGER);
    """)
    conn.commit()
    conn.close()


# ---------- automation: runs every minute in the background ----------
def fetch_prices():
    conn = sqlite3.connect(DB)
    coins = [r[0] for r in conn.execute("SELECT DISTINCT coin FROM watchlist")]
    if not coins:
        conn.close()
        return
    try:
        r = requests.get(PRICE_API, timeout=10, params={
            "ids": ",".join(coins), "vs_currencies": "usd",
            "include_24hr_change": "true"})
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print("Price fetch failed:", e)
        conn.close()
        return
    now = int(time.time())
    for coin, v in data.items():
        if "usd" not in v:
            continue
        conn.execute("INSERT INTO prices VALUES (?,?,?,?)",
                     (coin, v["usd"], v.get("usd_24h_change") or 0, now))
        hits = conn.execute(
            "SELECT user_id, target FROM watchlist "
            "WHERE coin=? AND target IS NOT NULL AND ? >= target",
            (coin, v["usd"])).fetchall()
        for uid, target in hits:  # price alert: record it, then clear the target
            conn.execute(
                "INSERT INTO alerts(user_id,coin,price,target,ts) VALUES (?,?,?,?,?)",
                (uid, coin, v["usd"], target, now))
            conn.execute("UPDATE watchlist SET target=NULL WHERE user_id=? AND coin=?",
                         (uid, coin))
    conn.execute("DELETE FROM prices WHERE ts < ?", (now - 7 * 86400,))
    conn.commit()
    conn.close()


# ---------- auth ----------
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "uid" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="login required"), 401
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


@app.route("/")
def home():
    return redirect(url_for("dashboard" if "uid" in session else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password"], request.form["password"]):
            session["uid"], session["email"] = user["id"], user["email"]
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.")
    return render_template("login.html", mode="login")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        pw = request.form["password"]
        if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(pw) < 8:
            flash("Use a valid email and a password of at least 8 characters.")
        else:
            try:
                cur = db().execute("INSERT INTO users(email,password) VALUES (?,?)",
                                   (email, generate_password_hash(pw)))
                for c in DEFAULT_COINS:
                    db().execute("INSERT INTO watchlist(user_id,coin) VALUES (?,?)",
                                 (cur.lastrowid, c))
                db().commit()
                session["uid"], session["email"] = cur.lastrowid, email
                fetch_prices()
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                flash("That email already has an account. Log in instead.")
    return render_template("login.html", mode="register")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- dashboard + API ----------
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", email=session["email"])


@app.route("/api/prices")
@login_required
def api_prices():
    uid = session["uid"]
    coins = db().execute("""
        SELECT w.coin, w.target, p.usd, p.change24 FROM watchlist w
        LEFT JOIN prices p ON p.coin = w.coin
          AND p.ts = (SELECT MAX(ts) FROM prices WHERE coin = w.coin)
        WHERE w.user_id=? ORDER BY w.coin""", (uid,)).fetchall()
    alerts = db().execute(
        "SELECT coin, price, target, ts FROM alerts WHERE user_id=? "
        "ORDER BY ts DESC LIMIT 8", (uid,)).fetchall()
    return jsonify(coins=[dict(c) for c in coins], alerts=[dict(a) for a in alerts])


@app.route("/api/history/<coin>")
@login_required
def api_history(coin):
    rows = db().execute(
        "SELECT ts, usd FROM prices WHERE coin=? ORDER BY ts DESC LIMIT 300",
        (coin,)).fetchall()
    return jsonify([dict(r) for r in reversed(rows)])


@app.route("/api/watchlist", methods=["POST"])
@login_required
def add_coin():
    body = request.get_json(silent=True) or {}
    coin = str(body.get("coin", "")).strip().lower()
    if not re.fullmatch(r"[a-z0-9-]{1,60}", coin):
        return jsonify(error="Enter a CoinGecko coin ID, like bitcoin."), 400
    try:
        target = float(body["target"]) if body.get("target") not in (None, "") else None
    except ValueError:
        return jsonify(error="Target price must be a number."), 400
    db().execute("INSERT OR REPLACE INTO watchlist(user_id,coin,target) VALUES (?,?,?)",
                 (session["uid"], coin, target))
    db().commit()
    fetch_prices()
    return jsonify(ok=True)


@app.route("/api/watchlist/<coin>", methods=["DELETE"])
@login_required
def remove_coin(coin):
    db().execute("DELETE FROM watchlist WHERE user_id=? AND coin=?",
                 (session["uid"], coin))
    db().commit()
    return jsonify(ok=True)


init_db()
if os.environ.get("RUN_SCHEDULER", "1") == "1":
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(fetch_prices, "interval", seconds=REFRESH_SECONDS)
    scheduler.start()

if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", 5000)))
