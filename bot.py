# bot.py (V6.0 - Több Napi Tuti Kezelése)

# ... (Az összes import és a start, button_handler, tippek, eredmenyek, stat függvények ugyanazok, mint a V5.0-ban) ...
# A változás csak a napi_tuti függvényben van.

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    
    # Lekérjük az összes olyan Napi Tutit, ami a tegnapi naptól készült
    # Ez biztosítja, hogy a holnapi, de már ma este generált szelvény is megjelenjen
    yesterday_start_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    
    response = supabase.table("napi_tuti").select("*").gte("created_at", str(yesterday_start_utc)).order('created_at', desc=True).execute()
        
    if not response.data:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek elérhető 'Napi Tuti' szelvények.")
        return
    
    full_message = []
    for i, szelveny in enumerate(response.data):
        # A szelvény nevéből vesszük a fejlécet
        header = f"🔥 *{szelveny['tipp_neve']}* 🔥"
        
        message_parts = [header]
        meccsek_res = supabase.table("meccsek").select("*").in_("id", szelveny.get('tipp_id_k', [])).execute()

        if not meccsek_res.data:
             continue # Ha egy szelvényhez nem találunk meccset, átugorjuk

        for tip in meccsek_res.data:
            local_time = datetime.fromisoformat(tip['kezdes']).astimezone(HUNGARY_TZ)
            time_str = local_time.strftime('%H:%M')
            tip_line = f"⚽️ *{tip.get('csapat_H')} vs {tip.get('csapat_V')}* `({time_str})`\n `•` {get_tip_details(tip['tipp'])}: *{tip['odds']:.2f}*"
            message_parts.append(tip_line)
        
        message_parts.append(f"🎯 *Eredő odds:* `{szelveny.get('eredo_odds', 0):.2f}`")
        
        # Az egyes szelvényeket elválasztóvonallal tagoljuk, ha több is van
        if i < len(response.data) - 1:
            message_parts.append("--------------------")

        full_message.extend(message_parts)

    await reply_obj.reply_text("\n\n".join(full_message), parse_mode='Markdown')

# ... (A többi függvény, start, stat, stb. változatlan)
