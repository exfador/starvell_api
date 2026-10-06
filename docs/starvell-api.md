# Starvell API: справочник для бота

Сверено 2026-10-03 по официальной документации `https://starvell.com/api/developers/docs` (OpenAPI 3.0, 24 метода) и по публичному коду сайта (buildId `wKd3zAmZ30_A67iS76Xkr`). Авторизация — cookie `session`. Ответы сайт разворачивает из поля `data`, если оно есть; ошибки имеют вид `{message: string | string[], data: {code, ...}}`. HTML вместо JSON с кодом 403 означает антибот-защиту (QRATOR).

## Что использует бот

| Метод | Путь | Где в боте | Статус |
|---|---|---|---|
| GET | `/_next/data/{buildId}/index.json` | `api/auth.py` — проверка сессии | есть; buildId меняется при каждом деплое, при 404 бот перечитывает его с главной |
| GET | `/api/profiles/me` | `api/auth.py` — запасная проверка | в документации |
| GET | `/_next/data/{buildId}/chat.json` | `api/chats.py` — список чатов | есть (серверная страница) |
| POST | `/api/bff/chat-page` | `api/messages.py` — первая страница сообщений | используется сайтом; `interlocutorId` — число, `messagesListDto` как у list-v2 |
| POST | `/api/messages/list-v2` | `api/messages.py` — история и чаты без собеседника | в документации; `limit` 1–50, курсоры `beforeId`/`afterId`/`aroundId`, ответ `items` (новые первыми), `hasMoreBefore`, `nextCursor`; 60 запросов/мин |
| POST | `/api/messages/send` | `api/send_message.py` | в документации; `chatId`, `content`, опционально `replyToMessageId`; 30 запросов/мин |
| POST | `/api/messages/send-with-image?chatId=` | `api/send_message.py` | используется сайтом; multipart-поле `image`, поля `content` нет — подпись бот отправляет отдельным сообщением |
| GET | `/_next/data/{buildId}/account/sells.json` | `api/orders.py` — мониторинг заказов | есть; параметр `page` в коде сайта не читается — для полной истории используется `orders/list` |
| POST | `/api/orders/list` | `api/orders.py` — статистика | в документации; `filter.userType = "seller"`, `status`, `limit`, `offset`; сайт передаёт `with: {buyer: true}` |
| POST | `/api/orders/refund` | `api/orders.py` | в документации; полный возврат, `orderId` |
| POST | `/api/offers/bump` | `api/bump.py` | в документации; `gameId`, `categoryIds`; кулдаун — код `OFFERS_BUMP_COOLDOWN` с `data.retryAfterSeconds` |
| GET | `/_next/data/{buildId}/profile/{username}.json?username=` | `api/find_lots_user.py` | есть |
| GET | `/_next/data/{buildId}/offers/{id}.json?offer_id=` | `api/offer_details.py` | есть |

Плагин Stars дополнительно вызывает `POST /api/offers/{publicId}/partial-update` (в документации; `price` — строка в рублях, `availability`, `isActive`; 100 запросов/мин).

Отметка прочитанным — `POST /api/chats/read {chatId}` (`api/chats.py`, в документации): кнопка «Прочитано» в уведомлении, автоматически после ответа из Telegram и, при `AUTO_READ_CHATS`, после доставки уведомления.

## Документированные методы, которые бот пока не использует

- `POST /api/chats/list`, `/api/chats/list-pinned`, `/api/chats/list-open-order` (`role: SELLER`), `GET /api/chats/{id}` — списки чатов с пагинацией (`offset` ≤ 1200, `limit` ≤ 50; 90 запросов/мин).
- `POST /api/chats/send-typing` — индикатор набора.
- `GET /api/orders/{id}`, `POST /api/orders/{id}/mark-seller-completed`.
- `POST /api/offers/list-my`, `GET /api/offers/{publicId}`, `POST /api/offers/{id}/update` (25 запросов/мин).
- `POST /api/reviews/list`, `/api/reviews/category-list`, `POST /api/review-responses/create` и связанные методы ответов на отзывы.

## Перечисления

- Статус заказа: `PRE_CREATED`, `CREATED`, `COMPLETED`, `REFUND`, `FAILED`. Тип возврата: `FULL`, `PARTIAL`, `COMPENSATION`.
- Цены заказа (`basePrice`, `totalPrice`) — в копейках; цены предложений — строки в рублях.
- Тип сообщения: `DEFAULT`, `NOTIFICATION`, `EVENT`, `ORDER_FEEDBACK`; флаги в `metadata`: `isAuto`, `isAutoResponse`, `orderId`, `notificationType`.
- Коды ошибок: `OFFERS_BUMP_COOLDOWN`, `FORBIDDEN_WORDS_FOUND`, `SESSION_NOT_FOUND`, `ACCOUNT_IS_BANNED`, `AUTH_EMAIL_REQUIRED`, `ACCESS_COOKIE_MISSING`, `FUNDS_FROZEN`, `INSUFFICIENT_FUNDS`, `KYC_REQUIRED`, `TWOFA_CODE_IS_REQUIRED`.
- Ограничения: текст сообщения до 1000 символов, изображение до 5 МБ (jpeg, png, webp).

## Realtime

Сайт получает события через socket.io v4 (`EIO=4`, только websocket, путь `/socket.io`, авторизация cookie сессии). Подключается только авторизованный пользователь. Если `HEAD /` отвечает `Server: QRATOR`, сайт открывает сокеты на `https://starvell.com:8443/<namespace>`, иначе на `https://starvell.com/<namespace>`. Без сессии пространства отвечают `connect_error {"message": "Unauthorized"}` (проверено 2026-10-03).

| Пространство | События |
|---|---|
| `/chats` | `message_created` (объект сообщения: `chatId`, `authorId`, `type`, `metadata`, `visibleTo`), `messages_deleted`, `chat_read`, `typing` |
| `/user-notifications` | `sale_update {delta}` (новые и изменённые продажи), `purchase_update`, `balance_updated`, `order_refunded_global`, `kyc_status_updated` |
| `/orders` | `order_updated {orderId, order}` после `order_subscribe {orderId}` |

Бот (`tg_bot_exfa/realtime.py`) держит `/chats` и `/user-notifications`: `message_created` будит проверку чатов, `sale_update` — проверку заказов. Сами уведомления по-прежнему строит опрос, поэтому курсоры, дедупликация и плагины работают одинаково. После первого события опрос замедляется до `REALTIME_POLL_INTERVAL`; при обрыве бот переподключается с паузами 5 с — 5 мин, при `Unauthorized` — раз в 5 минут.

## Не проверено

Ответы методов в документации не описаны; поля выше взяты из того, как их читает код сайта. Работа `sells.json?page=N`, наличие `createdAt`/`quantity` в `orders/list` и авторизация сокета с вашей сессией проверяются командой `python -m scripts.live_smoke` (проверки `orders`, `orders_list` и `realtime`).
