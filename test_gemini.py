import yaml
import requests

config = yaml.safe_load(open('config.yaml', encoding='utf-8'))
api_key = config.get('gemini_api_key', '')
print('APIキー:', api_key[:20], '...')

prompt = 'テスト。以下のJSON形式のみで返してください: {"key_reason": "テスト成功", "summary": "テスト", "caution": ""}'
resp = requests.post(
    'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
    params={'key': api_key},
    headers={'Content-Type': 'application/json'},
    json={'contents': [{'parts': [{'text': prompt}]}]},
    timeout=10,
)
print('ステータス:', resp.status_code)
print('レスポンス:', resp.text[:500])