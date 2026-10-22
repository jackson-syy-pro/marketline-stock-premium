# Marketline 股票會員 App

以 Flask、SQLite、Yahoo Finance 報價資料及 Stripe Billing 建立的股票觀察清單範例。會員可註冊／登入、查看最新可取得的股票價格、管理觀察清單，並透過 Stripe Checkout 訂閱 Premium；Stripe webhook 同步訂閱狀態和付款失敗通知。

## 功能

| 功能 | 說明 |
| --- | --- |
| 帳戶註冊及登入 | 電郵帳戶、Werkzeug 密碼雜湊、Flask-Login 登入狀態 |
| 股票觀察清單 | 加入／移除代號；預設展示 AAPL、MSFT、NVDA、0700.HK |
| 股票行情 | Flask 後端用 `yfinance` 讀取 Yahoo Finance 最近交易日收市價和前一交易日變化；可手動重新整理頁面 |
| Premium 訂閱 | Stripe Checkout `subscription` 模式，依 Stripe Price ID 建立週期扣款 |
| 會員狀態同步 | `checkout.session.completed`、`customer.subscription.*` webhook 更新 Premium 權限 |
| 付款失敗提示 | `invoice.payment_failed` 建立用戶通知，連到 Stripe Billing Portal 更新付款方式 |
| 會員管理 | Stripe Billing Portal 管理付款方法與訂閱 |
| Webhook 防重 | 保存 Stripe Event ID；重送事件會略過，避免重複處理 |

## 使用的外部 API

- **行情：** [Yahoo Finance via yfinance](https://ranaroussi.github.io/yfinance/)。此專案顯示最近可取得的日線收市價，不保證即時或完整；`yfinance` 說明其工具使用 Yahoo 公開 API、僅供個人用途。商業產品上線前，請確認 Yahoo 數據條款或改用有正式商業授權及 SLA 的行情供應商。
- **付款：** [Stripe Checkout Sessions](https://docs.stripe.com/api/checkout/sessions) 用於建立訂閱付款頁；[Stripe Billing Portal](https://docs.stripe.com/customer-management) 用於會員管理；[Stripe subscription webhooks](https://docs.stripe.com/billing/subscriptions/webhooks) 同步生命週期事件。
- **Webhook 簽名：** 使用 Stripe Python SDK `Webhook.construct_event` 驗證原始 request body 和 `Stripe-Signature`。Stripe 建議驗證簽名和防止重複事件；見[管理 webhook endpoints](https://docs.stripe.com/events/manage-webhook-endpoints)。

## 本機啟動

建議 Python 3.11 或以上。

```powershell
cd marketline-stock-premium
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

開啟 `http://127.0.0.1:5000`。資料庫會在第一次啟動時建立為 SQLite 檔案。

## Stripe 設定

1. Stripe Dashboard 切換至測試模式，建立 recurring Product/Price，並把 Price ID 放入 `STRIPE_PRICE_ID`。
2. 填寫 `.env` 的測試用 `STRIPE_SECRET_KEY`、`STRIPE_WEBHOOK_SECRET` 和隨機 `FLASK_SECRET_KEY`。
3. 本機測試可用 Stripe CLI 將 webhook 轉送到 `http://localhost:5000/webhooks/stripe`：

   ```powershell
   stripe listen --forward-to localhost:5000/webhooks/stripe
   ```

   將 CLI 顯示的 `whsec_...` 填入 `STRIPE_WEBHOOK_SECRET`。Dashboard endpoint secret 與 CLI secret 不同。
4. 測試事件至少選擇 `checkout.session.completed`、`customer.subscription.created`、`customer.subscription.updated`、`customer.subscription.deleted`、`invoice.payment_failed`。
5. 本地重新啟動 app 後，用 Stripe 測試卡流程驗證訂閱、續費失敗、權限更新及 Billing Portal。
6. Listener: stripe listen --all-snapshot --forward-to localhost:5000/webhooks/stripe

續費重試時間表應在 Stripe Dashboard 的 Revenue recovery / retries 設定。這個範例將重試交給 Stripe Billing，不會自行重複扣卡；可透過 `invoice.payment_failed` 通知會員處理付款方式。

## 環境變數

| 變數 | 用途 |
| --- | --- |
| `FLASK_SECRET_KEY` | Flask session 簽署密鑰；部署時必須使用隨機秘密值 |
| `DATABASE_URL` | SQLAlchemy 連線字串；預設 SQLite |
| `STRIPE_SECRET_KEY` | Stripe secret key，只能放伺服器端環境變數 |
| `STRIPE_PRICE_ID` | Stripe recurring Price ID |
| `PREMIUM_PRICE_DISPLAY` | 頁面顯示的訂閱價格／週期文字，須與 Stripe Price 一致 |
| `STRIPE_WEBHOOK_SECRET` | webhook endpoint signing secret |
| `PORT` | 服務 port，預設 5000 |
| `FLASK_DEBUG` | 設為 `1` 開啟除錯模式；只可本機開發使用 |

## API／事件對照

| App 路徑或 Stripe event | 用途 |
| --- | --- |
| `GET /dashboard` | 登入用戶行情和訂閱狀態 |
| `POST /subscribe` | 建立 Stripe Checkout subscription session |
| `POST /billing-portal` | 建立 Stripe Billing Portal session |
| `POST /webhooks/stripe` | 驗證、去重及同步 Stripe 事件 |
| `checkout.session.completed` | 儲存 Customer/Subscription 關聯並確認訂閱狀態 |
| `customer.subscription.created/updated/deleted` | 更新訂閱和 Premium entitlement |
| `invoice.payment_failed` | 產生用戶通知並將帳戶標示為 `past_due` |

