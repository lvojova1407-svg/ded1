"""
🤖 Telegram Bot для записи на перерывы
🚀 Версия с надежным авто-пингом для 24/7 работы на Render
"""
import os
import sys
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

# FastAPI и веб-сервер
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn

# Telegram
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# HTTP клиент для авто-пинга
import aiohttp
from aiohttp import ClientTimeout, ClientError

# --- Конфигурация ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")

# URL вашего сервиса на Render
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://ded1-8.onrender.com")
PORT = int(os.getenv("PORT", 10000))

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- FastAPI приложение ---
app = FastAPI(
    title="Telegram Bot Server",
    description="Сервер для Telegram бота записи на перерывы",
    version="2.0"
)

# Глобальные переменные
bot_app: Optional[Application] = None
auto_ping_task: Optional[asyncio.Task] = None
health_check_counter = 0
startup_time = datetime.now(timezone.utc)

# --- КЛАСС АВТО-ПИНГА ---
class RenderAutoPinger:
    """Надежный авто-пинг для Render с резервными стратегиями"""
    
    def __init__(self):
        self.is_running = False
        self.ping_count = 0
        self.last_success = None
        self.last_error = None
        self.consecutive_failures = 0
        self.max_failures = 3
        
        # Список URL для пинга (основной + резервные)
        self.ping_urls = [
            f"{RENDER_URL}/",           # Основной
            f"{RENDER_URL}/health",     # Health check
            f"{RENDER_URL}/docs",       # Документация
            "https://httpbin.org/get",  # Резервный внешний (для проверки сети)
        ]
        
        # Интервалы (в секундах)
        self.normal_interval = 8 * 60   # 8 минут (меньше 15-минутного лимита Render)
        self.error_interval = 2 * 60    # 2 минуты при ошибках
        self.initial_delay = 15         # Задержка перед первым пингом
        
        logger.info(f"🎯 Авто-пинг инициализирован для: {RENDER_URL}")
    
    async def _ping_single_url(self, session: aiohttp.ClientSession, url: str) -> bool:
        """Пинг одного URL"""
        try:
            start = datetime.now()
            timeout = ClientTimeout(total=15, connect=5)
            
            async with session.get(url, timeout=timeout, ssl=False) as response:
                elapsed = (datetime.now() - start).total_seconds()
                
                if response.status == 200:
                    logger.info(f"✅ Пинг {url} успешен: {response.status} ({elapsed:.2f}с)")
                    return True
                else:
                    logger.warning(f"⚠️ Пинг {url}: код {response.status}")
                    return False
                    
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут при пинге {url}")
            return False
        except ClientError as e:
            logger.warning(f"🌐 Сетевая ошибка при пинге {url}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка при пинге {url}: {e}")
            return False
    
    async def execute_ping(self) -> bool:
        """Выполнить серию пингов"""
        self.ping_count += 1
        logger.info(f"🔄 Выполняю пинг #{self.ping_count}...")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Пробуем все URL по порядку
                for url in self.ping_urls:
                    if await self._ping_single_url(session, url):
                        self.consecutive_failures = 0
                        self.last_success = datetime.now()
                        
                        # Если это резервный URL, всё равно считаем успехом
                        if "httpbin.org" in url:
                            logger.info("📡 Использован резервный URL, но сеть работает")
                        
                        return True
                
                # Все URL провалились
                self.consecutive_failures += 1
                self.last_error = datetime.now()
                
                if self.consecutive_failures >= self.max_failures:
                    logger.error(f"🚨 Критический сбой! {self.consecutive_failures} неудачных пингов подряд")
                    # Здесь можно добавить уведомление в Telegram
                
                return False
                
        except Exception as e:
            logger.error(f"💥 Критическая ошибка в execute_ping: {e}")
            self.consecutive_failures += 1
            return False
    
    async def start(self):
        """Запустить авто-пинг"""
        if self.is_running:
            return
        
        self.is_running = True
        logger.info("🚀 Запуск авто-пинга...")
        
        # Ждем полного запуска сервера
        await asyncio.sleep(self.initial_delay)
        
        # Первый пинг
        await self.execute_ping()
        
        # Запускаем бесконечный цикл
        asyncio.create_task(self._ping_loop())
    
    async def _ping_loop(self):
        """Основной цикл пинга"""
        while self.is_running:
            try:
                # Выбираем интервал в зависимости от успешности
                if self.consecutive_failures > 0:
                    interval = self.error_interval
                    logger.info(f"⚡ Режим восстановления: следующий пинг через {interval/60:.1f} мин")
                else:
                    interval = self.normal_interval
                
                await asyncio.sleep(interval)
                
                if self.is_running:
                    success = await self.execute_ping()
                    
                    # Статистика
                    if self.ping_count % 10 == 0:  # Каждые 10 пингов
                        uptime = datetime.now(timezone.utc) - startup_time
                        success_rate = (self.ping_count - self.consecutive_failures) / self.ping_count * 100
                        logger.info(f"📊 Статистика: {self.ping_count} пингов, "
                                  f"аптайм {uptime}, успешность {success_rate:.1f}%")
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"💥 Ошибка в ping_loop: {e}")
                await asyncio.sleep(60)
    
    async def stop(self):
        """Остановить авто-пинг"""
        self.is_running = False
        logger.info("🛑 Авто-пинг остановлен")
    
    def get_status(self) -> dict:
        """Получить статус авто-пинга"""
        return {
            "running": self.is_running,
            "ping_count": self.ping_count,
            "consecutive_failures": self.consecutive_failures,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_error": self.last_error.isoformat() if self.last_error else None,
            "render_url": RENDER_URL,
            "normal_interval_minutes": self.normal_interval / 60,
            "uptime": str(datetime.now(timezone.utc) - startup_time)
        }


# Инициализация авто-пинга
auto_pinger = RenderAutoPinger()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_moscow_time() -> str:
    """Получить текущее время по Москве"""
    moscow_tz = timezone(timedelta(hours=3))
    return datetime.now(moscow_tz).strftime("%H:%M")

async def start_bot():
    """Инициализация и запуск Telegram бота"""
    global bot_app
    
    logger.info("🤖 Инициализация Telegram бота...")
    
    # Создаем приложение
    bot_app = Application.builder().token(TOKEN).build()
    
    # Здесь добавьте ваши обработчики
    # bot_app.add_handler(CommandHandler("start", start_command))
    # bot_app.add_handler(CallbackQueryHandler(button_callback))
    
    # Запускаем бота
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    
    logger.info("✅ Telegram бот запущен и готов к работе!")
    return True

async def stop_bot():
    """Остановка Telegram бота"""
    global bot_app
    
    if bot_app:
        logger.info("🛑 Остановка Telegram бота...")
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        logger.info("✅ Telegram бот остановлен")

# --- FASTAPI ЭНДПОИНТЫ ---
@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "message": "🚀 Telegram Bot Server 24/7",
        "status": "running",
        "bot": "active" if bot_app else "inactive",
        "auto_ping": auto_pinger.get_status(),
        "time_moscow": get_moscow_time(),
        "uptime": str(datetime.now(timezone.utc) - startup_time),
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "status": "/status",
            "ping_info": "/ping-info"
        }
    }

@app.get("/health")
async def health_check():
    """Health check для Render и мониторинга"""
    global health_check_counter
    health_check_counter += 1
    
    status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bot_running": bool(bot_app),
        "auto_ping_running": auto_pinger.is_running,
        "health_checks": health_check_counter,
        "moscow_time": get_moscow_time(),
        "version": "2.0"
    }
    
    # Проверяем критические компоненты
    if not bot_app:
        status["status"] = "degraded"
        status["issues"] = ["bot_not_initialized"]
    
    return JSONResponse(content=status)

@app.get("/status")
async def detailed_status():
    """Детальный статус системы"""
    return {
        "server": {
            "startup_time": startup_time.isoformat(),
            "uptime": str(datetime.now(timezone.utc) - startup_time),
            "port": PORT,
            "render_url": RENDER_URL
        },
        "bot": {
            "initialized": bool(bot_app),
            "token_set": bool(TOKEN)
        },
        "auto_ping": auto_pinger.get_status(),
        "system": {
            "python_version": sys.version,
            "time_moscow": get_moscow_time(),
            "health_checks": health_check_counter
        }
    }

@app.get("/ping-info")
async def ping_info():
    """Информация о системе авто-пинга"""
    return auto_pinger.get_status()

@app.get("/force-ping")
async def force_ping():
    """Принудительный пинг (для тестирования)"""
    success = await auto_pinger.execute_ping()
    return {
        "forced_ping": True,
        "success": success,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# --- ОБРАБОТЧИКИ СОБЫТИЙ ---
@app.on_event("startup")
async def startup_event():
    """Запуск при старте приложения"""
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК СЕРВЕРА 24/7")
    logger.info("=" * 50)
    logger.info(f"✅ Токен бота: {'Найден' if TOKEN else 'Не найден'}")
    logger.info(f"🌐 Внешний URL: {RENDER_URL}")
    logger.info(f"⏰ Время по Москве: {get_moscow_time()}")
    logger.info("=" * 50)
    
    # Запускаем авто-пинг ПЕРВЫМ делом
    await auto_pinger.start()
    
    # Затем запускаем бота
    await start_bot()
    
    logger.info("🎉 Все системы запущены и готовы к работе!")
    logger.info(f"📡 Сервер доступен по адресу: {RENDER_URL}")

@app.on_event("shutdown")
async def shutdown_event():
    """Остановка при завершении"""
    logger.info("🛑 Завершение работы сервера...")
    
    # Останавливаем авто-пинг
    await auto_pinger.stop()
    
    # Останавливаем бота
    await stop_bot()
    
    logger.info("👋 Сервер остановлен")

# --- ОСНОВНАЯ ФУНКЦИЯ ---
def main():
    """Точка входа"""
    # Настраиваем обработку сигналов для корректного завершения
    import signal
    
    def handle_signal(signum, frame):
        logger.info(f"📞 Получен сигнал {signum}, завершаем работу...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    # Запускаем сервер
    logger.info(f"🌍 Запуск Uvicorn на порту {PORT}...")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        access_log=True,
        log_level="info",
        timeout_keep_alive=30
    )

if __name__ == "__main__":
    main()
