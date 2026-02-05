from flask import Flask, jsonify, request   # [基礎] 載入 Flask 網頁框架，以及處理 JSON 與請求的工具
import os                                   # [系統] 讀取作業系統環境變數，用來抓取 K8s 注入的配置
import redis                                # [資料] 載入 Redis 套件，處理與緩存資料庫的通訊
import requests                             # [網路] 載入 HTTP 請求工具，用來呼叫 Telegram 的 API

# ==============================================================================
# [SRE 監控核心] 整合 Prometheus Exporter
# 這裡會自動產生一個 /metrics 路由，讓 Prometheus 定時來爬取流量數據
# ==============================================================================
from prometheus_flask_exporter import PrometheusMetrics 

app = Flask(__name__)                       # [初始化] 建立 Flask 應用程式物件物件

# [監控初始化] 讓程式自動統計 HTTP 請求數量、回應時間與錯誤率
metrics = PrometheusMetrics(app)

# ==============================================================================
# [藍綠部署核心] 讀取版本標籤
# 透過 K8s 的 Deployment YAML 注入 APP_VERSION，讓我們區分現在是 v1 還是 v2
# ==============================================================================
APP_VERSION = os.environ.get('APP_VERSION', 'v1')  

# [環境配置] 讀取 Telegram 機器人的金鑰與頻道 ID (通常來自 K8s Secret)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# [服務發現] 讀取 Redis 的連線位址，在 K8s 中通常指向 redis-service
redis_host_address = os.environ.get('REDIS_HOST', 'localhost')

try:
    # [連線資料庫] 建立 Redis 連線，decode_responses=True 讓資料讀出來直接是字串
    redis_client = redis.Redis(host=redis_host_address, port=6379, db=0, decode_responses=True)
    redis_client.ping() # [健康檢查] 確保資料庫真的連得上
    print(f"✅ 成功連線到 Redis: {redis_host_address}")
except Exception as error_detail:
    print(f"❌ Redis 連線失敗: {error_detail}")

# ------------------------------------------------------------------------------
# [小幫手] Telegram 通知函式
# ------------------------------------------------------------------------------
def send_telegram_notification(message):
    """
    當使用者投票時，將結果推送到 SRE 的 Telegram 頻道。
    訊息開頭會標註 [v1] 或 [v2]，方便觀察分流狀況。
    """
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        
        # [封裝訊息] 這裡定義要傳給 Telegram 的內容格式
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"[{APP_VERSION}] {message}" 
        }
        try:
            # [發送請求] 設定 1 秒逾時，避免 Telegram 伺服器反應慢拖累我們的網頁
            requests.post(url, json=payload, timeout=1) 
        except:
            pass # [容錯處理] 即使通知失敗，也不能讓投票功能掛掉

# ------------------------------------------------------------------------------
# [路由 1] 首頁 - 渲染投票介面 (HTML/CSS/JS)
# ------------------------------------------------------------------------------
@app.route('/')
def hello():
    # [伺服器端渲染] 將 APP_VERSION 注入 HTML 模板中
    # 注意：在 Python f-string 裡，CSS 和 JS 的大括號必須寫兩次 {{ }} 來避開解析
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>DevOps 投票中心 - {APP_VERSION}</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Microsoft JhengHei', sans-serif; text-align: center; padding: 50px; background-color: #f4f4f9; }}
            .container {{ background: white; max-width: 600px; margin: auto; padding: 30px; border-radius: 15px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; }}
            .info {{ color: #777; font-size: 14px; margin-bottom: 30px; }}
            .btn {{ padding: 15px 30px; font-size: 22px; margin: 10px; cursor: pointer; border: none; border-radius: 50px; transition: transform 0.2s; }}
            .btn:active {{ transform: scale(0.95); }}
            .apple {{ background-color: #ff4d4d; color: white; }}
            .banana {{ background-color: #ffd700; color: black; }}
            .stat {{ font-size: 28px; margin-top: 30px; font-weight: bold; }}
            .count {{ color: #007bff; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🏆 水果人氣投票 ({APP_VERSION})</h1>
            <p class="info">後端資料庫: {redis_host_address} | Pod ID: {os.environ.get('HOSTNAME', 'local')}</p>
            
            <div id="buttons">
                <button class="btn apple" onclick="vote('apple')">🍎 投給 Apple</button>
                <button class="btn banana" onclick="vote('banana')">🍌 投給 Banana</button>
            </div>

            <div class="stat">
                <p>🍎 Apple 得票: <span id="apple-count" class="count">...</span></p>
                <p>🍌 Banana 得票: <span id="banana-count" class="count">...</span></p>
            </div>
        </div>

        <script>
            // 頁面載入後自動更新票數
            window.onload = updateCounts;

            // [AJAX 投票] 發送請求到後端 API，取得最新票數並更新網頁數字
            function vote(item) {{
                fetch('/vote/' + item)
                    .then(res => res.json())
                    .then(data => {{
                        document.getElementById(item + '-count').innerText = data.current_count;
                    }});
            }}

            // [更新票數] 從 /list API 拿回所有水果的統計數字
            function updateCounts() {{
                fetch('/list')
                    .then(res => res.json())
                    .then(data => {{
                        document.getElementById('apple-count').innerText = data.data['apple'] || 0;
                        document.getElementById('banana-count').innerText = data.data['banana'] || 0;
                    }});
            }}
        </script>
    </body>
    </html>
    """
    return html_content

# ------------------------------------------------------------------------------
# [路由 2] 投票介面 - 處理加分邏輯
# ------------------------------------------------------------------------------
@app.route('/vote/<product_name>')
def vote_item(product_name):
    try:
        # [Redis 指令] incr 會將指定的 key 數值加一，這在多執行緒下是安全的
        new_count = redis_client.incr(product_name)
        
        # 發送 Telegram 通知，標註目前是哪個版本收到的請求
        send_telegram_notification(f"🔥 {product_name} 獲得一票！目前總票數: {new_count}")

        # [回應前端] 回傳 JSON 格式的成功訊息與最新票數
        return jsonify({
            "status": "success", 
            "current_count": new_count,
            "version": APP_VERSION
        })
    except Exception as error_detail:
        # [錯誤處理] 若發生異常，回傳 500 錯誤碼與原因
        return jsonify({"error": str(error_detail)}), 500

# ------------------------------------------------------------------------------
# [路由 3] 統計列表 - 取得資料庫內所有資料
# ------------------------------------------------------------------------------
@app.route('/list')
def get_all():
    try:
        # [Redis 指令] 找出資料庫內所有的 Key (例如 apple, banana)
        keys = redis_client.keys('*')
        
        # [資料整理] 使用字典推導式，遍歷所有 Key 並抓取對應的數值轉成整數
        final_product_dictionary = {key: int(redis_client.get(key) or 0) for key in keys}
        
        # [回應前端] 將結果打包成 JSON
        return jsonify({"data": final_product_dictionary, "version": APP_VERSION})
    except Exception as error_detail:
        return jsonify({"error": str(error_detail)}), 500

# ------------------------------------------------------------------------------
# [啟動點] 執行 Flask 伺服器
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # host='0.0.0.0' 是關鍵，它讓容器可以接收來自外部網路 (K8s) 的流量
    app.run(host='0.0.0.0', port=5000)