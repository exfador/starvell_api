from api.http_client import request_json
from api.response import response_items


async def fetch_chat_messages(
    session_cookie: str,
    chat_id: str,
    limit: int = 50,
    my_games_cookie: str | None = None,
    interlocutor_id: int | None = None,
) -> list[dict]:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": "https://starvell.com/chat",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    safe_limit = min(100, max(1, int(limit)))

    if interlocutor_id is not None:
        url = "https://starvell.com/api/bff/chat-page"
        payload = {
            "interlocutorId": int(interlocutor_id),
            "messagesListDto": {"chatId": chat_id, "limit": safe_limit},
        }
        data = await request_json("POST", url, headers=headers, cookies=cookies, timeout=20, json=payload)
        return response_items(data, "fetch chat messages")

    url = "https://starvell.com/api/messages/list"
    payload = {"chatId": chat_id, "limit": safe_limit}
    data = await request_json("POST", url, headers=headers, cookies=cookies, timeout=20, json=payload)
    return response_items(data, "fetch chat messages")
