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
        b.button(text=t("btn_info"), callback_data="menu:info")
        b.button(text=t("btn_language"), callback_data="menu:lang")
        b.button(text=t("btn_notifications"), callback_data="menu:notifications")
        b.button(text=t("btn_welcome"), callback_data="menu:welcome")
        b.button(text=t("btn_templates"), callback_data="menu:templates")
        b.button(text=t("btn_autodelivery"), callback_data="menu:ad")
        b.button(text=t("btn_settings"), callback_data="menu:settings")
        b.button(text=t("btn_prefix"), callback_data="menu:prefix")
        b.button(text=t("btn_plugins"), callback_data="menu:plugins")
        b.button(text=t("btn_stats"), callback_data="menu:stats")
        b.adjust(2, 2, 2, 2, 2)
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

    def prefix_menu(self, t, enabled: bool) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_prefix_disable") if enabled else t("btn_prefix_enable"), callback_data="prefix:toggle")
        b.button(text=t("btn_prefix_change"), callback_data="prefix:change")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(1, 1, 1)
        return b

    def ad_menu(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_ad_list"), callback_data="ad:list")
        b.button(text=t("btn_ad_add"), callback_data="ad:add")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(2, 1)
        return b

    def ad_add(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_cancel"), callback_data="ad:cancel")
        return b

    def ad_list(self, t, items: list[tuple[str, str]]) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        for item_id, label in items:
            b.button(text=label, callback_data=f"ad:item:{item_id}")
        b.button(text=t("btn_back"), callback_data="menu:ad")
        b.adjust(1)
        return b

    def ad_item(self, t, item_id: str) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_ad_add"), callback_data=f"ad:item_add:{item_id}")
        b.button(text=t("btn_ad_delete"), callback_data=f"ad:del_confirm:{item_id}")
        b.button(text=t("btn_back"), callback_data="ad:list")
        b.adjust(1, 2)
        return b

    def ad_delete_confirm(self, t, item_id: str) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_yes"), callback_data=f"ad:del_yes:{item_id}")
        b.button(text=t("btn_no"), callback_data=f"ad:item:{item_id}")
        b.adjust(2)
        return b

    def ad_add_to_item(self, t, item_id: str) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_cancel"), callback_data=f"ad:item:{item_id}")
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
        b.button(text=t("btn_change_token"), callback_data="settings:change_token")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(1, 2, 1)
        return b

    def welcome_menu(self, t, enabled: bool) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(
            text=t("btn_welcome_toggle_off") if enabled else t("btn_welcome_toggle_on"),
            callback_data="welcome:toggle",
        )
        b.button(text=t("btn_welcome_change_text"), callback_data="welcome:change_text")
        b.button(text=t("btn_welcome_change_cooldown"), callback_data="welcome:change_cooldown")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(1, 1, 1, 1)
        return b

    def cancel(self, t) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_cancel"), callback_data="settings:cancel")
        return b

    def cancel_custom(self, t, callback_data: str) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        b.button(text=t("btn_cancel"), callback_data=callback_data)
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
        b.button(text=t("btn_plugins_add"), callback_data="plugins:add")
        b.button(text=t("btn_plugins_list"), callback_data="plugins:list")
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(2, 1)
        return b

    def info_links(self, t, author_url: str | None, channel_url: str | None, chat_url: str | None) -> InlineKeyboardBuilder:
        b = InlineKeyboardBuilder()
        if author_url:
            b.button(text=t("btn_author"), url=author_url)
        if channel_url:
            b.button(text=t("btn_channel"), url=channel_url)
        if chat_url:
            b.button(text=t("btn_chat"), url=chat_url)
        b.button(text=t("btn_back"), callback_data="back:main")
        b.adjust(2, 2)
        return b


