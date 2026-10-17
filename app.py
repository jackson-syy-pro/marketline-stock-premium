import os
from datetime import datetime, timezone

import stripe
import yfinance as yf
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY", "change-this-development-key"),
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///stock_app.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_premium = db.Column(db.Boolean, nullable=False, default=False)
    stripe_customer_id = db.Column(db.String(255), unique=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True)
    subscription_status = db.Column(db.String(40), nullable=False, default="free")
    watchlist = db.relationship("WatchlistItem", backref="user", cascade="all, delete-orphan")
    notifications = db.relationship("Notification", backref="user", cascade="all, delete-orphan")


class WatchlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    symbol = db.Column(db.String(20), nullable=False)
    __table_args__ = (db.UniqueConstraint("user_id", "symbol", name="uq_user_symbol"),)


class StripeEvent(db.Model):
    id = db.Column(db.String(255), primary_key=True)
    event_type = db.Column(db.String(120), nullable=False)
    received_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    read = db.Column(db.Boolean, nullable=False, default=False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_plan_details():
    return {"premium_price_display": os.getenv("PREMIUM_PRICE_DISPLAY", "請先設定會員價格")}


def get_quote(symbol):
    """Return the latest available Yahoo Finance data; this is not guaranteed real-time."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
        if hist.empty:
            return None
        latest = hist.iloc[-1]
        previous = hist.iloc[-2] if len(hist.index) > 1 else latest
        price = float(latest["Close"])
        prior = float(previous["Close"])
        info = getattr(ticker, "fast_info", {})
        currency = info.get("currency", "USD") if hasattr(info, "get") else "USD"
        timestamp = hist.index[-1].strftime("%Y-%m-%d")
        return {"symbol": symbol, "price": price, "change": price - prior,
                "change_pct": ((price - prior) / prior * 100) if prior else 0,
                "currency": currency, "as_of": timestamp}
    except Exception:
        app.logger.exception("Quote lookup failed for %s", symbol)
        return None


@app.get("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or len(password) < 8:
            flash("請輸入有效電郵，密碼最少 8 個字元。", "error")
        elif User.query.filter_by(email=email).first():
            flash("此電郵已註冊。", "error")
        else:
            user = User(email=email, password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("電郵或密碼不正確。", "error")
    return render_template("auth.html", mode="login")


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


@app.get("/dashboard")
@login_required
def dashboard():
    items = WatchlistItem.query.filter_by(user_id=current_user.id).order_by(WatchlistItem.symbol).all()
    symbols = [item.symbol for item in items] or ["AAPL", "MSFT", "NVDA", "0700.HK"]
    quotes = [q for symbol in symbols if (q := get_quote(symbol))]
    notices = Notification.query.filter_by(user_id=current_user.id, read=False).order_by(Notification.created_at.desc()).all()
    return render_template("dashboard.html", quotes=quotes, notices=notices)


@app.get("/premium")
@login_required
def premium_insights():
    if not current_user.is_premium:
        flash("進階市場洞察只限 Premium 會員使用。", "error")
        return redirect(url_for("dashboard"))
    items = WatchlistItem.query.filter_by(user_id=current_user.id).order_by(WatchlistItem.symbol).all()
    symbols = [item.symbol for item in items] or ["AAPL", "MSFT", "NVDA", "0700.HK"]
    insights = []
    for symbol in symbols:
        try:
            history = yf.Ticker(symbol).history(period="1mo", interval="1d", auto_adjust=False)
            if history.empty:
                continue
            closes = history["Close"].dropna()
            latest = float(closes.iloc[-1])
            start = float(closes.iloc[-min(6, len(closes))])
            insights.append({"symbol": symbol, "latest": latest,
                             "week_change_pct": ((latest - start) / start * 100) if start else 0,
                             "as_of": closes.index[-1].strftime("%Y-%m-%d")})
        except Exception:
            app.logger.exception("Premium insight lookup failed for %s", symbol)
    return render_template("premium.html", insights=insights)


@app.post("/watchlist")
@login_required
def add_watchlist():
    symbol = request.form.get("symbol", "").strip().upper()
    if not symbol or len(symbol) > 20 or not all(c.isalnum() or c in ".-^=" for c in symbol):
        flash("請輸入有效股票代號，例如 AAPL 或 0700.HK。", "error")
    elif not WatchlistItem.query.filter_by(user_id=current_user.id, symbol=symbol).first():
        db.session.add(WatchlistItem(user_id=current_user.id, symbol=symbol))
        db.session.commit()
        flash(f"已加入 {symbol}。", "success")
    return redirect(url_for("dashboard"))


@app.post("/watchlist/<int:item_id>/delete")
@login_required
def delete_watchlist(item_id):
    item = db.get_or_404(WatchlistItem, item_id)
    if item.user_id == current_user.id:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for("dashboard"))


@app.post("/subscribe")
@login_required
def subscribe():
    if not stripe.api_key or not os.getenv("STRIPE_PRICE_ID"):
        flash("Stripe 尚未設定。請管理員填寫 STRIPE_SECRET_KEY 同 STRIPE_PRICE_ID。", "error")
        return redirect(url_for("dashboard"))
    try:
        customer_id = current_user.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(email=current_user.email, metadata={"user_id": str(current_user.id)})
            current_user.stripe_customer_id = customer.id
            db.session.commit()
            customer_id = customer.id
        session = stripe.checkout.Session.create(
            mode="subscription", customer=customer_id,
            line_items=[{"price": os.environ["STRIPE_PRICE_ID"], "quantity": 1}],
            success_url=url_for("dashboard", _external=True) + "?checkout=success",
            cancel_url=url_for("dashboard", _external=True) + "?checkout=cancelled",
            client_reference_id=str(current_user.id),
            metadata={"user_id": str(current_user.id)},
            subscription_data={"metadata": {"user_id": str(current_user.id)}},
        )
        return redirect(session.url, code=303)
    except stripe.StripeError:
        app.logger.exception("Could not create Stripe Checkout Session")
        flash("暫時未能建立付款頁，請稍後再試。", "error")
        return redirect(url_for("dashboard"))


@app.post("/billing-portal")
@login_required
def billing_portal():
    if not stripe.api_key or not current_user.stripe_customer_id:
        flash("未找到 Stripe 會員記錄。", "error")
        return redirect(url_for("dashboard"))
    try:
        portal = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=url_for("dashboard", _external=True),
        )
        return redirect(portal.url, code=303)
    except stripe.StripeError:
        app.logger.exception("Could not create Stripe Billing Portal Session")
        flash("暫時未能開啟會員管理頁。", "error")
        return redirect(url_for("dashboard"))


def _user_for_stripe_object(obj):
    metadata = obj.get("metadata") or {}
    user_id = metadata.get("user_id")
    if user_id:
        return db.session.get(User, int(user_id))
    customer_id = obj.get("customer")
    if customer_id:
        return User.query.filter_by(stripe_customer_id=customer_id).first()
    return None


def _sync_subscription(subscription, status=None):
    user = _user_for_stripe_object(subscription)
    if not user:
        return
    status = status or subscription.get("status", "unknown")
    user.stripe_customer_id = subscription.get("customer") or user.stripe_customer_id
    user.stripe_subscription_id = subscription.get("id") or user.stripe_subscription_id
    user.subscription_status = status
    user.is_premium = status in {"active", "trialing"}
    db.session.add(user)


@app.post("/webhooks/stripe")
def stripe_webhook():
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        return {"error": "webhook is not configured"}, 503
    payload = request.get_data()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except (ValueError, stripe.SignatureVerificationError):
        return {"error": "invalid webhook"}, 400

    if db.session.get(StripeEvent, event.id):
        return {"received": True, "duplicate": True}, 200
    db.session.add(StripeEvent(id=event.id, event_type=event.type))
    obj = event.data.object
    if event.type == "checkout.session.completed":
        user = db.session.get(User, int(obj.client_reference_id)) if obj.client_reference_id else None
        if user:
            user.stripe_customer_id = obj.customer or user.stripe_customer_id
            user.stripe_subscription_id = obj.subscription or user.stripe_subscription_id
        if obj.mode == "subscription" and obj.subscription:
            # Checkout completion is not sufficient to grant access until Stripe confirms active status.
            try:
                subscription = stripe.Subscription.retrieve(obj.subscription)
                _sync_subscription(subscription)
            except stripe.StripeError:
                app.logger.exception("Could not retrieve subscription after Checkout completion")
    elif event.type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        _sync_subscription(obj)
    elif event.type == "invoice.payment_failed":
        customer_id = obj.customer
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            db.session.add(Notification(user_id=user.id,
                message="會員續費未成功，請到會員管理頁更新付款方式。"))
            if user.subscription_status == "active":
                user.subscription_status = "past_due"
                user.is_premium = False
    db.session.commit()
    return {"received": True}, 200


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
