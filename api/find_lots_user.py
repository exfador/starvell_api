import aiohttp
import json


async def find_user_lots(session_cookie: str, sid_cookie: str, user_id: int) -> list[dict]:
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "ru,en;q=0.9",
        "cache-control": "max-age=0",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {
        "session": session_cookie,
        "starvell.theme": "dark",
        "starvell.time_zone": "Europe/Moscow",
        "starvell.my_games": "10,1,11",
    }
    if sid_cookie:
        cookies["sid"] = sid_cookie
    url = f"https://starvell.com/users/{user_id}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            html = await resp.text()

    marker = '<script id="__NEXT_DATA__" type="application/json">'
    idx = html.find(marker)
    if idx == -1:
        return []
    json_start = html.find('{', idx)
    if json_start == -1:
        return []
    json_end = html.find('</script>', json_start)
    if json_end == -1:
        return []
    data = json.loads(html[json_start:json_end])

    page_props = data.get("props", {}).get("pageProps", {})
    categories = page_props.get("categoriesWithOffers", [])
    lots: list[dict] = []
    for category in categories:
        offers = category.get("offers", [])
        for offer in offers:
            offer_id = offer.get("id")
            price = offer.get("price")
            availability = offer.get("availability")
            brief = (
                (offer.get("descriptions") or {})
                .get("rus", {})
                .get("briefDescription")
            )
            attrs = offer.get("attributes", [])
            labels = [a.get("valueLabel") for a in attrs if a.get("valueLabel")]
            title_parts = [p for p in [brief, *labels] if p]
            title = ", ".join(title_parts) if title_parts else None
            lots.append(
                {
                    "id": offer_id,
                    "title": title,
                    "availability": availability,
                    "price": price,
                    "url": f"https://starvell.com/offers/{offer_id}" if offer_id else None,
                }
            )
    return lots


