import json
import aiohttp


async def send_chat_message(session_cookie: str, chat_id: str, content: str) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": f"https://starvell.com/chat/{chat_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {
        "session": session_cookie,
        "starvell.theme": "dark",
        "starvell.time_zone": "Europe/Moscow",
        "starvell.my_games": "10,1,11",
    }
    payload = {"chatId": chat_id, "content": content}
    url = "https://starvell.com/api/messages/send"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            response_text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {response_text}")
            try:
                return json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Invalid response from server") from exc


