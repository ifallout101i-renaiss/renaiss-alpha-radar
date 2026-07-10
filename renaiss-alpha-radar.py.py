#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Product: Renaiss RWA Terminal - Smart Money & Whale Edition
Description: 全矩阵卡池监控 + 二级市场套利(Ask<FMV)监控 + 巨鲸扫货(Sold)追踪
"""

import asyncio
import json
import logging
import requests
import urllib3
import os
from openai import AsyncOpenAI
from datetime import datetime, UTC

# 🔥 穿透 VPN
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===================================================
# ⚙️ 【路演终极配置区】
# ===================================================
AI_API_KEY = "sk-D7VDMAjT3a09373cFee8T3BlbKFJ1aEbe12644fb4F6B826f" 
AI_BASE_URL = "https://api.ohmygpt.com/v1" 
MODEL_NAME = "gpt-4o-mini"

DISCORD_WEBHOOK_URL = "https://discordapp.com/api/webhooks/1525144505781588161/oCQnSQBGBxT8IERFTUdLw2lzFPb-OMKS1t2VUp9SDUrgZT5_oRXFlevs4qJH5cPYZ17W"

POLL_INTERVAL = 15 
CACHE_FILE = "market_cache.json"

SILENT_TIERS = ["C", "COMMON", "UNCOMMON", "BLOOM", "THORN"]
FOMO_TIERS = ["S", "TOP", "LEGENDARY", "CROWN"]
# ===================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')
aclient = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL, timeout=15.0)

class RenaissSniperBot:
    def __init__(self):
        self.market_data = {}
        self.current_listed_tokens = set() # 用来对比谁被买走了
        self.load_cache()

    def load_cache(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    self.market_data = json.load(f)
                logging.info(f"💾 [记忆库] 成功加载 {len(self.market_data)} 条历史数据。")
            except Exception:
                self.market_data = {}
        else:
            logging.info("💾 [记忆库] 未发现历史快照，准备建立全盘基准线。")

    def save_cache(self):
        try:
            clean_cache = dict(list(self.market_data.items())[-3000:])
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(clean_cache, f, ensure_ascii=False)
        except Exception:
            pass

    async def fetch_cli_data(self):
        """【上帝视角】4进程并发 + 纯净 JSON 解析"""
        hunted_all = []
        processes = {}
        try:
            processes['market'] = await asyncio.create_subprocess_shell("npx renaiss marketplace --listed --limit 50 --json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            processes['omega'] = await asyncio.create_subprocess_shell("npx renaiss packs omega --json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            processes['renacrypt'] = await asyncio.create_subprocess_shell("npx renaiss packs renacrypt-pack --json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            processes['eden'] = await asyncio.create_subprocess_shell("npx renaiss packs eden-pack --json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            
            try:
                out_m, _ = await asyncio.wait_for(processes['market'].communicate(), timeout=20.0)
                out_o, _ = await asyncio.wait_for(processes['omega'].communicate(), timeout=20.0)
                out_r, _ = await asyncio.wait_for(processes['renacrypt'].communicate(), timeout=20.0)
                out_e, _ = await asyncio.wait_for(processes['eden'].communicate(), timeout=20.0)
            except asyncio.TimeoutError:
                logging.warning("⚠️ CLI 接口超时，强行切断，保护回路...")
                for p in processes.values():
                    try: p.kill() 
                    except: pass
                return []
            
            # --- 解析轨道 1: 二级市场 (精准 JSON 提取) ---
            if out_m:
                try:
                    market_json = json.loads(out_m.decode('utf-8', errors='ignore'))
                    listed_items = market_json.get("collection", [])
                    new_listed_tokens = set()
                    
                    for item in listed_items:
                        token_id = str(item.get("tokenId", ""))
                        if not token_id: continue
                        new_listed_tokens.add(token_id)
                        
                        name = item.get("name", "Unknown Card")
                        company = item.get("gradingCompany", "Unknown")
                        
                        # 提取证书号
                        cert = token_id[:8] + "..."
                        for attr in item.get("attributes", []):
                            if attr.get("trait") == "Serial":
                                cert = attr.get("value")
                                break
                                
                        ask_raw = float(item.get("askPriceInUSDT", 0))
                        fmv_raw = float(item.get("fmvPriceInUSD", 0))
                        
                        # 精度转换
                        ask_usd = ask_raw / 1e18
                        fmv_usd = fmv_raw / 100.0
                        
                        hunted_all.append({
                            "cert": token_id,       # 唯一主键
                            "display_cert": cert,   # 证书号或折叠ID
                            "name": name,
                            "company": company,
                            "ask_price": ask_usd,
                            "fmv_price": fmv_usd,
                            "source": "Market_Listed"
                        })
                        
                    self.current_listed_tokens = new_listed_tokens
                except Exception as e:
                    pass
                
            # --- 解析轨道 2: 矩阵开包 ---
            pack_outputs = [(out_o, "OMEGA"), (out_r, "RenaCrypt"), (out_e, "Eden")]
            for stdout_p, pack_name in pack_outputs:
                if not stdout_p: continue
                try:
                    pack_json = json.loads(stdout_p.decode('utf-8', errors='ignore'))
                    recent_pulls = pack_json.get("cardPack", {}).get("recentOpenedPacks", [])
                    for pull in recent_pulls:
                        token_id = str(pull.get("collectibleTokenId", ""))
                        tier = str(pull.get("tier", "Unknown")).upper() 
                        fmv = float(pull.get("fmv", "0"))
                        fmv_usd = fmv / 100.0
                            
                        hunted_all.append({
                            "cert": token_id,
                            "display_cert": token_id,
                            "name": f"{pack_name} 卡包 [{tier}] 掉落",
                            "ask_price": fmv_usd, # 对于盲盒，标价等于FMV估值
                            "fmv_price": fmv_usd,
                            "company": "Renaiss Vault",
                            "source": "Pack",
                            "tier": tier,
                            "pack_name": pack_name
                        })
                except json.JSONDecodeError:
                    pass
            return hunted_all
        except Exception as e:
            logging.error(f"⚠️ 核心网络异常: {e}")
            return []

    async def generate_hype_text_with_retry(self, card_data: dict, max_retries=3) -> str:
        display_cert = card_data['display_cert']
        if len(display_cert) > 20: display_cert = f"{display_cert[:6]}...{display_cert[-4:]}"
        
        tier = card_data.get('tier', '')
        pack_name = card_data.get('pack_name', '')
        source = card_data.get('source', '')
        
        ask_usd = card_data.get('ask_price', 0)
        fmv_usd = card_data.get('fmv_price', 0)

        # 🔥 核心分析师 AI Prompt 矩阵
        if source == "Pack":
            trigger_event = f"一发源自链上 {pack_name} 机器的卡包盲盒掉落！"
            price_str = f"链上估值: ${fmv_usd:.2f}"
            if tier in FOMO_TIERS:
                scenario_guide = f"这是 {pack_name} 中开出的【{tier} 级】无敌史诗大奖！全网通报！文案强调绝世好运和极致暴富效应，让群友极度 FOMO！"
                tone_guide = "化身狂热分子，表现出极致的震惊，打破高冷人设！"
                emoji_guide = "多用抢眼 Emoji (🚨, 🤯, 🏆, 🔥, 💎)。"
            else:
                scenario_guide = f"这是一次源自 {pack_name} 的优质掉落（等级：{tier}）。侧重：盲盒解密、资产流入生态的潜力。"
                tone_guide = "保持专业、敏锐的 Alpha 社群情报感。"
                emoji_guide = "克制使用高级 Emoji (💎, 📊, 📦)。"
                
        elif source == "Market_Arbitrage":
            trigger_event = "二级市场上刚刚发生的【价格倒挂/套利】挂单异动！"
            price_str = f"挂单标价: ${ask_usd:.2f} (⚠️ 低于合理估值 FMV: ${fmv_usd:.2f})"
            scenario_guide = "这是一张刚上架、且售价低于其内在估值的低洼资产！存在利润空间！侧重于呼吁聪明钱（Smart Money）迅速介入抄底，抓住套利机会。"
            tone_guide = "极其敏锐、果断，充满华尔街量化交易员发现漏网之鱼的兴奋感。"
            emoji_guide = "使用提示机会的 Emoji (🚨, 📉, 💰, 🛒)。"
            
        elif source == "Market_Sold":
            trigger_event = "一张存在套利空间的优质资产刚刚在二级市场上【被成功扫货】！"
            price_str = f"成交估值: ${ask_usd:.2f} (原官方 FMV: ${fmv_usd:.2f})"
            scenario_guide = "这张资产刚刚被巨鲸/买家买走（从货架上消失）。侧重于：巨鲸出手、市场流动性极佳、FOMO 情绪验证，没上车的人拍大腿。"
            tone_guide = "热烈、笃定！向社区释放强烈的‘市场正在加速换手’的繁荣信号。"
            emoji_guide = "使用代表成交和资金的 Emoji (🤝, 🐋, 💥, 📈)。"

        prompt = f"""
        你现在是 Renaiss RWA 市场的首席链上数据播报员。
        请根据以下实时数据，写一段 80-100 字左右的 Web3 市场快报。
        
        【资产数据】
        - 触发事件: {trigger_event}
        - 资产标的: {card_data['name']}
        - 溯源节点: {card_data['company']} (Cert: {display_cert})
        - {price_str}
        
        【生成指引】
        1. 场景侧重: {scenario_guide}
        2. 语气定位: {tone_guide}
        3. 排版要求: {emoji_guide}
        """
        
        for attempt in range(max_retries):
            try:
                resp = await aclient.chat.completions.create(model=MODEL_NAME, messages=[{"role": "user", "content": prompt}], temperature=0.88)
                return resp.choices[0].message.content.strip()
            except Exception as e:
                await asyncio.sleep(2 ** attempt) 
        return f"🚨 异动发生！【{card_data['name']}】触发雷达监控！"

    async def push_to_discord(self, hype_text: str, card_data: dict, max_retries=5):
        display_cert = card_data['display_cert']
        if len(display_cert) > 20: display_cert = f"{display_cert[:6]}...{display_cert[-4:]}"
        
        source = card_data.get('source', '')
        tier = card_data.get('tier', '')
        pack_name = card_data.get('pack_name', 'OMEGA')

        if source == "Pack":
            if tier in FOMO_TIERS:
                title, color = f"🏆 【万中无一！{pack_name} {tier} 级神抽大奖】", 16711680 # 红
            else:
                title, color = f"🎰 【Renaiss {pack_name} 机优质掉落】", 16753920 # 橙
            val_str = f"**${card_data['fmv_price']:.2f}**"
        elif source == "Market_Arbitrage":
            title, color = "🚨 【二级市场抄底预警：发现价格洼地】", 65280 # 绿 (代表利润)
            val_str = f"**${card_data['ask_price']:.2f}** (FMV: ${card_data['fmv_price']:.2f})"
        elif source == "Market_Sold":
            title, color = "🤝 【巨鲸扫货！套利资产已被成交】", 16766720 # 金 (代表落袋为安)
            val_str = f"**${card_data['ask_price']:.2f}**"

        embed = {
            "title": title, "description": hype_text, "color": color, 
            "fields": [
                {"name": "🃏 锁定资产", "value": f"**{card_data['name']}**", "inline": False},
                {"name": "📜 链上节点", "value": f"**{card_data['company']}**\n`ID: {display_cert}`", "inline": True},
                {"name": "💰 标价/估值", "value": val_str, "inline": True}
            ],
            "footer": {"text": "📡 驱动内核：Renaiss Sniper Engine (Smart Whale Tracker)"},
            "timestamp": datetime.now(UTC).isoformat()
        }

        def sync_push():
            return requests.post(DISCORD_WEBHOOK_URL, json={"username": "Renaiss Sniper Bot", "embeds": [embed]}, timeout=10, verify=False)

        for attempt in range(max_retries):
            try:
                resp = await asyncio.to_thread(sync_push)
                if resp.status_code in [200, 204]:
                    logging.info(f"✅ [大屏轰炸] 资产战报已推送至 Discord！")
                    return
            except Exception:
                await asyncio.sleep(2 ** attempt)

    async def process_new_asset(self, card_data: dict):
        hype = await self.generate_hype_text_with_retry(card_data)
        if hype: await self.push_to_discord(hype, card_data)

    async def start_monitor(self):
        logging.info("🚀 [Renaiss Terminal] 巨鲸与套利监控引擎已全面点火！")
        
        while True:
            initial_items = await self.fetch_cli_data()
            if initial_items or len(self.market_data) > 0:
                for item in initial_items:
                    # 初始启动，所有扫描到的都标记为忽略，避免启动时满屏刷
                    item['status'] = 'ignored'
                    self.market_data[item['cert']] = item
                self.save_cache()
                logging.info(f"✅ 全生态基准线校验完毕。进入高维雷达巡航模式...")
                break
            else:
                await asyncio.sleep(5)

        while True:
            try:
                current_items = await self.fetch_cli_data()
                has_new = False
                
                # --- 1. 处理新增数据 (开包 & 挂单) ---
                for item in current_items:
                    cert = item['cert']
                    
                    if cert not in self.market_data:
                        # 发现新数据
                        if item['source'] == "Pack":
                            tier_tag = item.get('tier', '')
                            if tier_tag in SILENT_TIERS:
                                item['status'] = 'ignored'
                                logging.info(f"🚫 过滤低优开包: {item['name']} | 静默记入缓存。")
                            else:
                                item['status'] = 'notified'
                                logging.info(f"🎰 优质/神级掉落: {item['name']} | 准备播报！")
                                asyncio.create_task(self.process_new_asset(item))
                                
                        elif item['source'] == "Market_Listed":
                            ask_usd = item['ask_price']
                            fmv_usd = item['fmv_price']
                            
                            # 🔥 核心：套利发现逻辑 (挂单价 < 官方估值)
                            if ask_usd > 0 and ask_usd < fmv_usd:
                                item['source'] = "Market_Arbitrage"
                                item['status'] = 'watching' # 设为追踪状态，等巨鲸买走
                                logging.info(f"📉 发现套利洼地: {item['name']} | 标价 ${ask_usd} < FMV ${fmv_usd}")
                                asyncio.create_task(self.process_new_asset(item))
                            else:
                                item['status'] = 'ignored' # 价格偏高，不播报，只记缓存
                                logging.info(f"💤 挂单溢价或平价: {item['name']} | 已静默记入缓存。")

                        self.market_data[cert] = item
                        has_new = True

                # --- 2. 处理巨鲸扫货 (追踪中 -> 消失) ---
                for cert, data in list(self.market_data.items()):
                    if data.get('status') == 'watching':
                        # 如果这张套利卡，不在当前的列出名单里了 -> 说明被买走了！
                        if cert not in self.current_listed_tokens:
                            logging.info(f"🤝 巨鲸扫货确认: {data['name']} 已脱离交易板！")
                            data['source'] = "Market_Sold"
                            data['status'] = 'sold' # 避免重复播报
                            has_new = True
                            asyncio.create_task(self.process_new_asset(data))

                if has_new:
                    self.save_cache()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"🔌 核心回路异常保护: {e}")
                
            logging.info(f"💓 矩阵雷达 & 巨鲸探针 扫描完毕。等待 {POLL_INTERVAL} 秒...")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    print("=================================================================")
    print(" 👑 RENAISS RWA TERMINAL - SMART MONEY & WHALE TRACKER EDITION 👑")
    print("=================================================================")
    bot = RenaissSniperBot()
    try:
        asyncio.run(bot.start_monitor())
    except KeyboardInterrupt:
        print("\n🛑 矩阵雷达安全关机，核心数据已落盘保存。")