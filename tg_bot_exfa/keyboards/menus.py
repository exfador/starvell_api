from aiogram.utils.keyboard import InlineKeyboardBuilder


class Keyboards:
    def language(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("lang_ru"), callback_data="lang:ru")
        b.button(text=t("lang_en"), callback_data="lang:en")
        b.adjust(2)
        return b

    def main_menu(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_language"), callback_data="menu:lang")
        b.button(text=t("btn_notifications"), callback_data="menu:notifications")
        b.button(text=t("btn_templates"), callback_data="menu:templates")
        b.button(text=t("btn_settings"), callback_data="menu:settings")
        b.button(text=t("btn_plugins"), callback_data="menu:plugins")
        b.button(text=t("btn_stats"), callback_data="menu:stats")
        b.adjust(1, 2, 3)
        return b

    def notifications(
        self,
        t,
        auth_on: bool,
        bump_on: bool,
        chat_on: bool,
        orders_on: bool,
        auth_btn_text: str | None = None,
        bump_btn_text: str | None = None,
        chat_btn_text: str | None = None,
        orders_btn_text: str | None = None,
    ) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=(auth_btn_text or t("btn_toggle_auth")), callback_data="notif:toggle:auth")
        b.button(text=(bump_btn_text or t("btn_toggle_bump")), callback_data="notif:toggle:bump")
        chat_text = chat_btn_text or t("btn_turn_chat_off" if chat_on else "btn_turn_chat_on")
        b.button(text=chat_text, callback_data="notif:toggle:chat")
        orders_text = orders_btn_text or t("btn_turn_orders_off" if orders_on else "btn_turn_orders_on")
        b.button(text=orders_text, callback_data="notif:toggle:orders")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(2, 2, 1)
        return b

    def language_with_back(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("lang_ru"), callback_data="lang:ru")
        b.button(text=t("lang_en"), callback_data="lang:en")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(2, 1)
        return b

    def settings_menu(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_change_session"), callback_data="settings:change_session")
        b.button(text=t("btn_change_password"), callback_data="settings:change_password")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(1, 2)
        return b

    def cancel(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_cancel"), callback_data="settings:cancel")
        return b

    def templates_menu(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_templates_add"), callback_data="templates:add")
        b.button(text=t("btn_templates_list"), callback_data="templates:list:1")
        b.button(text=t("btn_templates_delete"), callback_data="templates:delete:1")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(1, 2, 1)
        return b

    def templates_cancel(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_cancel"), callback_data="templates:cancel")
        return b

    def chat_notification(self, t, chat_id: str, url: str):
        b = InlineKeyboardBuilder()
        if url:
            b.button(text=t("btn_open_link"), url=url)
        b.button(text=t("btn_send_message"), callback_data=f"chat:reply:{chat_id}")
        b.button(text=t("btn_templates_open"), callback_data=f"chat:templates:{chat_id}")
        if url:
            b.adjust(1, 2)
        else:
            b.adjust(2)
        return b

    def chat_reply_cancel(self, t, chat_id: str):
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_cancel"), callback_data=f"chat:reply_cancel:{chat_id}")
        return b

    def order_notification(self, t, order_id: str, url: str):
        b = InlineKeyboardBuilder()
        if url:
            b.button(text=t("btn_order_open"), url=url)
        b.button(text=t("btn_order_refund"), callback_data=f"order:refund:{order_id}")
        if url:
            b.adjust(1, 1)
        else:
            b.adjust(1)
        return b

    def order_refund_confirm(self, t, order_id: str):
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_yes"), callback_data=f"order:refund_yes:{order_id}")
        b.button(text=t("btn_no"), callback_data=f"order:refund_no:{order_id}")
        b.adjust(2)
        return b

    def order_notification_view(self, t, order_id: str, url: str):
        b = InlineKeyboardBuilder()
        if url:
            b.button(text=t("btn_order_open"), url=url)
            b.adjust(1)
        return b

    def plugins_menu(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_order_plugin"), url="https://t.me/exfador")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(1, 1)
        return b


