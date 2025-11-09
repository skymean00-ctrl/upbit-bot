from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def index():
    # 여기에 Upbit Bot의 데이터를 가져오는 로직을 추가할 수 있습니다.
    # 예: bot_status = get_upbit_bot_status()
    #     current_balance = get_upbit_balance()
    
    bot_status = '실행 중' # 임시 데이터
    current_balance = '1,000,000 KRW' # 임시 데이터
    
    return render_template('index.html', bot_status=bot_status, current_balance=current_balance)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

